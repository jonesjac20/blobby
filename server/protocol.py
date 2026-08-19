"""WebSocket JSON protocol: parse, serialize, sessions, join/death.

Wire messages are additive to source plan section 4. See GUIDEBOOK Divergence.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from server import simulation
from server.config import (
    BASE_SPEED,
    DEFAULT_COLOR,
    DEFAULT_NAME,
    INITIAL_PLAYER_MASS,
    NAME_MAX_LEN,
    SPEED_FALLOFF,
    SPEED_FLOOR_FRACTION,
    TICK_RATE,
    WORLD_HEIGHT,
    WORLD_WIDTH,
)
from server.models import Player
from server.world import World

_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
JSON_SEPARATORS = (",", ":")


def encode_json(payload: dict) -> str:
    return json.dumps(payload, separators=JSON_SEPARATORS)


@dataclass
class ClientSession:
    """Per-socket state. Survives death; the Player does not.

    A human socket owns at most one player. A bot fleet (`bot: true` joins)
    may own many; `player_id` is then any remaining owned id, for call sites
    that still speak a single life.
    """

    ws: Any = None
    player_ids: set[str] = field(default_factory=set)
    pending_welcome: set[str] = field(default_factory=set)
    peak_masses: dict[str, float] = field(default_factory=dict)
    spawn_sim_times: dict[str, float] = field(default_factory=dict)
    name: str = ""
    color: str = DEFAULT_COLOR
    # Set when this socket has joined with `"bot": true`. Extra joins are
    # allowed; a human socket still ignores a second join while alive.
    allows_multi: bool = False
    # Version of the food field this socket has successfully received. 0 matches
    # FoodStream's initial version, which is the empty field — so a world with
    # no food never sends a food message, and a late joiner (still at 0) gets
    # the current field on the next emit.
    food_version: int = 0

    @property
    def player_id(self) -> str | None:
        if not self.player_ids:
            return None
        return next(iter(self.player_ids))

    @player_id.setter
    def player_id(self, value: str | None) -> None:
        if value is None:
            self.player_ids.clear()
            self.pending_welcome.clear()
            self.peak_masses.clear()
            self.spawn_sim_times.clear()
            return
        if value not in self.player_ids:
            self.player_ids = {value}

    def owns(self, player_id: str) -> bool:
        return player_id in self.player_ids

    def mark_welcome_sent(self, player_id: str) -> None:
        self.pending_welcome.discard(player_id)

    def release_player(self, player_id: str) -> None:
        self.player_ids.discard(player_id)
        self.pending_welcome.discard(player_id)
        self.peak_masses.pop(player_id, None)
        self.spawn_sim_times.pop(player_id, None)

    @property
    def welcome_sent(self) -> bool:
        return bool(self.player_ids) and not self.pending_welcome

    @welcome_sent.setter
    def welcome_sent(self, sent: bool) -> None:
        if sent:
            self.pending_welcome.clear()
        else:
            self.pending_welcome = set(self.player_ids)

    @property
    def peak_mass(self) -> float:
        pid = self.player_id
        if pid is None:
            return 0.0
        return self.peak_masses.get(pid, 0.0)

    @peak_mass.setter
    def peak_mass(self, value: float) -> None:
        pid = self.player_id
        if pid is not None:
            self.peak_masses[pid] = value

    @property
    def spawn_sim_time(self) -> float | None:
        pid = self.player_id
        if pid is None:
            return None
        return self.spawn_sim_times.get(pid)

    @spawn_sim_time.setter
    def spawn_sim_time(self, value: float | None) -> None:
        pid = self.player_id
        if pid is None:
            return
        if value is None:
            self.spawn_sim_times.pop(pid, None)
        else:
            self.spawn_sim_times[pid] = value


class FoodStream:
    """Change-gated food broadcast. One shared payload, a version per socket.

    Pellets have no velocity, so `World.food_epoch` catches every change
    without allocating a frozenset of ids each tick. The payload is bare
    `[x, y]` integer pairs — food has no radius in the simulation, so a
    half-unit shift is sub-pixel at play zoom, and dropping the 32-hex ids is
    what makes the message small enough to send at all. `encoded` is that
    payload dumped once; `_emit` sends the string to every behind socket
    rather than `json.dumps` per socket. A later per-pellet delta is a
    drop-in on this same message type.
    """

    def __init__(self) -> None:
        self.version = 0
        self._epoch = 0
        self.payload: dict = {"type": "food", "version": 0, "food": []}
        self.encoded: str = encode_json(self.payload)

    def refresh(self, world: World) -> None:
        if world.food_epoch == self._epoch:
            return
        self._epoch = world.food_epoch
        self.version += 1
        self.payload = {
            "type": "food",
            "version": self.version,
            "food": [[round(f.x), round(f.y)] for f in world.food.values()],
        }
        self.encoded = encode_json(self.payload)


def normalize_name(value: object) -> str:
    if not isinstance(value, str):
        return DEFAULT_NAME
    name = value.strip()[:NAME_MAX_LEN]
    return name or DEFAULT_NAME


def normalize_color(value: object) -> str:
    if isinstance(value, str) and _COLOR_RE.match(value):
        return value.lower()
    return DEFAULT_COLOR


def unique_name(world: World, name: str) -> str:
    """`name`, or `name (2)`, `name (3)`... if a live player already holds it.

    Names are the only thing distinguishing two blobs on screen — colors are
    deliberately not unique, since a hex picker cannot promise that — so two
    players answering to "jack" makes the scoreboard and the labels lie.
    Compared case-insensitively: "Jack" and "jack" are the same name to anyone
    reading them, and letting them coexist is impersonation with extra steps.

    Suffixing rather than rejecting the join. A rejection needs a new wire
    message and an error state in the menu, and it can fail a *respawn* — the
    Game Over screen resends the name it already had, which someone else may
    have taken during that life — leaving the client stuck on an overlay whose
    only button no longer works. Renaming always succeeds, and the client shows
    the result because labels are drawn from `state`, not from what was typed.

    Only live players are considered, so a name is free again the moment its
    owner is eaten or closes the tab.
    """
    taken = {player.name.casefold() for player in world.players.values()}
    if name.casefold() not in taken:
        return name
    # Terminates: every n yields a candidate ending in its own digits, so the
    # candidates are distinct, and `taken` is finite. The base is truncated to
    # leave room for the suffix rather than letting the result exceed
    # NAME_MAX_LEN, which is the cap the label rendering is sized against.
    n = 2
    while True:
        suffix = f" ({n})"
        candidate = f"{name[: max(NAME_MAX_LEN - len(suffix), 0)]}{suffix}"
        if candidate.casefold() not in taken:
            return candidate
        n += 1


def parse_client_message(raw: object) -> dict | None:
    """Return a normalized client message, or None to drop it."""
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, dict):
        return None

    msg_type = raw.get("type")
    if msg_type == "join":
        return {
            "type": "join",
            "name": normalize_name(raw.get("name")),
            "color": normalize_color(raw.get("color")),
            "bot": raw.get("bot") is True,
        }
    if msg_type == "input":
        dx, dy = raw.get("dx"), raw.get("dy")
        if isinstance(dx, bool) or isinstance(dy, bool):
            return None
        if not isinstance(dx, (int, float)) or not isinstance(dy, (int, float)):
            return None
        if not math.isfinite(dx) or not math.isfinite(dy):
            return None
        msg = {"type": "input", "dx": float(dx), "dy": float(dy)}
        return _with_optional_id(raw, msg)
    if msg_type == "split":
        return _with_optional_id(raw, {"type": "split"})
    return None


def _with_optional_id(raw: dict, msg: dict) -> dict | None:
    """Attach `"id"` when present. Drop the message if it is not a non-empty string."""
    if "id" not in raw:
        return msg
    ident = raw["id"]
    if not isinstance(ident, str) or not ident:
        return None
    msg["id"] = ident
    return msg


def _wire_config() -> dict:
    """Arena size, tick rate, spawn mass and speed knobs — values the client must not hardcode."""
    return {
        "world": {"width": WORLD_WIDTH, "height": WORLD_HEIGHT},
        "tickRate": TICK_RATE,
        "initialPlayerMass": INITIAL_PLAYER_MASS,
        "baseSpeed": BASE_SPEED,
        "speedFalloff": SPEED_FALLOFF,
        "speedFloorFraction": SPEED_FLOOR_FRACTION,
    }


def serialize_state(world: World) -> dict:
    return {
        "type": "state",
        **_wire_config(),
        "players": [
            {
                "id": player.id,
                "name": player.name,
                "color": player.color,
                "protected": simulation.is_spawn_protected(world, player),
                "inert": player.inert,
                "peak_mass": player.last_total_mass,
                "pieces": [
                    {
                        "piece_id": piece.piece_id,
                        "x": round(piece.x, 2),
                        "y": round(piece.y, 2),
                        "mass": round(piece.mass, 1),
                        "remerge_in": (
                            0
                            if player.inert
                            else round(simulation.remerge_in(world, piece), 2)
                        ),
                    }
                    for piece in player.pieces
                ],
            }
            for player in world.players.values()
        ],
    }


def welcome_message(player_id: str) -> dict:
    return {
        "type": "welcome",
        "id": player_id,
        **_wire_config(),
    }


def game_over_message(
    peak_mass: float, survival_seconds: float, player_id: str | None = None
) -> dict:
    payload = {
        "type": "game_over",
        "peak_mass": peak_mass,
        "survival_seconds": survival_seconds,
    }
    if player_id is not None:
        payload["id"] = player_id
    return payload


def playing_player(world: World, session: ClientSession) -> Player | None:
    for player_id in session.player_ids:
        player = world.players.get(player_id)
        if player is not None:
            return player
    return None


def playing_players(world: World, session: ClientSession) -> list[Player]:
    return [
        world.players[player_id]
        for player_id in session.player_ids
        if player_id in world.players
    ]


def _owned_player(world: World, session: ClientSession, msg: dict) -> Player | None:
    """The player an `input`/`split` is for.

    One life: omit `id` (browsers) or send that id. Many lives: `id` is
    required and must be owned; missing or foreign ids are ignored.
    """
    ident = msg.get("id")
    if ident is not None:
        if not session.owns(ident):
            return None
        return world.players.get(ident)
    if len(session.player_ids) != 1:
        return None
    return world.players.get(session.player_id)


def handle_message(world: World, session: ClientSession, msg: dict) -> dict | None:
    if msg["type"] == "join":
        return handle_join(world, session, msg)
    if msg["type"] == "input":
        handle_input(world, session, msg)
        return None
    if msg["type"] == "split":
        handle_split(world, session, msg)
        return None
    return None


def _debug_spawn_xy() -> tuple[float, float] | None:
    """Pinned spawn for local feel-testing. Not a join field — that would be a cheat.

    `BLOBBY_DEBUG_SPAWN=x,y` is read on each join so a test can monkeypatch it.
    Malformed values are ignored and the RNG spawn runs as usual.
    """
    raw = os.environ.get("BLOBBY_DEBUG_SPAWN", "").strip()
    if not raw:
        return None
    parts = raw.split(",")
    if len(parts) != 2:
        return None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None


def _debug_spawn_mass() -> float | None:
    """Pinned spawn mass for local split testing. Not a join field — that would be a cheat.

    `BLOBBY_DEBUG_MASS=280` is read on each join so a test can monkeypatch it.
    Empty, non-finite, or non-positive values are ignored and spawn stays at
    INITIAL_PLAYER_MASS.
    """
    raw = os.environ.get("BLOBBY_DEBUG_MASS", "").strip()
    if not raw:
        return None
    try:
        mass = float(raw)
    except ValueError:
        return None
    if not math.isfinite(mass) or mass <= 0:
        return None
    return mass


def handle_join(world: World, session: ClientSession, msg: dict) -> dict | None:
    is_bot = bool(msg.get("bot"))
    if is_bot:
        session.allows_multi = True
    # A human socket ignores a second join while any owned life is still in
    # the world. A bot fleet (`bot: true`) may spawn another alongside them.
    if playing_player(world, session) is not None and not (
        session.allows_multi and is_bot
    ):
        return None
    # The name that lands in the world, not the one that was typed, so the log
    # line and the labels agree. The client learns it from `state` like everyone
    # else; nothing needs to echo it back on `welcome`.
    session.name = unique_name(world, msg["name"])
    session.color = msg["color"]
    spawn = _debug_spawn_xy()
    mass = _debug_spawn_mass()
    if mass is None:
        mass = INITIAL_PLAYER_MASS
    if spawn is None:
        player = world.spawn_player(
            session.name, color=session.color, mass=mass, bot=is_bot
        )
    else:
        player = world.spawn_player(
            session.name,
            x=spawn[0],
            y=spawn[1],
            color=session.color,
            mass=mass,
            bot=is_bot,
        )
    # A spawn point is drawn from the RNG and clamped into the rectangle, never
    # away from other bodies, so this is the only thing stopping a join from
    # landing inside a predator and dying on the next tick.
    player.spawn_time = world.now
    peak = sum(piece.mass for piece in player.pieces)
    session.player_ids.add(player.id)
    session.pending_welcome.add(player.id)
    session.peak_masses[player.id] = peak
    session.spawn_sim_times[player.id] = world.now
    return welcome_message(player.id)


def handle_input(world: World, session: ClientSession, msg: dict) -> None:
    player = _owned_player(world, session, msg)
    if player is None or player.inert:
        return
    player.last_input = (msg["dx"], msg["dy"])


def handle_split(world: World, session: ClientSession, msg: dict) -> None:
    player = _owned_player(world, session, msg)
    if player is None or player.inert:
        return
    simulation.try_split(world, player)


def update_and_eliminate(
    world: World, sessions: Sequence[ClientSession]
) -> list[tuple[ClientSession, dict]]:
    """Update peak mass, then drop empty-piece players from the world.

    Returns (session, game_over payload) pairs for sockets that were playing.
    A player with no session is still removed, so the broadcast cannot emit
    ghosts.

    Runs after `simulation.step`, which means a player killed this tick is
    already down to zero pieces here. Mass it gained before dying, and mass
    it held before a burst peel, lives on `Player.last_total_mass`.
    """
    session_by_player: dict[str, ClientSession] = {}
    for session in sessions:
        for player_id in session.player_ids:
            session_by_player[player_id] = session
    deaths: list[tuple[ClientSession, dict]] = []
    eliminated: list[str] = []

    for player in list(world.players.values()):
        session = session_by_player.get(player.id)
        if session is not None:
            current = sum(piece.mass for piece in player.pieces)
            total = max(current, player.last_total_mass)
            if total > session.peak_masses.get(player.id, 0.0):
                session.peak_masses[player.id] = total
        if player.pieces:
            continue
        eliminated.append(player.id)
        if session is not None:
            spawned = session.spawn_sim_times.get(player.id)
            survival = world.now - spawned if spawned is not None else 0.0
            peak = session.peak_masses.get(player.id, 0.0)
            deaths.append(
                (session, game_over_message(peak, survival, player.id))
            )
            session.release_player(player.id)

    for player_id in eliminated:
        world.remove_player(player_id)
    return deaths
