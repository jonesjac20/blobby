"""Covers every item on the Phase 1 verify list in GUIDEBOOK.md."""

import pytest
from conftest import add_piece, add_player, advance

from server import simulation
from server.config import (
    FOOD_COUNT,
    FOOD_MASS,
    MAX_PIECES,
    MIN_SPLIT_MASS,
    REMERGE_SECONDS,
    SPLIT_KICK_DECAY_SECONDS,
    SPLIT_KICK_SPEED,
    WORLD_HEIGHT,
    WORLD_WIDTH,
    speed_for_mass,
)
from server.models import Food

TICK = 1.0 / 30.0


# --- movement -------------------------------------------------------------


@pytest.mark.parametrize(
    "direction, expect_x, expect_y",
    [
        ((1.0, 0.0), 1, 0),
        ((-1.0, 0.0), -1, 0),
        ((0.0, 1.0), 0, 1),
        ((0.0, -1.0), 0, -1),
        ((1.0, 1.0), 1, 1),
    ],
)
def test_piece_moves_in_direction_of_input(world, direction, expect_x, expect_y):
    player = add_player(world, x=1000.0, y=1000.0, last_input=direction)
    piece = player.pieces[0]

    simulation.step(world, TICK)

    def sign(value: float) -> int:
        if value > 1e-9:
            return 1
        return -1 if value < -1e-9 else 0

    assert sign(piece.x - 1000.0) == expect_x
    assert sign(piece.y - 1000.0) == expect_y


def test_zero_input_does_not_move_piece(world):
    player = add_player(world, x=1000.0, y=1000.0, last_input=(0.0, 0.0))
    piece = player.pieces[0]

    advance(world, 1.0, TICK)

    assert (piece.x, piece.y) == (1000.0, 1000.0)


def test_unnormalized_input_moves_at_full_speed(world):
    """A half-length input vector is a direction, not a throttle."""
    unit = add_player(world, "unit", 100.0, 100.0, last_input=(1.0, 0.0))
    short = add_player(world, "short", 100.0, 1000.0, last_input=(0.5, 0.0))

    simulation.step(world, TICK)

    assert unit.pieces[0].x == pytest.approx(short.pieces[0].x)


def test_speed_for_mass_decreases_as_mass_grows():
    assert speed_for_mass(30) > speed_for_mass(100) > speed_for_mass(300)


def test_heavier_piece_travels_less_per_tick(world):
    light = add_player(world, "light", 100.0, 100.0, mass=30, last_input=(1.0, 0.0))
    heavy = add_player(world, "heavy", 100.0, 1000.0, mass=300, last_input=(1.0, 0.0))

    advance(world, 1.0, TICK)

    assert light.pieces[0].x - 100.0 > heavy.pieces[0].x - 100.0


def test_piece_stays_inside_world_bounds(world):
    player = add_player(
        world, x=WORLD_WIDTH - 1.0, y=1.0, mass=30, last_input=(1.0, -1.0)
    )
    piece = player.pieces[0]

    advance(world, 5.0, TICK)

    assert 0.0 <= piece.x <= WORLD_WIDTH
    assert 0.0 <= piece.y <= WORLD_HEIGHT
    assert piece.x == pytest.approx(WORLD_WIDTH)
    assert piece.y == pytest.approx(0.0)


# --- food -----------------------------------------------------------------


def test_food_inside_radius_is_eaten(world):
    player = add_player(world, x=500.0, y=500.0, mass=30)
    piece = player.pieces[0]
    radius = simulation.radius_for_mass(piece.mass)
    world.food["target"] = Food(id="target", x=500.0 + radius / 2.0, y=500.0)

    simulation.step(world, TICK)

    assert "target" not in world.food
    assert piece.mass == pytest.approx(30 + FOOD_MASS)


def test_food_outside_radius_is_not_eaten(world):
    player = add_player(world, x=500.0, y=500.0, mass=30)
    piece = player.pieces[0]
    radius = simulation.radius_for_mass(piece.mass)
    world.food["target"] = Food(id="target", x=500.0 + radius * 2.0, y=500.0)

    simulation.step(world, TICK)

    assert "target" in world.food
    assert piece.mass == pytest.approx(30)


def test_eaten_food_is_respawned_to_target_count(world_with_food):
    assert len(world_with_food.food) == FOOD_COUNT
    for food_id in list(world_with_food.food)[:20]:
        del world_with_food.food[food_id]
    assert len(world_with_food.food) == FOOD_COUNT - 20

    simulation.step(world_with_food, TICK)

    assert len(world_with_food.food) == FOOD_COUNT


