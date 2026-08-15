"""Covers every item on the Phase 1 verify list in docs/GUIDEBOOK.md."""

import math

import pytest
from conftest import add_piece, add_player, advance, split

from server import simulation
from server.config import (
    BASE_SPEED,
    EAT_OVERLAP,
    EAT_RATIO,
    FOOD_COUNT,
    FOOD_MASS,
    INITIAL_PLAYER_MASS,
    MAX_PIECES,
    MIN_SPLIT_MASS,
    OWN_PIECE_OVERLAP,
    REMERGE_SECONDS,
    SPAWN_INVULN_SECONDS,
    SPLIT_KICK_DECAY_SECONDS,
    SPLIT_KICK_RADII,
    TICK_RATE,
    WORLD_HEIGHT,
    WORLD_WIDTH,
    split_kick_displacement_max,
    split_kick_speed,
    speed_for_mass,
)
from server.models import Food
from server.world import World

TICK = 1.0 / TICK_RATE


def separation(a, b) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


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


@pytest.mark.parametrize(
    "direction",
    [
        (float("nan"), 0.0),
        (0.0, float("nan")),
        (float("nan"), float("nan")),
        (float("inf"), 0.0),
        (float("-inf"), 1.0),
    ],
)
def test_non_finite_input_is_ignored(world, direction):
    player = add_player(world, x=500.0, y=500.0, last_input=direction)
    piece = player.pieces[0]

    advance(world, 1.0, TICK)

    assert (piece.x, piece.y) == (500.0, 500.0)


def test_non_finite_input_cannot_spread_to_another_player(world):
    """NaN fails every threshold test, so it used to leak out through collisions.

    `_project_apart` separates this overlapping pair, and a NaN center would slip
    past its `distance >= target` bail and write NaN into the neighbour.
    """
    bad = add_player(world, "bad", 500.0, 500.0, mass=100, last_input=(float("nan"), 0.0))
    good = add_player(world, "good", 505.0, 500.0, mass=100, last_input=(0.0, 0.0))

    advance(world, 1.0, TICK)

    for piece in (*bad.pieces, *good.pieces):
        assert math.isfinite(piece.x)
        assert math.isfinite(piece.y)


def test_speed_for_mass_decreases_as_mass_grows():
    assert speed_for_mass(30) > speed_for_mass(100) > speed_for_mass(300)


def test_speed_for_mass_of_zero_is_base_speed():
    assert speed_for_mass(0) == BASE_SPEED
    assert speed_for_mass(-1) == BASE_SPEED


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
    radius = simulation.radius_for_mass(piece.mass)

    advance(world, 5.0, TICK)

    assert radius <= piece.x <= WORLD_WIDTH - radius
    assert radius <= piece.y <= WORLD_HEIGHT - radius
    assert piece.x == pytest.approx(WORLD_WIDTH - radius)
    assert piece.y == pytest.approx(radius)


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


def test_food_just_inside_radius_is_eaten(world):
    """The rule is circle-covers-center, so the threshold is exactly radius."""
    player = add_player(world, x=500.0, y=500.0, mass=30)
    radius = simulation.radius_for_mass(30)
    world.food["target"] = Food(id="target", x=500.0 + radius * 0.99, y=500.0)

    simulation.step(world, TICK)

    assert "target" not in world.food


def test_food_just_outside_radius_is_not_eaten(world):
    player = add_player(world, x=500.0, y=500.0, mass=30)
    radius = simulation.radius_for_mass(30)
    world.food["target"] = Food(id="target", x=500.0 + radius * 1.01, y=500.0)

    simulation.step(world, TICK)

    assert "target" in world.food


@pytest.mark.parametrize("mass", [200, 40, 20, 10])
def test_food_on_the_path_is_eaten_even_when_travel_exceeds_radius(world, mass):
    """Point-sampling the post-move center misses the midpoint at mass 10."""
    start_x, start_y = 500.0, 500.0
    player = add_player(
        world, x=start_x, y=start_y, mass=mass, last_input=(1.0, 0.0)
    )
    travel = speed_for_mass(mass) * TICK
    world.food["mid"] = Food(id="mid", x=start_x + travel / 2.0, y=start_y)

    simulation.step(world, TICK)

    assert "mid" not in world.food
    assert player.pieces[0].mass == pytest.approx(mass + FOOD_MASS)


