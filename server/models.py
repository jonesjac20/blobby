"""Dataclasses for world entities. No behavior lives here."""

from dataclasses import dataclass, field

from server.config import (
    BURST_SPLIT_SECONDS,
    DEFAULT_COLOR,
    REMERGE_SECONDS,
    SPAWN_INVULN_SECONDS,
)


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
    # Life high-water mark: total mass after eats, before this tick's losses
    # or a burst peel. Never decreases, so `peak_mass` still sees a 75k life
    # after the remnant is 1500. A player that dies has an empty piece list by
    # the time anything downstream looks, so this is what Game Over reads.
    last_total_mass: float = 0.0
    # True when this life came from a join with `"bot": true`. Humans never
    # send it. Bots burst at a lower mass cap than humans.
    bot: bool = False
    # Socket-less burst corpse: cannot eat, never remerges, auto-splits.
    inert: bool = False
    # Sim-time of the last inert auto-split (or of the burst that created it).
    last_burst_split: float = -BURST_SPLIT_SECONDS


@dataclass
class Food:
    id: str
    x: float
    y: float
