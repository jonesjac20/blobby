"""Shared fixtures and helpers for the Phase 1 simulation tests."""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from server import simulation  # noqa: E402
from server.config import INITIAL_PLAYER_MASS  # noqa: E402
from server.models import Piece, Player  # noqa: E402
from server.world import World  # noqa: E402


@pytest.fixture
def no_food(monkeypatch):
    """Stop food from respawning.

    server.world binds FOOD_COUNT at import time, so patching server.config
    would have no effect here.
    """
    monkeypatch.setattr("server.world.FOOD_COUNT", 0)


@pytest.fixture
def world(no_food) -> World:
    """Empty deterministic world. No food, so masses only change on purpose."""
    return World(seed=0)


@pytest.fixture
def world_with_food() -> World:
    world = World(seed=0)
    world.spawn_food_to_target_count()
    return world


def add_player(
    world: World,
    name: str = "P",
    x: float = 500.0,
    y: float = 500.0,
    mass: float = INITIAL_PLAYER_MASS,
    last_input: tuple[float, float] = (0.0, 0.0),
) -> Player:
    player = world.spawn_player(name, x, y, mass)
    player.last_input = last_input
    return player


def add_piece(
    world: World,
    player: Player,
    x: float,
    y: float,
    mass: float,
    split_time: float | None = None,
) -> Piece:
    piece = Piece(piece_id=world.new_id(), x=x, y=y, mass=mass)
    if split_time is not None:
        piece.split_time = split_time
    player.pieces.append(piece)
    return piece


def advance(world: World, seconds: float, dt: float) -> int:
    """Step until sim time has advanced by at least `seconds`. Returns tick count."""
    target = world.now + seconds
    ticks = 0
    while world.now < target:
        simulation.step(world, dt)
        ticks += 1
    return ticks