def test_two_pieces_cannot_both_eat_the_same_pellet(world):
    left = add_player(world, "left", 500.0, 500.0, mass=40)
    right = add_player(world, "right", 500.0, 500.0, mass=40)
    world.food["pellet"] = Food(id="pellet", x=500.0, y=500.0)

    simulation.step(world, TICK)

    gained = sorted(p.pieces[0].mass - 40 for p in (left, right))
    assert gained == [0.0, FOOD_MASS]
    assert "pellet" not in world.food


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


def test_food_that_spawns_on_a_blob_is_eaten_the_same_tick(world):
    """Spawn is after the swept eat, so a pellet on a blob would otherwise sit a tick."""
    player = add_player(world, x=500.0, y=500.0, mass=30)
    world.food_target = 1
    placements = iter([(500.0, 500.0), (50.0, 50.0)])

    def spawn_placed() -> None:
        while len(world.food) < 1:
            x, y = next(placements)
            food = Food(id=world.new_id(), x=x, y=y)
            world.food[food.id] = food

    world.spawn_food_to_target_count = spawn_placed  # type: ignore[method-assign]

    simulation.step(world, TICK)

    assert player.pieces[0].mass == pytest.approx(30 + FOOD_MASS)
    leftover = next(iter(world.food.values()))
    assert leftover.x == 50.0
    assert leftover.y == 50.0


# --- eating other players -------------------------------------------------


@pytest.mark.parametrize(
    "predator_first", [True, False], ids=["predator-joins-first", "prey-joins-first"]
)
@pytest.mark.parametrize(
    "ratio, expect_eaten",
    [(1.10, False), (1.24, False), (1.25, False), (1.26, True), (2.00, True)],
)
def test_eat_requires_mass_ratio_above_1_25(world, ratio, expect_eaten, predator_first):
    """Both join orders, because the eat check is a two-branch scan over each pair.

    `_eat_other_players` tests `_can_eat(a, b)` first and only falls through to
    `_can_eat(b, a)` when that fails, and `a` is whichever player joined earlier.
    Staging the predator first would leave the second branch unexecuted.
    """
    small_mass = 100.0
    big_mass = small_mass * ratio
    names = ["big", "small"] if predator_first else ["small", "big"]
    masses = {"big": big_mass, "small": small_mass}
    players = {name: add_player(world, name, 500.0, 500.0, mass=masses[name]) for name in names}
    big, small = players["big"], players["small"]

    simulation.step(world, TICK)

    if expect_eaten:
        assert small.pieces == []
        assert big.pieces[0].mass == pytest.approx(big_mass + small_mass)
    else:
        assert len(big.pieces) == 1
        assert len(small.pieces) == 1
        assert big.pieces[0].mass == pytest.approx(big_mass)
        assert small.pieces[0].mass == pytest.approx(small_mass)


