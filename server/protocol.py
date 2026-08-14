"""WebSocket JSON protocol: parse, serialize, sessions, join/death.

Wire messages are additive to source plan section 4. See GUIDEBOOK Divergence.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from server import simulation
from server.config import DEFAULT_COLOR, NAME_MAX_LEN
from server.models import Player
from server.world import World

_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass
class ClientSession:
    """Per-socket state. Survives death; the Player does not."""

    ws: Any = None
    player_id: str | None = None
    name: str = ""
    color: str = DEFAULT_COLOR
    peak_mass: float = 0.0
    spawn_sim_time: float | None = None
    # False between spawning the player and the socket actually receiving its
    # welcome. A state broadcast in that window would name a player the client
    # cannot follow yet.
    welcome_sent: bool = False


def normalize_name(value: object) -> str:
    if not isinstance(value, str):
        return "blob"
    name = value.strip()[:NAME_MAX_LEN]
    return name or "blob"


def normalize_color(value: object) -> str:
    if isinstance(value, str) and _COLOR_RE.match(value):
        return value.lower()
    return DEFAULT_COLOR


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
        }
    if msg_type == "input":
        dx, dy = raw.get("dx"), raw.get("dy")
        if isinstance(dx, bool) or isinstance(dy, bool):
            return None
        if not isinstance(dx, (int, float)) or not isinstance(dy, (int, float)):
            return None
        if not math.isfinite(dx) or not math.isfinite(dy):
            return None
        return {"type": "input", "dx": float(dx), "dy": float(dy)}
    if msg_type == "split":
        return {"type": "split"}
    return None


def serialize_state(world: World) -> dict:
    return {
        "type": "state",
        "players": [
            {
                "id": player.id,
                "name": player.name,
                "color": player.color,
                "pieces": [
                    {
                        "piece_id": piece.piece_id,
                        "x": piece.x,
                        "y": piece.y,
                        "mass": piece.mass,
                    }
                    for piece in player.pieces
                ],
            }
            for player in world.players.values()
        ],
        "food": [
            {"id": food.id, "x": food.x, "y": food.y} for food in world.food.values()
        ],
    }


def welcome_message(player_id: str) -> dict:
    return {"type": "welcome", "id": player_id}


def game_over_message(peak_mass: float, survival_seconds: float) -> dict:
    return {
        "type": "game_over",
        "peak_mass": peak_mass,
        "survival_seconds": survival_seconds,
    }


def playing_player(world: World, session: ClientSession) -> Player | None:
    if session.player_id is None:
        return None
    return world.players.get(session.player_id)


def handle_message(world: World, session: ClientSession, msg: dict) -> dict | None:
    if msg["type"] == "join":
        return handle_join(world, session, msg)
    if msg["type"] == "input":
        handle_input(world, session, msg)
        return None
    if msg["type"] == "split":
        handle_split(world, session)
        return None
    return None


def handle_join(world: World, session: ClientSession, msg: dict) -> dict | None:
    if playing_player(world, session) is not None:
        return None
    session.name = msg["name"]
    session.color = msg["color"]
    player = world.spawn_player(session.name, color=session.color)
    # A spawn point is drawn from the RNG and clamped into the rectangle, never
    # away from other bodies, so this is the only thing stopping a join from
    # landing inside a predator and dying on the next tick.
    player.spawn_time = world.now
    session.player_id = player.id
    session.welcome_sent = False
    session.peak_mass = sum(piece.mass for piece in player.pieces)
    session.spawn_sim_time = world.now
    return welcome_message(player.id)


def handle_input(world: World, session: ClientSession, msg: dict) -> None:
    player = playing_player(world, session)
    if player is None:
        return
    player.last_input = (msg["dx"], msg["dy"])


def handle_split(world: World, session: ClientSession) -> None:
    player = playing_player(world, session)
    if player is None:
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
    already down to zero pieces here. Mass it gained before dying comes from
    `Player.last_total_mass`, recorded mid-tick for exactly this reason.
    """
    session_by_player = {
        session.player_id: session for session in sessions if session.player_id
    }
    deaths: list[tuple[ClientSession, dict]] = []
    eliminated: list[str] = []

    for player in list(world.players.values()):
        session = session_by_player.get(player.id)
        if session is not None:
            # A dead player's pieces are already gone, so its own last total is
            # the only record of mass it gained on the tick that killed it.
            total = (
                sum(piece.mass for piece in player.pieces)
                if player.pieces
                else player.last_total_mass
            )
            if total > session.peak_mass:
                session.peak_mass = total
        if player.pieces:
            continue
        eliminated.append(player.id)
        if session is not None:
            spawned = session.spawn_sim_time
            survival = world.now - spawned if spawned is not None else 0.0
            deaths.append((session, game_over_message(session.peak_mass, survival)))
            session.player_id = None
            session.spawn_sim_time = None

    for player_id in eliminated:
        world.remove_player(player_id)
    return deaths
