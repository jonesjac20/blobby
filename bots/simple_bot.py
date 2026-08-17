"""Ordinary WebSocket bot clients. Plumbing only — the brain is bots.brain.

    python -m bots.simple_bot --url http://127.0.0.1:8000/ws --name bot --count 5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import signal
import sys

from aiohttp import ClientSession, WSMsgType

from bots.brain import (
    PERSONALITIES,
    FoodIndex,
    Memory,
    Personality,
    decide,
    new_memory,
)
from server.config import HOST, NAME_MAX_LEN, PORT

RECONNECT_BASE_SECONDS = 0.5
RECONNECT_MAX_SECONDS = 8.0
RESPAWN_SECONDS = 3.0


def _default_url() -> str:
    host = "127.0.0.1" if HOST in {"0.0.0.0", "::"} else HOST
    return f"http://{host}:{PORT}/ws"


def distinct_names(base: str, count: int) -> list[str]:
    """`bot`, `bot2`, `bot3`… truncated to NAME_MAX_LEN."""
    base = (base or "bot")[:NAME_MAX_LEN]
    names: list[str] = []
    for index in range(count):
        if index == 0:
            names.append(base)
            continue
        suffix = str(index + 1)
        names.append(f"{base[: max(NAME_MAX_LEN - len(suffix), 0)]}{suffix}")
    return names


def random_color(rng: random.Random) -> str:
    return f"#{rng.randint(0, 0xFFFFFF):06x}"


def assign_colors(
    count: int, pinned: str | None, rng: random.Random
) -> list[str]:
    if pinned:
        if count == 1:
            return [pinned]
        # A table of N should not be a clone army unless the user passed one
        # client. Pin only the first slot; the rest stay random.
        return [pinned] + [random_color(rng) for _ in range(count - 1)]
    return [random_color(rng) for _ in range(count)]


def personality_for_slot(index: int) -> Personality:
    return PERSONALITIES[index % len(PERSONALITIES)]


def _prev_positions(state: dict | None) -> dict[str, tuple[float, float]]:
    if not state:
        return {}
    positions: dict[str, tuple[float, float]] = {}
    for player in state.get("players") or []:
        for piece in player.get("pieces") or []:
            positions[piece["piece_id"]] = (piece["x"], piece["y"])
    return positions


def _centroid_of(player: dict | None) -> tuple[float, float] | None:
    if not player or not player.get("pieces"):
        return None
    pieces = player["pieces"]
    total = sum(piece["mass"] for piece in pieces)
    if total <= 0:
        return pieces[0]["x"], pieces[0]["y"]
    x = sum(piece["x"] * piece["mass"] for piece in pieces) / total
    y = sum(piece["y"] * piece["mass"] for piece in pieces) / total
    return x, y


class BotClient:
    def __init__(
        self,
        *,
        url: str,
        name: str,
        color: str,
        personality: Personality,
        food_index: FoodIndex,
        seed: int,
        http: ClientSession,
        stop: asyncio.Event,
    ) -> None:
        self.url = url
        self.name = name
        self.color = color
        self.personality = personality
        self.food_index = food_index
        self.seed = seed
        self.http = http
        self.stop = stop
        self.self_id: str | None = None
        self.memory: Memory | None = None
        self.prev_state: dict | None = None
        self.last_split = False
        self.world_width = 0.0
        self.world_height = 0.0
        self.tick_rate = 30.0
        self.initial_player_mass = 50.0

    def _reset_life(self) -> None:
        self.memory = new_memory(self.seed)
        self.prev_state = None
        self.last_split = False

    # Update config for live world state
    def _apply_config(self, msg: dict) -> None:
        world = msg.get("world") or {}
        if isinstance(world.get("width"), (int, float)):
            self.world_width = float(world["width"])
        if isinstance(world.get("height"), (int, float)):
            self.world_height = float(world["height"])
        if isinstance(msg.get("tickRate"), (int, float)) and msg["tickRate"] > 0:
            self.tick_rate = float(msg["tickRate"])
        if isinstance(msg.get("initialPlayerMass"), (int, float)):
            self.initial_player_mass = float(msg["initialPlayerMass"])

    def _on_food(self, msg: dict) -> None:
        pellets = msg.get("food") or []
        version = msg.get("version")
        self.food_index.update(version, pellets)

    def _decide_and_send(self, ws, state: dict) -> None:
        if self.self_id is None:
            return
        me = next(
            (player for player in state.get("players") or [] if player.get("id") == self.self_id),
            None,
        )
        if me is None:
            return
        if not me.get("pieces"):
            return
        if self.memory is None:
            self._reset_life()
        prev_me = None
        if self.prev_state:
            prev_me = next(
                (
                    player
                    for player in self.prev_state.get("players") or []
                    if player.get("id") == self.self_id
                ),
                None,
            )
        view = {
            "self_id": self.self_id,
            "tick_rate": self.tick_rate,
            "world_width": self.world_width,
            "world_height": self.world_height,
            "initial_player_mass": self.initial_player_mass,
            "players": state.get("players") or [],
            "prev_positions": _prev_positions(self.prev_state),
            "prev_centroid": _centroid_of(prev_me),
            "food_index": self.food_index,
            "personality": self.personality,
        }
        dx, dy, want_split = decide(view, self.memory)
        self.prev_state = state
        return dx, dy, want_split

    async def _join(self, ws) -> None:
        self.self_id = None
        self._reset_life()
        await ws.send_json({"type": "join", "name": self.name, "color": self.color})

    async def _play(self, ws) -> str:
        """Run until disconnect. Returns 'gone'."""
        await self._join(ws)
        async for msg in ws:
            if self.stop.is_set():
                return "gone"
            if msg.type != WSMsgType.TEXT:
                if msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED):
                    return "gone"
                continue
            try:
                data = json.loads(msg.data)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            kind = data.get("type")
            if kind == "welcome":
                self.self_id = data.get("id")
                self._apply_config(data)
                continue
            # Update food index (food has changed since last tick)
            if kind == "food":
                self._on_food(data)
                continue
            # Respawn the bot on game over
            if kind == "game_over":
                self.self_id = None
                self.memory = None
                self.prev_state = None
                self.last_split = False
                try:
                    await asyncio.wait_for(self.stop.wait(), timeout=RESPAWN_SECONDS)
                    return "gone"
                except asyncio.TimeoutError:
                    await self._join(ws)
                    continue
            
            # Update config for live world state
            if kind != "state":
                continue
            self._apply_config(data)
            if self.self_id is None:
                continue
            
            # Decide and send bot input to the server
            result = self._decide_and_send(ws, data)
            if result is None:
                continue
            dx, dy, want_split = result
            await ws.send_json({"type": "input", "dx": dx, "dy": dy})
            
            # Send split command if the bot wants to split
            if want_split and not self.last_split:
                await ws.send_json({"type": "split"})
            self.last_split = want_split
        return "gone"

    async def run(self) -> None:
        attempt = 0
        while not self.stop.is_set():
            try:
                # Connect to the server
                async with self.http.ws_connect(self.url, heartbeat=20.0) as ws:
                    attempt = 0
                    await self._play(ws)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            if self.stop.is_set():
                return

            # Reconnect to the server
            attempt += 1
            delay = min(
                RECONNECT_BASE_SECONDS * (2 ** (attempt - 1)),
                RECONNECT_MAX_SECONDS,
            )
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=delay)
                return
            except asyncio.TimeoutError:
                continue


async def run_bots(
    url: str,
    names: list[str],
    colors: list[str],
    stop: asyncio.Event | None = None,
) -> None:
    if stop is None:
        stop = asyncio.Event()
    food_index = FoodIndex()
    async with ClientSession() as http:
        # Create tasks representing each bot (i.e., a "task" is a bot whose actions must be executed)
        tasks = [
            asyncio.create_task(
                BotClient(
                    url=url,
                    name=name,
                    color=color,
                    personality=personality_for_slot(index),
                    food_index=food_index,
                    seed=index + 1,
                    http=http,
                    stop=stop,
                ).run()
            )
            for index, (name, color) in enumerate(zip(names, colors))
        ]
        # Run all tasks concurrently
        try:
            await asyncio.gather(*tasks)
        finally:
            stop.set()
            # End all running tasks
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=_default_url())
    parser.add_argument("--name", default="bot")
    parser.add_argument("--color", default=None, help="Pin the first client; others stay random.")
    parser.add_argument("--count", type=int, default=1)
    args = parser.parse_args(argv)
    if args.count < 1:
        parser.error("--count must be >= 1")
    rng = random.Random()
    names = distinct_names(args.name, args.count)
    colors = assign_colors(args.count, args.color, rng)
    stop = asyncio.Event()

    def _request_stop(*_args) -> None:
        stop.set()

    try:
        # Create a new event loop and set it as the current event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # Add signal handlers for SIGINT and SIGTERM
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _request_stop)
            except NotImplementedError:
                # Windows: signal handlers on the loop are limited.
                signal.signal(sig, lambda *_: _request_stop())
        # Run the bots
        loop.run_until_complete(run_bots(args.url, names, colors, stop))
    except KeyboardInterrupt:
        stop.set()
    finally:
        # Close the event loop
        loop.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