def test_a_predator_that_joins_late_eats_an_earlier_prey(world):
    """Prey joins first, holds still and never eats; the predator grows into it.

    Both players arrive at equal mass, so the ratio is earned rather than staged,
    and this join order leaves `_can_eat(b, a)` as the only branch that can fire.
    """
    prey = add_player(world, "prey", 600.0, 500.0, mass=100)
    predator = add_player(world, "predator", 500.0, 500.0, mass=100)

    # A cluster inside the predator's own radius and nowhere near the motionless
    # prey, sized to carry it just past the ratio in a single bite.
    pellets = int((100.0 * EAT_RATIO - 100.0) // FOOD_MASS) + 1
    for index in range(pellets):
        food = Food(id=f"pellet-{index}", x=498.0 + index % 5, y=498.0 + index // 5)
        world.food[food.id] = food

    simulation.step(world, TICK)

    assert world.food == {}
    assert prey.pieces[0].mass == 100
    assert predator.pieces[0].mass > prey.pieces[0].mass * EAT_RATIO

    predator.last_input = (1.0, 0.0)
    advance(world, 4.0, TICK)

    assert prey.pieces == []
    assert predator.pieces[0].mass == pytest.approx(200 + pellets * FOOD_MASS)


def test_no_eating_when_pieces_do_not_overlap(world):
    big = add_player(world, "big", 100.0, 100.0, mass=1000)
    small = add_player(world, "small", 1100.0, 1100.0, mass=10)

    simulation.step(world, TICK)

    assert len(big.pieces) == 1
    assert len(small.pieces) == 1


def test_own_pieces_never_eat_each_other(world):
    """Crushed into a wall, so the pair is actually deep enough to be eaten.

    In open field `_resolve_collisions` runs before the eat check and separates
    own pieces to OWN_PIECE_OVERLAP, short of the EAT_OVERLAP an eat needs, so
    the pair would survive even without the own-piece exclusion. Bounds beat
    separation. Against a wall the small piece has nowhere to be pushed along
    the normal, so the two end up coincident on that axis and the exclusion
    becomes the only thing keeping the small one alive.
    """
    player = add_player(world, "solo", 12.0, 500.0, mass=400, last_input=(-1.0, 0.0))
    player.pieces[0].split_time = world.now
    add_piece(world, player, 1.0, 500.0, mass=30, split_time=world.now)

    deepest = 0.0
    for _ in range(120):
        simulation.step(world, TICK)
        assert len(player.pieces) == 2
        deepest = max(deepest, simulation.engulfment(*player.pieces))

    assert deepest > EAT_OVERLAP, "staging never reached a depth an eat could fire at"
    assert sorted(p.mass for p in player.pieces) == [30, 400]


def test_eating_some_pieces_of_a_split_prey_leaves_the_rest_to_remerge(world):
    """Eating is per-piece. Survivors still remerge with only the uneaten mass.

    Predator 60 can eat a mass-40 fragment (60 > 40 * 1.25) but not the unsplit
    120, which is the interesting case.
    """
    predator = add_player(world, "pred", 500.0, 500.0, mass=60)
    prey = add_player(world, "prey", 200.0, 500.0, mass=40)
    add_piece(world, prey, 200.0, 500.0, mass=40)
    rim = simulation.radius_for_mass(60)
    add_piece(world, prey, predator.pieces[0].x + rim * 0.5, 500.0, mass=40)
    for piece in prey.pieces:
        piece.split_time = world.now

    simulation.step(world, TICK)

    assert len(prey.pieces) == 2
    assert sorted(p.mass for p in prey.pieces) == [40, 40]
    assert predator.pieces[0].mass == pytest.approx(100)

    for piece in prey.pieces:
        piece.split_time = world.now - REMERGE_SECONDS - 0.01
        piece.x, piece.y = 200.0, 500.0
    simulation.step(world, TICK)

    assert len(prey.pieces) == 1
    assert prey.pieces[0].mass == pytest.approx(80)


# --- solid bodies ---------------------------------------------------------


def test_equal_players_collide_instead_of_passing_through(world):
    """Neither can eat the other, so neither can enter the other."""
    left = add_player(world, "left", 480.0, 500.0, mass=100, last_input=(1.0, 0.0))
    right = add_player(world, "right", 520.0, 500.0, mass=100, last_input=(-1.0, 0.0))
    a, b = left.pieces[0], right.pieces[0]

    for _ in range(round(3.0 / TICK)):
        simulation.step(world, TICK)
        assert simulation.engulfment(a, b) < EAT_OVERLAP

    assert len(left.pieces) == 1
    assert len(right.pieces) == 1
    assert simulation.engulfment(a, b) == pytest.approx(0.0, abs=1e-9)


def test_a_predator_is_not_blocked_by_its_prey(world):
    """Collision must not become a way to be invulnerable to something bigger."""
    big = add_player(world, "big", 480.0, 500.0, mass=200, last_input=(1.0, 0.0))
    small = add_player(world, "small", 520.0, 500.0, mass=100, last_input=(-1.0, 0.0))

    advance(world, 3.0, TICK)

    assert small.pieces == []
    assert big.pieces[0].mass == pytest.approx(300)


def test_a_graze_does_not_eat_until_the_predator_sinks_in(world):
    """The mass ratio is met throughout; only the overlap depth changes."""
    big = add_player(world, "big", 500.0, 500.0, mass=200)
    small = add_player(world, "small", 500.0, 500.0, mass=100)
    predator, prey = big.pieces[0], small.pieces[0]
    rim = simulation.radius_for_mass(predator.mass)

    # Circles just touching.
    prey.x = predator.x + rim + simulation.radius_for_mass(prey.mass)
    simulation.step(world, TICK)
    assert len(small.pieces) == 1

    # Prey's center now inside the predator's rim.
    prey.x = predator.x + rim * 0.95
    simulation.step(world, TICK)
    assert small.pieces == []


# --- spawn invulnerability -------------------------------------------------


def test_a_player_staged_by_a_test_is_edible_immediately(world):
    """The default `spawn_time` is already expired, so Phase 1 staging is unprotected.

    Protection is granted by a live `join`, not by `spawn_player`. If the
    default ever changes, every eat test in this file starts lying.
    """
    big = add_player(world, "big", 500.0, 500.0, mass=200)
    small = add_player(world, "small", 500.0, 500.0, mass=100)

    assert big.spawn_time <= -SPAWN_INVULN_SECONDS

    simulation.step(world, TICK)

    assert small.pieces == []


def test_a_spawn_protected_player_is_not_eaten_until_the_window_closes(world):
    """A predator bearing down on a fresh spawn shoves it until the window closes."""
    big = add_player(world, "big", 480.0, 500.0, mass=200, last_input=(1.0, 0.0))
    small = add_player(world, "small", 520.0, 500.0, mass=100)
    small.spawn_time = world.now

    advance(world, SPAWN_INVULN_SECONDS - TICK, TICK)

    assert len(small.pieces) == 1
    assert big.pieces[0].mass == pytest.approx(200)

    advance(world, 1.0, TICK)

    assert small.pieces == []
    assert big.pieces[0].mass == pytest.approx(300)


def test_a_spawn_protected_player_is_solid_rather_than_edible(world):
    """Protection cannot leave the pair interpenetrating, or the kill just lands late."""
    big = add_player(world, "big", 480.0, 500.0, mass=200, last_input=(1.0, 0.0))
    small = add_player(world, "small", 520.0, 500.0, mass=100)
    small.spawn_time = world.now
    predator, prey = big.pieces[0], small.pieces[0]

    while world.now < SPAWN_INVULN_SECONDS - TICK:
        simulation.step(world, TICK)
        assert simulation.engulfment(predator, prey) < EAT_OVERLAP

    assert len(small.pieces) == 1


def test_a_spawn_protected_player_can_still_eat(world):
    """Protection is one-way: it stops you being a meal, not being a predator."""
    fresh = add_player(world, "fresh", 500.0, 500.0, mass=200)
    victim = add_player(world, "victim", 500.0, 500.0, mass=100)
    fresh.spawn_time = world.now

    simulation.step(world, TICK)

    assert victim.pieces == []
    assert fresh.pieces[0].mass == pytest.approx(300)


def test_spawn_protection_covers_every_piece_of_a_split_player(world):
    """Protection lives on the player, so every fragment stays uneatable and solid.

    Splitting neither forfeits nor extends the window. A predator overlapping
    either half is shoved rather than left to sink in until the timer dies.
    """
    big = add_player(world, "big", 500.0, 500.0, mass=400)
    small = add_player(world, "small", 500.0, 500.0, mass=100)
    small.spawn_time = world.now
    split(world, small)
    assert len(small.pieces) == 2
    predator = big.pieces[0]

    while world.now < SPAWN_INVULN_SECONDS - TICK:
        simulation.step(world, TICK)
        assert len(small.pieces) == 2
        for prey in small.pieces:
            assert simulation.engulfment(predator, prey) < EAT_OVERLAP

    assert big.pieces[0].mass == pytest.approx(400)


def test_bounds_hold_when_blobs_are_crushed_into_a_corner(world):
    """Separation loses to the world edge, and residual overlap is the price."""
    crowd = add_player(world, "crowd", 20.0, 20.0, mass=200, last_input=(-1.0, -1.0))
    crowd.pieces[0].split_time = world.now
    for offset in (3.0, 6.0, 9.0):
        add_piece(world, crowd, 20.0 + offset, 20.0, mass=200, split_time=world.now)
    # Heavy enough to shove, too light to eat, so it cannot be projected away.
    add_player(world, "shover", 90.0, 20.0, mass=220, last_input=(-1.0, 0.0))

    advance(world, 4.0, TICK)

    for player in world.players.values():
        for piece in player.pieces:
            radius = simulation.radius_for_mass(piece.mass)
            assert radius <= piece.x <= WORLD_WIDTH - radius
            assert radius <= piece.y <= WORLD_HEIGHT - radius


@pytest.mark.parametrize("mass", [30, 200, 1000])
@pytest.mark.parametrize(
    "x, y, dx, dy",
    [
        (1.0, 1.0, -1.0, -1.0),
        (WORLD_WIDTH - 1.0, 1.0, 1.0, -1.0),
        (1.0, WORLD_HEIGHT - 1.0, -1.0, 1.0),
        (WORLD_WIDTH - 1.0, WORLD_HEIGHT - 1.0, 1.0, 1.0),
    ],
)
def test_body_stays_inside_world_in_every_corner(world, mass, x, y, dx, dy):
    player = add_player(world, x=x, y=y, mass=mass, last_input=(dx, dy))
    piece = player.pieces[0]
    radius = simulation.radius_for_mass(mass)

    advance(world, 3.0, TICK)

    assert radius <= piece.x <= WORLD_WIDTH - radius
    assert radius <= piece.y <= WORLD_HEIGHT - radius


# --- cohesion -------------------------------------------------------------


def test_split_halves_drift_back_into_contact(world):
    player = add_player(world, x=500.0, y=500.0, mass=200)
    split(world, player)
    parent, child = player.pieces

    advance(world, SPLIT_KICK_DECAY_SECONDS, TICK)
    assert separation(parent, child) > 25.0

    advance(world, 3.0, TICK)
    assert simulation.engulfment(parent, child) == pytest.approx(
        OWN_PIECE_OVERLAP, abs=1e-9
    )

    # And they stay there rather than creeping on into a merge.
    advance(world, 2.0, TICK)
    assert len(player.pieces) == 2
    assert simulation.engulfment(parent, child) == pytest.approx(
        OWN_PIECE_OVERLAP, abs=1e-9
    )


def test_cohesion_does_not_eat_into_the_split_kick(world):
    """Peak separation still matches the analytic kick integral exactly."""
    player = add_player(world, x=500.0, y=500.0, mass=100)
    split(world, player)
    parent, child = player.pieces
    # Staged far apart so the separation pass has nothing to do either, leaving
    # the kick as the only thing that can move the child.
    child.x += 200.0
    start = child.x

    advance(world, SPLIT_KICK_DECAY_SECONDS, TICK)

    assert child.x - start == pytest.approx(
        split_kick_speed(100) * SPLIT_KICK_DECAY_SECONDS / 2.0, abs=1e-9
    )
    assert parent.x == pytest.approx(500.0, abs=1e-9)


def test_every_piece_of_a_full_cluster_touches_a_neighbour(world):
    player = add_player(world, x=600.0, y=600.0, mass=280)
    for direction in ((1.0, 0.0), (0.0, 1.0), (-0.8, 0.6)):
        split(world, player, direction)
        advance(world, 1.5, TICK)
    assert len(player.pieces) == MAX_PIECES

    advance(world, 4.0, TICK)

    for piece in player.pieces:
        touching = [
            other
            for other in player.pieces
            if other is not piece and simulation.engulfment(piece, other) >= 0.0
        ]
        assert touching, f"piece at ({piece.x:.1f}, {piece.y:.1f}) is off on its own"


# --- splitting ------------------------------------------------------------


def test_try_split_refuses_below_min_split_mass(world):
    player = add_player(world, mass=MIN_SPLIT_MASS - 1)

    assert split(world, player) == 0
    assert len(player.pieces) == 1
    assert player.pieces[0].mass == MIN_SPLIT_MASS - 1


def test_try_split_allows_exactly_min_split_mass(world):
    player = add_player(world, mass=MIN_SPLIT_MASS)

    assert split(world, player) == 1
    assert len(player.pieces) == 2


def test_try_split_refuses_at_max_pieces(world):
    player = add_player(world, mass=100)
    for _ in range(MAX_PIECES - 1):
        add_piece(world, player, 500.0, 500.0, mass=100)
    assert len(player.pieces) == MAX_PIECES

    assert split(world, player) == 0
    assert len(player.pieces) == MAX_PIECES
    assert all(p.mass == 100 for p in player.pieces)


def test_try_split_stops_once_max_pieces_is_reached(world):
    player = add_player(world, mass=100)
    for _ in range(MAX_PIECES - 2):
        add_piece(world, player, 500.0, 500.0, mass=100)
    assert len(player.pieces) == MAX_PIECES - 1

    assert split(world, player) == 1
    assert len(player.pieces) == MAX_PIECES
    assert sorted(p.mass for p in player.pieces) == [50, 50] + [100] * 6


@pytest.mark.parametrize("n_pieces", [3, 5, 6, 7])
def test_try_split_splits_the_largest_pieces_first(world, n_pieces):
    masses = [100 - 10 * i for i in range(n_pieces)]
    player = add_player(world, mass=masses[0])
    for mass in masses[1:]:
        add_piece(world, player, 500.0, 500.0, mass=mass)

    ranked = sorted(player.pieces, key=lambda p: p.mass, reverse=True)
    n_split = min(n_pieces, MAX_PIECES - n_pieces)
    should_split = {p.piece_id for p in ranked[:n_split]}
    should_keep = {p.piece_id for p in ranked[n_split:]}
    original_mass = {p.piece_id: p.mass for p in player.pieces}

    assert split(world, player) == n_split
    assert len(player.pieces) == n_pieces + n_split

    by_id = {p.piece_id: p for p in player.pieces}
    for piece_id in should_split:
        assert by_id[piece_id].mass == pytest.approx(original_mass[piece_id] / 2.0)
    for piece_id in should_keep:
        assert by_id[piece_id].mass == pytest.approx(original_mass[piece_id])


def test_split_with_zero_input_produces_no_kick(world):
    """A player who has not moved yet can still split; the halves just land flat."""
    player = add_player(world, x=500.0, y=500.0, mass=100, last_input=(0.0, 0.0))

    assert simulation.try_split(world, player) == 1
    assert len(player.pieces) == 2
    assert all(p.initial_kick_vx == 0.0 for p in player.pieces)
    assert all(p.initial_kick_vy == 0.0 for p in player.pieces)
    assert all((p.x, p.y) == (500.0, 500.0) for p in player.pieces)

    simulation.step(world, TICK)

    parent, child = player.pieces
    assert separation(parent, child) > 0.0
    assert simulation.engulfment(parent, child) == pytest.approx(
        OWN_PIECE_OVERLAP, abs=1e-9
    )


def test_split_produces_two_half_mass_pieces_and_one_kick(world):
    player = add_player(world, x=500.0, y=500.0, mass=40)

    assert split(world, player) == 1

    assert len(player.pieces) == 2
    assert all(p.mass == 20 for p in player.pieces)
    assert all((p.x, p.y) == (500.0, 500.0) for p in player.pieces)

    kicked = [p for p in player.pieces if p.initial_kick_vx or p.initial_kick_vy]
    assert len(kicked) == 1
    assert kicked[0].initial_kick_vx == pytest.approx(split_kick_speed(40))
    assert kicked[0].initial_kick_vy == pytest.approx(0.0)


@pytest.mark.parametrize(
    "direction",
    [(1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -3.0), (2.0, 2.0), (-0.4, 0.9)],
)
def test_split_kick_points_along_last_input(world, direction):
    """The wire message has no direction field, so stored input is the only aim."""
    player = add_player(world, x=500.0, y=500.0, mass=40, last_input=direction)

    assert simulation.try_split(world, player) == 1

    child = player.pieces[1]
    kick = split_kick_speed(40) / math.hypot(*direction)
    assert child.initial_kick_vx == pytest.approx(kick * direction[0])
    assert child.initial_kick_vy == pytest.approx(kick * direction[1])


def test_split_kick_decays_to_zero(world):
    player = add_player(world, x=500.0, y=500.0, mass=40)
    split(world, player)
    child = player.pieces[1]

    advance(world, SPLIT_KICK_DECAY_SECONDS / 2.0, TICK)
    assert 0.0 < child.vx < split_kick_speed(40)

    advance(world, SPLIT_KICK_DECAY_SECONDS, TICK)
    assert child.vx == 0.0
    assert child.vy == 0.0


def test_split_kick_moves_the_new_piece_away_from_the_parent(world):
    player = add_player(world, x=500.0, y=500.0, mass=40)
    split(world, player)
    parent, child = player.pieces

    advance(world, SPLIT_KICK_DECAY_SECONDS, TICK)

    # The parent no longer holds still: separation shoves it the other way while
    # the two are still on top of each other.
    assert child.x > parent.x


def _isolated_kick_displacement(mass: float) -> float:
    """Kick-only travel: child staged far enough that cohesion and separation miss."""
    world = World(seed=0, food_target=0)
    player = add_player(world, x=500.0, y=500.0, mass=mass)
    split(world, player)
    child = player.pieces[1]
    child.x += 200.0
    start = child.x
    advance(world, SPLIT_KICK_DECAY_SECONDS, TICK)
    return child.x - start


def test_split_kick_displacement_grows_with_parent_mass_below_the_cap():
    small = _isolated_kick_displacement(50)
    large = _isolated_kick_displacement(200)
    assert large > small
    assert small == pytest.approx(
        SPLIT_KICK_RADII * simulation.radius_for_mass(50), abs=1e-9
    )
    assert large == pytest.approx(
        SPLIT_KICK_RADII * simulation.radius_for_mass(200), abs=1e-9
    )


def test_split_kick_displacement_is_capped_at_a_fraction_of_the_arena():
    """A giant's lunge is split_kick_displacement_max(), not 6 parent radii."""
    mass = 10_000
    uncapped = SPLIT_KICK_RADII * simulation.radius_for_mass(mass)
    cap = split_kick_displacement_max()
    assert uncapped > cap

    travelled = _isolated_kick_displacement(mass)
    assert travelled == pytest.approx(cap, abs=1e-9)


def test_split_kick_displacement_max_tracks_the_shorter_arena_axis(monkeypatch):
    import server.config as config

    monkeypatch.setattr(config, "WORLD_WIDTH", 1000.0)
    monkeypatch.setattr(config, "WORLD_HEIGHT", 800.0)
    assert config.split_kick_displacement_max() == pytest.approx(80.0)


def test_a_heavy_split_pops_farther_apart_than_the_halves_rest(world):
    """Feel-pass A2: a mass-2000 split is a lunge, not a twitch resettled by projection."""
    mass = 2000.0
    player = add_player(world, x=WORLD_WIDTH / 2, y=WORLD_HEIGHT / 2, mass=mass)
    split(world, player)
    _parent, child = player.pieces
    # Isolation offset so cohesion cannot eat the kick during the 0.5s window.
    child.x += 200.0
    start = child.x

    advance(world, SPLIT_KICK_DECAY_SECONDS, TICK)

    travelled = child.x - start
    resting = 2 * simulation.radius_for_mass(mass / 2) * (1.0 - OWN_PIECE_OVERLAP)
    assert travelled > resting
    assert travelled == pytest.approx(
        SPLIT_KICK_RADII * simulation.radius_for_mass(mass), abs=1e-9
    )


def test_split_resets_a_leftover_kick_on_the_parent(world):
    """Re-splitting resets split_time, which must not revive the old kick.

    Also pins down that the cluster forces stay out of `vx/vy`: if cohesion wrote
    a velocity, the count below would pick up the drifting halves too.
    """
    player = add_player(world, x=500.0, y=500.0, mass=200)
    split(world, player)
    advance(world, SPLIT_KICK_DECAY_SECONDS, TICK)
    assert all(p.vx == 0.0 for p in player.pieces)

    split(world, player)
    simulation.step(world, TICK)

    # Only the two pieces created by the second split carry a kick.
    assert sum(1 for p in player.pieces if p.vx != 0.0) == 2


def test_split_is_exponential_halving(world):
    """One press splits every eligible piece, so counts double and masses halve."""
    player = add_player(world, x=500.0, y=500.0, mass=280)

    assert split(world, player) == 1
    assert sorted(p.mass for p in player.pieces) == [140] * 2

    assert split(world, player) == 2
    assert sorted(p.mass for p in player.pieces) == [70] * 4

    assert split(world, player) == 4
    assert sorted(p.mass for p in player.pieces) == [35] * 8

    assert split(world, player) == 0
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


def test_pieces_do_not_remerge_across_the_map_before_the_timer(world):
    """Distance recall only starts once both timers have cleared."""
    player = add_player(world, "solo", 100.0, 100.0, mass=50)
    add_piece(world, player, 1100.0, 1100.0, mass=50)
    for piece in player.pieces:
        piece.split_time = world.now - REMERGE_SECONDS + 0.5

    advance(world, 0.4, TICK)

    assert len(player.pieces) == 2
    assert separation(*player.pieces) > 1000.0


def test_merge_ready_pieces_coalesce_from_across_the_map(world):
    """Once the timer clears, distance does not matter: the leftover flies home."""
    player = add_player(world, "solo", 100.0, 100.0, mass=50)
    add_piece(world, player, 1100.0, 1100.0, mass=50, split_time=-REMERGE_SECONDS)
    player.pieces[0].split_time = -REMERGE_SECONDS

    advance(world, 8.0, TICK)

    assert len(player.pieces) == 1
    assert player.pieces[0].mass == pytest.approx(100)


def test_a_merge_ready_fragment_catches_the_parent_while_steering(world):
    """A light leftover used to outrun the core and hover just outside it."""
    player = add_player(world, "solo", 500.0, 500.0, mass=245, last_input=(1.0, 0.0))
    add_piece(world, player, 530.0, 500.0, mass=35, split_time=-REMERGE_SECONDS)
    player.pieces[0].split_time = -REMERGE_SECONDS

    advance(world, 3.0, TICK)

    assert len(player.pieces) == 1
    assert player.pieces[0].mass == pytest.approx(280)


def test_an_eight_piece_merge_ready_cluster_collapses_to_one(world):
    """The while-merged loop has to cascade; two leftover clumps is the bug."""
    player = add_player(world, "solo", 600.0, 600.0, mass=280)
    for direction in ((1.0, 0.0), (0.0, 1.0), (-0.8, 0.6)):
        assert split(world, player, direction) > 0
    assert len(player.pieces) == MAX_PIECES

    for i, piece in enumerate(player.pieces):
        piece.split_time = -REMERGE_SECONDS
        piece.x = 200.0 + (i % 4) * 400.0
        piece.y = 200.0 + (i // 4) * 800.0

    advance(world, 8.0, TICK)

    assert len(player.pieces) == 1
    assert player.pieces[0].mass == pytest.approx(280)


def test_pieces_in_contact_do_not_merge_until_they_sink_in(world):
    """Resting contact is shallower than MERGE_OVERLAP, so the pull has work to do."""
    player = add_player(world, "solo", 500.0, 500.0, mass=100)
    split(world, player)
    parent, child = player.pieces

    advance(world, REMERGE_SECONDS, TICK)

    assert len(player.pieces) == 2, "merged the moment the timer cleared"
    assert simulation.engulfment(parent, child) > OWN_PIECE_OVERLAP


def test_merge_pull_closes_the_gap_over_several_ticks(world):
    player = add_player(world, "solo", 500.0, 500.0, mass=100)
    split(world, player)
    parent, child = player.pieces
    advance(world, REMERGE_SECONDS, TICK)

    distances = []
    while len(player.pieces) == 2:
        distances.append(separation(parent, child))
        simulation.step(world, TICK)
        assert world.now < REMERGE_SECONDS + 2.0, "merge pull never finished"

    assert len(distances) > 1, "the pull took a single tick, so it is a snap"
    assert all(later < earlier for earlier, later in zip(distances, distances[1:]))
    assert player.pieces[0].mass == pytest.approx(100)


def test_split_pieces_remerge_after_the_full_cycle(world):
    """The whole Phase 1 story: split, pop apart, drift back, sink in, remerge."""
    player = add_player(world, x=500.0, y=500.0, mass=100)
    split(world, player)
    assert len(player.pieces) == 2

    advance(world, SPLIT_KICK_DECAY_SECONDS, TICK)
    assert separation(*player.pieces) > 25.0

    advance(world, REMERGE_SECONDS + 1.0, TICK)

    assert len(player.pieces) == 1
    assert player.pieces[0].mass == pytest.approx(100)


# --- world / hardening ----------------------------------------------------


def test_zero_mass_pieces_do_not_crash_remerge(world):
    player = add_player(world, "z", 500.0, 500.0, mass=0)
    add_piece(world, player, 500.0, 500.0, mass=0)
    for piece in player.pieces:
        piece.split_time = world.now - REMERGE_SECONDS - 0.01

    simulation.step(world, TICK)


def test_engulfment_of_massless_pieces_is_total_on_contact(world):
    a = add_player(world, "a", 500.0, 500.0, mass=0)
    b = add_player(world, "b", 500.0, 500.0, mass=0)

    assert simulation.engulfment(a.pieces[0], b.pieces[0]) == 1.0

    b.pieces[0].x += 1.0
    assert simulation.engulfment(a.pieces[0], b.pieces[0]) == 0.0


def test_step_survives_a_player_with_no_pieces(world):
    player = add_player(world, "gone", 500.0, 500.0)
    player.pieces.clear()

    simulation.step(world, TICK)

    assert player.pieces == []
    assert player.id in world.players


def test_spawn_player_clamps_into_the_world(world):
    radius = simulation.radius_for_mass(INITIAL_PLAYER_MASS)
    player = world.spawn_player("out", -50.0, WORLD_HEIGHT + 50.0)
    piece = player.pieces[0]

    assert piece.x == pytest.approx(radius)
    assert piece.y == pytest.approx(WORLD_HEIGHT - radius)


def test_spawn_player_without_coordinates_is_seeded_and_in_bounds():
    radius = simulation.radius_for_mass(INITIAL_PLAYER_MASS)
    first = World(seed=0, food_target=0).spawn_player("a")
    second = World(seed=0, food_target=0).spawn_player("a")
    piece = first.pieces[0]

    assert (piece.x, piece.y) == (second.pieces[0].x, second.pieces[0].y)
    assert radius <= piece.x <= WORLD_WIDTH - radius
    assert radius <= piece.y <= WORLD_HEIGHT - radius


def test_spawn_player_stores_color(world):
    player = world.spawn_player("painted", 100.0, 100.0, color="#ff0000")

    assert player.color == "#ff0000"


def test_new_id_is_32_hex_chars(world):
    seen = {world.new_id() for _ in range(100)}

    assert len(seen) == 100
    assert all(len(item) == 32 for item in seen)
    for item in seen:
        int(item, 16)


def test_remove_player_drops_the_player_and_ignores_unknown_ids(world):
    first = add_player(world, "a", 100.0, 100.0)
    second = add_player(world, "b", 200.0, 200.0)

    world.remove_player(first.id)

    assert first.id not in world.players
    assert second.id in world.players

    world.remove_player("missing")
    assert second.id in world.players