def test_food_count_stays_at_target_over_time(world_with_food):
    add_player(world_with_food, x=1000.0, y=1000.0, mass=500, last_input=(1.0, 0.3))

    for _ in range(300):
        simulation.step(world_with_food, TICK)
        assert len(world_with_food.food) == FOOD_COUNT


# --- eating other players -------------------------------------------------


@pytest.mark.parametrize(
    "ratio, expect_eaten",
    [(1.10, False), (1.24, False), (1.25, False), (1.26, True), (2.00, True)],
)
def test_eat_requires_mass_ratio_above_1_25(world, ratio, expect_eaten):
    small_mass = 100.0
    big_mass = small_mass * ratio
    big = add_player(world, "big", 500.0, 500.0, mass=big_mass)
    small = add_player(world, "small", 500.0, 500.0, mass=small_mass)

    simulation.step(world, TICK)

    if expect_eaten:
        assert small.pieces == []
        assert big.pieces[0].mass == pytest.approx(big_mass + small_mass)
    else:
        assert len(big.pieces) == 1
        assert len(small.pieces) == 1
        assert big.pieces[0].mass == pytest.approx(big_mass)
        assert small.pieces[0].mass == pytest.approx(small_mass)


def test_no_eating_when_pieces_do_not_overlap(world):
    big = add_player(world, "big", 100.0, 100.0, mass=1000)
    small = add_player(world, "small", 1100.0, 1100.0, mass=10)

    simulation.step(world, TICK)

    assert len(big.pieces) == 1
    assert len(small.pieces) == 1


def test_own_pieces_never_eat_each_other(world):
    player = add_player(world, "solo", 500.0, 500.0, mass=200)
    player.pieces[0].split_time = world.now
    add_piece(world, player, 500.0, 500.0, mass=10, split_time=world.now)

    advance(world, 1.0, TICK)

    assert len(player.pieces) == 2
    assert sorted(p.mass for p in player.pieces) == [10, 200]


# --- splitting ------------------------------------------------------------


def test_try_split_refuses_below_min_split_mass(world):
    player = add_player(world, mass=MIN_SPLIT_MASS - 1)

    assert simulation.try_split(world, player, 1.0, 0.0) == 0
    assert len(player.pieces) == 1
    assert player.pieces[0].mass == MIN_SPLIT_MASS - 1


def test_try_split_allows_exactly_min_split_mass(world):
    player = add_player(world, mass=MIN_SPLIT_MASS)

    assert simulation.try_split(world, player, 1.0, 0.0) == 1
    assert len(player.pieces) == 2


def test_try_split_refuses_at_max_pieces(world):
    player = add_player(world, mass=100)
    for _ in range(MAX_PIECES - 1):
        add_piece(world, player, 500.0, 500.0, mass=100)
    assert len(player.pieces) == MAX_PIECES

    assert simulation.try_split(world, player, 1.0, 0.0) == 0
    assert len(player.pieces) == MAX_PIECES
    assert all(p.mass == 100 for p in player.pieces)


def test_try_split_stops_once_max_pieces_is_reached(world):
    player = add_player(world, mass=100)
    for _ in range(MAX_PIECES - 2):
        add_piece(world, player, 500.0, 500.0, mass=100)
    assert len(player.pieces) == MAX_PIECES - 1

    assert simulation.try_split(world, player, 1.0, 0.0) == 1
    assert len(player.pieces) == MAX_PIECES
    assert sorted(p.mass for p in player.pieces) == [50, 50] + [100] * 6


def test_try_split_refuses_zero_length_cursor(world):
    player = add_player(world, mass=100)

    assert simulation.try_split(world, player, 0.0, 0.0) == 0
    assert len(player.pieces) == 1


def test_split_produces_two_half_mass_pieces_and_one_kick(world):
    player = add_player(world, x=500.0, y=500.0, mass=40)

    assert simulation.try_split(world, player, 1.0, 0.0) == 1

    assert len(player.pieces) == 2
    assert all(p.mass == 20 for p in player.pieces)
    assert all((p.x, p.y) == (500.0, 500.0) for p in player.pieces)

    kicked = [p for p in player.pieces if p.initial_kick_vx or p.initial_kick_vy]
    assert len(kicked) == 1
    assert kicked[0].initial_kick_vx == pytest.approx(SPLIT_KICK_SPEED)
    assert kicked[0].initial_kick_vy == pytest.approx(0.0)


