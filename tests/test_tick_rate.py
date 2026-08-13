"""Tick-rate invariance.

The tick loop should only decide *when* step() runs, never *what* it computes.
Every test here runs the same scenario at 15Hz, 30Hz and 60Hz and asserts the
outcome matches. These are the tests that catch per-tick decrements and other
dt-dependent integration, which look fine at a single tick rate.
"""

import pytest

from server import simulation
from server.config import (
    INITIAL_PLAYER_MASS,
    REMERGE_SECONDS,
    SPLIT_KICK_DECAY_SECONDS,
    SPLIT_KICK_SPEED,
    speed_for_mass,
)
from server.world import World

TICK_RATES = [1.0 / 15.0, 1.0 / 30.0, 1.0 / 60.0]
TOLERANCE = 1e-6


def test_movement_is_invariant_across_tick_rates(no_food):
    end_positions = []

    for dt in TICK_RATES:
        world = World(seed=0)
        player = world.spawn_player("A", 100.0, 100.0, mass=INITIAL_PLAYER_MASS)
        player.last_input = (1.0, 0.0)

        for _ in range(round(1.0 / dt)):
            simulation.step(world, dt)

        assert world.now == pytest.approx(1.0)
        end_positions.append(player.pieces[0].x)

    assert max(end_positions) - min(end_positions) < TOLERANCE
    # One second of travel at the mass-30 speed.
    assert end_positions[0] == pytest.approx(
        100.0 + speed_for_mass(INITIAL_PLAYER_MASS), abs=TOLERANCE
    )


def test_split_kick_displacement_is_invariant_across_tick_rates(no_food):
    """A naive per-tick velocity decrement drifts here; the analytic one does not."""
    displacements = []

    for dt in TICK_RATES:
        world = World(seed=0)
        player = world.spawn_player("A", 500.0, 500.0, mass=40)
        simulation.try_split(world, player, 1.0, 0.0)

        while world.now < SPLIT_KICK_DECAY_SECONDS:
            simulation.step(world, dt)

        parent, child = player.pieces
        assert child.vx == 0.0, f"kick still active at dt={dt}"
        assert child.vy == 0.0
        assert parent.x == pytest.approx(500.0)
        displacements.append(child.x - parent.x)

    assert max(displacements) - min(displacements) < TOLERANCE
    # Integral of a linear decay from SPLIT_KICK_SPEED to zero.
    assert displacements[0] == pytest.approx(
        SPLIT_KICK_SPEED * SPLIT_KICK_DECAY_SECONDS / 2.0, abs=TOLERANCE
    )


def test_kick_reaches_exactly_zero_at_every_tick_rate(no_food):
    for dt in TICK_RATES:
        world = World(seed=0)
        player = world.spawn_player("A", 500.0, 500.0, mass=40)
        simulation.try_split(world, player, 1.0, 0.0)
        child = player.pieces[1]

        # Still decaying halfway through the window, at every tick rate.
        while world.now < SPLIT_KICK_DECAY_SECONDS / 2.0:
            simulation.step(world, dt)
        assert 0.0 < child.vx < SPLIT_KICK_SPEED, f"unexpected mid-decay vx at dt={dt}"

        while world.now < SPLIT_KICK_DECAY_SECONDS:
            simulation.step(world, dt)
        assert child.vx == 0.0
        assert child.vy == 0.0


def test_remerge_fires_at_the_same_sim_time_across_tick_rates(no_food):
    merge_times = []

    for dt in TICK_RATES:
        world = World(seed=0)
        player = world.spawn_player("A", 500.0, 500.0, mass=100)
        simulation.try_split(world, player, 1.0, 0.0)
        # Drop the kick so the halves stay co-located and only the timer gates
        # the merge.
        for piece in player.pieces:
            piece.vx = piece.vy = 0.0
            piece.initial_kick_vx = piece.initial_kick_vy = 0.0

        merged_at = None
        while world.now < REMERGE_SECONDS + 1.0:
            simulation.step(world, dt)
            if len(player.pieces) == 1:
                merged_at = world.now
                break

        assert merged_at is not None, f"never remerged at dt={dt}"
        # The merge lands on the first tick at or after the timer, so it can be
        # up to a full tick late. The margin is deliberately loose: at 15Hz the
        # exact-one-tick bound is a float coin flip.
        assert merged_at == pytest.approx(REMERGE_SECONDS, abs=1.5 * dt)
        assert merged_at >= REMERGE_SECONDS
        assert player.pieces[0].mass == pytest.approx(100)
        merge_times.append(merged_at)

    assert max(merge_times) - min(merge_times) <= 1.5 * max(TICK_RATES)


def test_remerge_does_not_fire_early_at_a_coarse_tick_rate(no_food):
    """A 15Hz tick must not round the 12s timer down into an early merge."""
    for dt in TICK_RATES:
        world = World(seed=0)
        player = world.spawn_player("A", 500.0, 500.0, mass=100)
        simulation.try_split(world, player, 1.0, 0.0)
        for piece in player.pieces:
            piece.vx = piece.vy = 0.0
            piece.initial_kick_vx = piece.initial_kick_vy = 0.0

        while world.now < REMERGE_SECONDS - dt:
            simulation.step(world, dt)
            assert len(player.pieces) == 2, (
                f"merged at t={world.now} (dt={dt}), before {REMERGE_SECONDS}s"
            )


def test_world_is_deterministic_for_a_given_seed():
    """Guards against a stray global random() creeping into the simulation."""

    def run() -> tuple[list, list]:
        world = World(seed=42)
        player = world.spawn_player("A", 100.0, 100.0)
        player.last_input = (1.0, 0.4)
        world.spawn_food_to_target_count()

        for _ in range(120):
            simulation.step(world, 1.0 / 30.0)

        food = sorted((f.id, f.x, f.y) for f in world.food.values())
        pieces = sorted((p.piece_id, p.x, p.y, p.mass) for p in player.pieces)
        return food, pieces

    assert run() == run()


def test_same_sim_time_gives_same_state_at_different_tick_rates(no_food):
    """End-to-end: split, kick decay and steering all land in the same place at 2s."""
    states = []

    for dt in TICK_RATES:
        world = World(seed=0)
        player = world.spawn_player("A", 500.0, 500.0, mass=100)
        player.last_input = (0.0, 1.0)
        simulation.try_split(world, player, 1.0, 0.0)

        # An exact tick count, so every rate covers the same 2s of sim time and
        # the comparison isn't muddied by a partial trailing tick.
        for _ in range(round(2.0 / dt)):
            simulation.step(world, dt)

        assert world.now == pytest.approx(2.0)
        states.append([(p.x, p.y, p.mass) for p in player.pieces])

    reference = states[0]
    for state in states[1:]:
        assert len(state) == len(reference)
        for (x, y, mass), (rx, ry, rmass) in zip(state, reference):
            assert x == pytest.approx(rx, abs=TOLERANCE)
            assert y == pytest.approx(ry, abs=TOLERANCE)
            assert mass == pytest.approx(rmass)
