"""Authoritative world state: players, food, and the simulation clock."""

import math
import random
import uuid

from server.config import (
    DEFAULT_COLOR,
    FOOD_COUNT,
    INITIAL_PLAYER_MASS,
    WORLD_HEIGHT,
    WORLD_WIDTH,
)
from server.models import Food, Piece, Player


def clamp_body_position(x: float, y: float, mass: float) -> tuple[float, float]:
    """Inset a piece's center so its disc stays inside the world rectangle.

    A blob whose diameter exceeds the world is pinned to the center rather than
    inverting the range.
    """
    radius = math.sqrt(max(mass, 0.0) / math.pi)

    def _axis(value: float, limit: float) -> float:
        lo, hi = radius, limit - radius
        if lo > hi:
            return limit / 2.0
        return min(max(value, lo), hi)

    return _axis(x, WORLD_WIDTH), _axis(y, WORLD_HEIGHT)


class World:
    def __init__(self, seed: int | None = None, food_target: int | None = None) -> None:
        self.players: dict[str, Player] = {}
        self.food: dict[str, Food] = {}
        # Simulation time in seconds. Advanced by simulation.step, never by the
        # wall clock, so timers behave identically at any tick rate.
        self.now: float = 0.0
        self.rng = random.Random(seed)
        # None means "use the configured FOOD_COUNT". An explicit value lets a
        # scenario run with a sparse or empty food field.
        self.food_target = food_target

    def new_id(self) -> str:
        """A 32-hex-char uuid4 drawn from the world RNG so worlds stay reproducible."""
        return uuid.UUID(int=self.rng.getrandbits(128), version=4).hex

    def spawn_player(
        self,
        name: str,
        x: float | None = None,
        y: float | None = None,
        mass: float = INITIAL_PLAYER_MASS,
        color: str = DEFAULT_COLOR,
    ) -> Player:
        if x is None:
            x = self.rng.uniform(0.0, WORLD_WIDTH)
        if y is None:
            y = self.rng.uniform(0.0, WORLD_HEIGHT)
        x, y = clamp_body_position(x, y, mass)
        player = Player(
            id=self.new_id(),
            name=name,
            pieces=[Piece(piece_id=self.new_id(), x=x, y=y, mass=mass)],
            color=color,
        )
        self.players[player.id] = player
        return player

    def spawn_food_to_target_count(self) -> None:
        # Resolved per call so tests can patch FOOD_COUNT after construction.
        target = FOOD_COUNT if self.food_target is None else self.food_target
        while len(self.food) < target:
            food = Food(
                id=self.new_id(),
                x=self.rng.uniform(0.0, WORLD_WIDTH),
                y=self.rng.uniform(0.0, WORLD_HEIGHT),
            )
            self.food[food.id] = food

    def remove_player(self, player_id: str) -> None:
        self.players.pop(player_id, None)