def test_split_kick_points_at_the_cursor(world):
    player = add_player(world, x=500.0, y=500.0, mass=40)

    simulation.try_split(world, player, 0.0, -3.0)

    child = player.pieces[1]
    assert child.initial_kick_vx == pytest.approx(0.0)
    assert child.initial_kick_vy == pytest.approx(-SPLIT_KICK_SPEED)


def test_split_kick_decays_to_zero(world):
    player = add_player(world, x=500.0, y=500.0, mass=40)
    simulation.try_split(world, player, 1.0, 0.0)
    child = player.pieces[1]

    advance(world, SPLIT_KICK_DECAY_SECONDS / 2.0, TICK)
    assert 0.0 < child.vx < SPLIT_KICK_SPEED

    advance(world, SPLIT_KICK_DECAY_SECONDS, TICK)
    assert child.vx == 0.0
    assert child.vy == 0.0


def test_split_kick_moves_the_new_piece_away_from_the_parent(world):
    player = add_player(world, x=500.0, y=500.0, mass=40)
    simulation.try_split(world, player, 1.0, 0.0)
    parent, child = player.pieces

    advance(world, SPLIT_KICK_DECAY_SECONDS, TICK)

    assert parent.x == pytest.approx(500.0)
    assert child.x > parent.x


def test_split_resets_a_leftover_kick_on_the_parent(world):
    """Re-splitting resets split_time, which must not revive the old kick."""
    player = add_player(world, x=500.0, y=500.0, mass=200)
    simulation.try_split(world, player, 1.0, 0.0)
    advance(world, SPLIT_KICK_DECAY_SECONDS, TICK)
    assert all(p.vx == 0.0 for p in player.pieces)

    simulation.try_split(world, player, 1.0, 0.0)
    simulation.step(world, TICK)

    # Only the two pieces created by the second split carry a kick.
    assert sum(1 for p in player.pieces if p.vx != 0.0) == 2


def test_split_is_exponential_halving(world):
    """One press splits every eligible piece, so counts double and masses halve."""
    player = add_player(world, x=500.0, y=500.0, mass=280)

    assert simulation.try_split(world, player, 1.0, 0.0) == 1
    assert sorted(p.mass for p in player.pieces) == [140] * 2

    assert simulation.try_split(world, player, 1.0, 0.0) == 2
    assert sorted(p.mass for p in player.pieces) == [70] * 4

    assert simulation.try_split(world, player, 1.0, 0.0) == 4
    assert sorted(p.mass for p in player.pieces) == [35] * 8

    assert simulation.try_split(world, player, 1.0, 0.0) == 0
    assert sorted(p.mass for p in player.pieces) == [35] * 8

    assert sum(p.mass for p in player.pieces) == pytest.approx(280)


# --- remerging ------------------------------------------------------------


def test_pieces_remerge_after_timer_when_overlapping(world):
    player = add_player(world, "solo", 500.0, 500.0, mass=50)
    add_piece(world, player, 500.0, 500.0, mass=50)
    for piece in player.pieces:
        piece.split_time = world.now - REMERGE_SECONDS - 0.01

    simulation.step(world, TICK)

    assert len(player.pieces) == 1
    assert player.pieces[0].mass == pytest.approx(100)


def test_pieces_do_not_remerge_before_timer(world):
    player = add_player(world, "solo", 500.0, 500.0, mass=50)
    add_piece(world, player, 500.0, 500.0, mass=50)
    for piece in player.pieces:
        piece.split_time = world.now - REMERGE_SECONDS + 0.5

    simulation.step(world, TICK)

    assert len(player.pieces) == 2


def test_pieces_do_not_remerge_when_far_apart(world):
    player = add_player(world, "solo", 100.0, 100.0, mass=50)
    add_piece(world, player, 1100.0, 1100.0, mass=50, split_time=-REMERGE_SECONDS)
    player.pieces[0].split_time = -REMERGE_SECONDS

    advance(world, 1.0, TICK)

    assert len(player.pieces) == 2


def test_split_pieces_remerge_after_the_full_cycle(world):
    """The whole Phase 1 story: split, drift apart, drift back, remerge."""
    player = add_player(world, x=500.0, y=500.0, mass=100)
    simulation.try_split(world, player, 1.0, 0.0)
    assert len(player.pieces) == 2

    advance(world, SPLIT_KICK_DECAY_SECONDS, TICK)
    assert player.pieces[0].x != player.pieces[1].x

    # Pull them back together; only the remerge timer should still be gating.
    for piece in player.pieces:
        piece.x = piece.y = 500.0
    advance(world, REMERGE_SECONDS, TICK)

    assert len(player.pieces) == 1
    assert player.pieces[0].mass == pytest.approx(100)
