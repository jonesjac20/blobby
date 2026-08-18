"""Covers the Phase 1 console harness in server/demo.py.

The summary line format is quoted verbatim in the guidebook, so it is pinned
here. The aiohttp tick loop lives in server.loop; centroid and nearest-food
steering stay with the demo until they graduate into the bots.
"""

import asyncio
import logging
import math

import pytest
from conftest import add_piece, add_player

from server import simulation
from server.config import MAX_TICK_SECONDS, TICK_RATE
from server.demo import (
    _summary_line,
    centroid,
    input_toward_nearest_food,
)
from server.loop import measured_dt, next_deadline, sleep_until, tick_loop
from server.models import Food
from server.world import World

# --- centroid -------------------------------------------------------------


def test_centroid_of_a_single_piece_is_its_position(world):
    player = add_player(world, "solo", 100.0, 200.0)

    assert centroid(player) == (100.0, 200.0)


def test_centroid_is_mass_weighted(world):
    player = add_player(world, "two", 200.0, 200.0, mass=300)
    add_piece(world, player, 300.0, 200.0, mass=100)

    # Three times the mass on the left, so the centroid sits a quarter of the way over.
    assert centroid(player) == (225.0, 200.0)


def test_centroid_of_a_player_with_no_pieces_is_the_origin(world):
    """An eaten player reads as (0, 0), which is why the summary shows `pieces=[]`."""
    player = add_player(world, "gone", 500.0, 500.0)
    player.pieces.clear()

    assert centroid(player) == (0.0, 0.0)


# --- nearest-food steering ------------------------------------------------


def test_input_toward_nearest_food_is_a_unit_vector_at_the_closest_pellet(world):
    player = add_player(world, "seeker", 500.0, 500.0)
    world.food["near"] = Food(id="near", x=530.0, y=540.0)
    world.food["far"] = Food(id="far", x=1000.0, y=500.0)

    dx, dy = input_toward_nearest_food(world, player)

    assert math.hypot(dx, dy) == pytest.approx(1.0)
    assert (dx, dy) == pytest.approx((0.6, 0.8))


def test_input_toward_nearest_food_is_zero_when_there_is_no_food(world):
    player = add_player(world, "seeker", 500.0, 500.0)

    assert input_toward_nearest_food(world, player) == (0.0, 0.0)


def test_input_toward_nearest_food_is_zero_when_standing_on_it(world):
    player = add_player(world, "seeker", 500.0, 500.0)
    world.food["under"] = Food(id="under", x=500.0, y=500.0)

    assert input_toward_nearest_food(world, player) == (0.0, 0.0)


def test_input_toward_nearest_food_is_zero_for_a_player_with_no_pieces(world):
    player = add_player(world, "gone", 500.0, 500.0)
    player.pieces.clear()
    world.food["near"] = Food(id="near", x=530.0, y=540.0)

    assert input_toward_nearest_food(world, player) == (0.0, 0.0)


# --- summary line ---------------------------------------------------------


def test_summary_line_matches_the_documented_format(world):
    a = add_player(world, "A", 300.0, 600.0, mass=200)
    b = add_player(world, "B", 900.0, 600.0, mass=200)
    world.food["pellet"] = Food(id="pellet", x=1.0, y=1.0)

    assert _summary_line(world, 30, a, b) == (
        "tick 30 | A pieces=[200] pos=(300,600) | B pieces=[200] pos=(900,600) | food=1"
    )


def test_summary_line_spells_out_the_halves_of_a_split_player(world):
    """A centroid alone cannot show a split, so multi-piece players also get `at=`."""
    a = add_player(world, "A", 300.0, 600.0, mass=100)
    add_piece(world, a, 340.0, 600.0, mass=100)
    b = add_player(world, "B", 900.0, 600.0, mass=200)

    assert _summary_line(world, 90, a, b) == (
        "tick 90 | A pieces=[100,100] pos=(320,600) at=(300,600) (340,600) "
        "| B pieces=[200] pos=(900,600) | food=0"
    )


# --- tick loop ------------------------------------------------------------


def test_next_deadline_advances_by_the_interval():
    interval = 1.0 / TICK_RATE

    assert next_deadline(0.0, 0.001, interval) == pytest.approx(interval)


def test_next_deadline_slips_when_a_tick_overruns():
    interval = 1.0 / TICK_RATE
    now = 0.1

    assert next_deadline(0.0, now, interval) == pytest.approx(now + interval)


def test_measured_dt_is_the_elapsed_interval():
    assert measured_dt(0.05, 0.0) == pytest.approx(0.05)


def test_measured_dt_clamps_a_hitch():
    """A debugger pause must not teleport every blob across the map on the next tick."""
    assert measured_dt(30.0, 0.0) == MAX_TICK_SECONDS


def test_measured_dt_never_runs_the_world_backwards():
    assert measured_dt(0.0, 1.0) == 0.0


def test_tick_loop_advances_sim_time_by_measured_elapsed_not_the_tick_rate():
    """The loop decides *when* step runs; the clock decides *how much* time passed.

    Pinned separately from test_dt_invariance.py, which never calls the loop.
    Here every tick takes four times its budget, so a fixed `1 / TICK_RATE` dt
    would leave world.now at a quarter of the elapsed time.
    """
    overrun = 4.0

    class SlowClock:
        def __init__(self) -> None:
            self.t = 0.0

        def __call__(self) -> float:
            return self.t

    clock = SlowClock()

    async def slow_sleep(seconds: float) -> None:
        clock.t += seconds * overrun

    async def drive() -> World:
        world = World(seed=0, food_target=0)
        stop = asyncio.Event()
        ticks = 0

        async def emit(payload: dict, deaths: list) -> None:
            nonlocal ticks
            ticks += 1
            if ticks == 10:
                stop.set()

        await tick_loop(
            world, [], emit=emit, stop=stop, clock=clock, sleep=slow_sleep
        )
        return world

    world = asyncio.run(drive())

    assert world.now == pytest.approx(clock.t)
    assert world.now > 10.0 / TICK_RATE


