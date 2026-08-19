"""One WebSocket owning many bot lives.

Default lobby path: parse `state`/`food` once, `decide()` per live id, tag
`input`/`split` with `"id"`. `--sockets` in `bots.simple_bot` restores one
socket per life.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from dataclasses import dataclass

from aiohttp import ClientSession, WSMsgType

from bots.brain import FoodIndex, Memory, Personality, decide, new_memory
from bots.simple_bot import (
    RECONNECT_BASE_SECONDS,
    RECONNECT_MAX_SECONDS,
    RESPAWN_SECONDS,
    _centroid_of,
    _prev_positions,
    personality_for_slot,
)


@dataclass
class Slot:
    name: str
    color: str
    personality: Personality
    seed: int
    player_id: str | None = None
    memory: Memory | None = None
    last_split: bool = False

    def reset_life(self) -> None:
        self.memory = new_memory(self.seed)
        self.last_split = False


class FleetClient:
    def __init__(
        self,
        *,
        url: str,
        names: list[str],
        colors: list[str],
        food_index: FoodIndex,
        http: ClientSession,
        stop: asyncio.Event,
    ) -> None:
        self.url = url
        self.food_index = food_index
        self.http = http
        self.stop = stop
        self.slots = [
            Slot(
                name=name,
                color=color,
                personality=personality_for_slot(index),
                seed=index + 1,
            )
            for index, (name, color) in enumerate(zip(names, colors))
        ]
        self.prev_state: dict | None = None
        self.world_width = 0.0
        self.world_height = 0.0
        self.tick_rate = 30.0
        self.initial_player_mass = 50.0
        self._ws = None
        self._pending_welcome: deque[Slot] = deque()
        self._respawn_tasks: set[asyncio.Task] = set()

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

    def _slot_by_id(self, player_id: str | None) -> Slot | None:
        if not player_id:
            return None
        for slot in self.slots:
            if slot.player_id == player_id:
                return slot
        return None

    def _decide_slot(
        self, slot: Slot, state: dict
    ) -> tuple[float, float, bool] | None:
        if slot.player_id is None:
            return None
        me = next(
            (
                player
                for player in state.get("players") or []
                if player.get("id") == slot.player_id
            ),
            None,
        )
        if me is None or not me.get("pieces"):
            return None
        if slot.memory is None:
            slot.reset_life()
        prev_me = None
        if self.prev_state:
            prev_me = next(
                (
                    player
                    for player in self.prev_state.get("players") or []
                    if player.get("id") == slot.player_id
                ),
                None,
            )
        view = {
            "self_id": slot.player_id,
            "tick_rate": self.tick_rate,
            "world_width": self.world_width,
            "world_height": self.world_height,
            "initial_player_mass": self.initial_player_mass,
            "players": state.get("players") or [],
            "prev_positions": _prev_positions(self.prev_state),
            "prev_centroid": _centroid_of(prev_me),
            "food_index": self.food_index,
            "personality": slot.personality,
        }
        return decide(view, slot.memory)

    def commands_for_state(self, state: dict) -> list[dict]:
        """`input`/`split` payloads for every live slot. Used by tests and `_play`."""
        commands: list[dict] = []
        for slot in self.slots:
            result = self._decide_slot(slot, state)
            if result is None:
                continue
            dx, dy, want_split = result
            commands.append(
                {
                    "type": "input",
                    "dx": dx,
                    "dy": dy,
                    "id": slot.player_id,
                }
            )
            if want_split and not slot.last_split:
                commands.append({"type": "split", "id": slot.player_id})
            slot.last_split = want_split
        self.prev_state = state
        return commands

    async def _join_slot(self, slot: Slot) -> None:
        slot.player_id = None
        slot.reset_life()
        self._pending_welcome.append(slot)
        if self._ws is None:
            return
        await self._ws.send_json(
            {
                "type": "join",
                "name": slot.name,
                "color": slot.color,
                "bot": True,
            }
        )

    def _schedule_respawn(self, slot: Slot) -> None:
        task = asyncio.create_task(self._respawn_later(slot))
        self._respawn_tasks.add(task)
        task.add_done_callback(self._respawn_tasks.discard)

    async def _respawn_later(self, slot: Slot) -> None:
        try:
            await asyncio.wait_for(self.stop.wait(), timeout=RESPAWN_SECONDS)
            return
        except asyncio.TimeoutError:
            pass
        if self.stop.is_set() or self._ws is None or self._ws.closed:
            return
        await self._join_slot(slot)

    async def _cancel_respawns(self) -> None:
        tasks = list(self._respawn_tasks)
        self._respawn_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _on_welcome(self, data: dict) -> None:
        if not self._pending_welcome:
            return
        slot = self._pending_welcome.popleft()
        slot.player_id = data.get("id")
        self._apply_config(data)

    def _on_game_over(self, data: dict) -> None:
        slot = self._slot_by_id(data.get("id"))
        if slot is None:
            live = [item for item in self.slots if item.player_id]
            if data.get("id") is None and len(live) == 1:
                slot = live[0]
            else:
                return
        slot.player_id = None
        slot.memory = None
        slot.last_split = False
        self._schedule_respawn(slot)

    async def _play(self, ws) -> str:
        self._ws = ws
        self._pending_welcome.clear()
        self.prev_state = None
        for slot in self.slots:
            await self._join_slot(slot)
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
                self._on_welcome(data)
                continue
            if kind == "food":
                self._on_food(data)
                continue
            if kind == "game_over":
                self._on_game_over(data)
                continue
            if kind != "state":
                continue
            self._apply_config(data)
            for command in self.commands_for_state(data):
                await ws.send_json(command)
        return "gone"

    async def run(self) -> None:
        attempt = 0
        while not self.stop.is_set():
            try:
                async with self.http.ws_connect(self.url, heartbeat=20.0) as ws:
                    attempt = 0
                    try:
                        await self._play(ws)
                    finally:
                        self._ws = None
                        await self._cancel_respawns()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            if self.stop.is_set():
                return
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


async def run_fleet(
    url: str,
    names: list[str],
    colors: list[str],
    stop: asyncio.Event | None = None,
) -> None:
    if stop is None:
        stop = asyncio.Event()
    food_index = FoodIndex()
    async with ClientSession() as http:
        client = FleetClient(
            url=url,
            names=names,
            colors=colors,
            food_index=food_index,
            http=http,
            stop=stop,
        )
        try:
            await client.run()
        finally:
            stop.set()
            await client._cancel_respawns()
