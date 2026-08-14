"""Dataclasses for world entities. No behavior lives here."""

from dataclasses import dataclass, field

from server.config import DEFAULT_COLOR, REMERGE_SECONDS, SPAWN_INVULN_SECONDS


@dataclass
class Piece:
    piece_id: str
    x: float
    y: float
    mass: float
    vx: float = 0.0
    vy: float = 0.0
    initial_kick_vx: float = 0.0
    initial_kick_vy: float = 0.0
    # Sim-time stamp of the split that created this piece. Default is far enough in the past that an unsplit piece is immediately remergeable.
    split_time: float = -REMERGE_SECONDS


@dataclass
class Player:
    id: str
    name: str
    pieces: list[Piece] = field(default_factory=list)
    last_input: tuple[float, float] = (0.0, 0.0)
    color: str = DEFAULT_COLOR
    # Sim-time stamp of the join that spawned this player, gating spawn
    # invulnerability. The default is far enough in the past that a player
    # staged by a test or a scenario is edible immediately; only a live join
    # sets it to `world.now`.
    spawn_time: float = -SPAWN_INVULN_SECONDS
    # Total mass at the high-water mark of the most recent tick, recorded before
    # eaten pieces are removed. A player that dies has an empty piece list by
    # the time anything downstream looks, so this is what `peak_mass` reads.
    last_total_mass: float = 0.0


@dataclass
class Food:
    id: str
    x: float
    y: float