def test_tick_loop_clamps_a_hitch_instead_of_teleporting():
    class HitchClock:
        def __init__(self) -> None:
            self.t = 0.0

        def __call__(self) -> float:
            return self.t

    clock = HitchClock()

    async def hitching_sleep(seconds: float) -> None:
        clock.t += 5.0

    async def drive() -> World:
        world = World(seed=0, food_target=0)
        stop = asyncio.Event()

        async def emit(payload: dict, deaths: list) -> None:
            stop.set()

        await tick_loop(
            world, [], emit=emit, stop=stop, clock=clock, sleep=hitching_sleep
        )
        return world

    world = asyncio.run(drive())

    assert world.now == pytest.approx(MAX_TICK_SECONDS)


def test_tick_loop_survives_a_failing_tick(caplog, monkeypatch):
    """One bad tick must not leave HTTP serving a world that stopped moving."""
    caplog.set_level(logging.ERROR, logger="blobby")
    calls = {"n": 0}
    real_step = simulation.step

    def flaky_step(world, dt):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ValueError("boom")
        real_step(world, dt)

    monkeypatch.setattr("server.loop.simulation.step", flaky_step)

    class FakeClock:
        def __init__(self) -> None:
            self.t = 0.0

        def __call__(self) -> float:
            return self.t

    clock = FakeClock()

    async def fake_sleep(seconds: float) -> None:
        clock.t += seconds

    async def drive() -> list[dict]:
        world = World(seed=0, food_target=0)
        stop = asyncio.Event()
        emitted: list[dict] = []

        async def emit(payload: dict, deaths: list) -> None:
            emitted.append(payload)
            if len(emitted) == 3:
                stop.set()

        await tick_loop(
            world, [], emit=emit, stop=stop, clock=clock, sleep=fake_sleep
        )
        return emitted

    emitted = asyncio.run(drive())

    assert calls["n"] == 4
    assert len(emitted) == 3
    assert "boom" in caplog.text


def test_tick_loop_does_not_call_on_tick_ok_when_process_tick_fails(monkeypatch):
    """Production /healthz stamps from this callback; a failed tick must not heartbeat."""
    calls = {"n": 0, "ok": 0}
    real_step = simulation.step

    def flaky_step(world, dt):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ValueError("boom")
        real_step(world, dt)

    monkeypatch.setattr("server.loop.simulation.step", flaky_step)

    class FakeClock:
        def __init__(self) -> None:
            self.t = 0.0

        def __call__(self) -> float:
            return self.t

    clock = FakeClock()

    async def fake_sleep(seconds: float) -> None:
        clock.t += seconds

    async def drive() -> None:
        world = World(seed=0, food_target=0)
        stop = asyncio.Event()
        emitted = 0

        async def emit(payload: dict, deaths: list) -> None:
            nonlocal emitted
            emitted += 1
            if emitted == 3:
                stop.set()

        def on_tick_ok() -> None:
            calls["ok"] += 1

        await tick_loop(
            world,
            [],
            emit=emit,
            stop=stop,
            clock=clock,
            sleep=fake_sleep,
            on_tick_ok=on_tick_ok,
        )

    asyncio.run(drive())

    assert calls["n"] == 4
    assert calls["ok"] == 3


def test_tick_loop_survives_a_failing_broadcast(caplog):
    caplog.set_level(logging.ERROR, logger="blobby")

    class FakeClock:
        def __init__(self) -> None:
            self.t = 0.0

        def __call__(self) -> float:
            return self.t

    clock = FakeClock()

    async def fake_sleep(seconds: float) -> None:
        clock.t += seconds

    async def drive() -> World:
        world = World(seed=0, food_target=0)
        stop = asyncio.Event()
        attempts = 0

        async def emit(payload: dict, deaths: list) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("socket exploded")
            stop.set()

        await tick_loop(
            world, [], emit=emit, stop=stop, clock=clock, sleep=fake_sleep
        )
        return world

    world = asyncio.run(drive())

    assert world.now > 0.0
    assert "socket exploded" in caplog.text


def test_tick_loop_runs_at_configured_rate():
    """Fake clock: over 30s of simulated time, wake count is within 1% of TICK_RATE * elapsed."""

    class FakeClock:
        def __init__(self) -> None:
            self.t = 0.0

        def __call__(self) -> float:
            return self.t

    clock = FakeClock()

    async def fake_sleep(seconds: float) -> None:
        clock.t += seconds

    async def drive() -> int:
        interval = 1.0 / TICK_RATE
        deadline = clock() + interval
        ticks = 0
        end = 30.0
        while True:
            now = await sleep_until(deadline, clock=clock, sleep=fake_sleep)
            ticks += 1
            deadline = next_deadline(deadline, clock(), interval)
            if now >= end:
                break
        return ticks

    ticks = asyncio.run(drive())
    expected = TICK_RATE * 30
    assert abs(ticks - expected) / expected <= 0.01
