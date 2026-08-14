"""Dataclasses for world entities. No behavior lives here."""

from dataclasses import dataclass, field

from server.config import DEFAULT_COLOR, REMERGE_SECONDS


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
    # Sim-time stamp of the split that created this piece. The default is far
    # enough in the past that a never-split piece is immediately remergeable.
    split_time: float = -REMERGE_SECONDS


@dataclass
class Player:
    id: str
    name: str
    pieces: list[Piece] = field(default_factory=list)
    last_input: tuple[float, float] = (0.0, 0.0)
    color: str = DEFAULT_COLOR


@dataclass
class Food:
    id: str
    x: float
    y: float
