"""Tick clock: deadline sleep, measured dt, synchronous mutation.

The aiohttp process runs this loop as a task alongside the HTTP server.
Nothing here is a second clock — World.now still only advances in
simulation.step, by an interval the server measured itself.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence

from server import simulation
from server.config import MAX_TICK_SECONDS, SIMULATION_CLOCK_SOURCE, TICK_RATE
from server.protocol import ClientSession, serialize_state, update_and_eliminate
from server.world import World

log = logging.getLogger("blobby")

Emit = Callable[[dict, list[tuple[ClientSession, dict]]], Awaitable[None]]


def next_deadline(previous: float, now: float, interval: float) -> float:
    """When the next tick should wake. Overruns slip rather than bursting."""
    nxt = previous + interval
    if nxt < now:
        return now + interval
    return nxt


def measured_dt(now: float, last: float) -> float:
    """Sim seconds to advance for a tick that woke at `now`, having last run at `last`.

    Deliberately not `1 / TICK_RATE`: the loop reports the time it actually
    took, so a tick that runs long is honest and the simulation never depends
    on the tick rate holding. MAX_TICK_SECONDS caps a hitch so sim time falls
    behind rather than teleporting every blob. The floor is defensive - the
    clock is monotonic, so a negative interval should be impossible, and a
    negative dt would run the world backwards if one ever appeared.
    """
    return min(max(now - last, 0.0), MAX_TICK_SECONDS)


async def sleep_until(
    deadline: float,
    clock=SIMULATION_CLOCK_SOURCE,
    sleep=asyncio.sleep,
) -> float:
    """Sleep until `deadline`. Returns the clock reading after the wait."""
    now = clock()
    delay = deadline - now
    if delay > 0.0:
        await sleep(delay)
        now = clock()
    return now


def process_tick(
    world: World, sessions: Sequence[ClientSession], dt: float
) -> tuple[dict, list[tuple[ClientSession, dict]]]:
    """Advance the world by `dt` and snapshot the broadcast. No await."""
    simulation.step(world, dt)
    deaths = update_and_eliminate(world, sessions)
    return serialize_state(world), deaths


async def tick_loop(
    world: World,
    sessions: list[ClientSession],
    *,
    emit: Emit,
    stop: asyncio.Event,
    clock=SIMULATION_CLOCK_SOURCE,
    sleep=asyncio.sleep,
    on_tick_ok: Callable[[], None] | None = None,
) -> None:
    """Sleep to a 1/TICK_RATE deadline, step, then emit. Slips on overrun.

    A tick that raises is logged and skipped rather than killing the task. The
    alternative is a server whose HTTP and WebSocket endpoints still answer
    while the world silently stopped moving, which looks like a network fault
    and is far harder to diagnose than a traceback in the log. A throw partway
    through `step` can leave the world half-mutated; for a POC that is the
    better trade. `on_tick_ok` runs only after process_tick succeeds, so
    /healthz can age out without treating a failed tick as a heartbeat.
    """
    interval = 1.0 / TICK_RATE
    last = clock()
    deadline = last + interval
    tick_n = 0
    hz_window = last
    while not stop.is_set():
        now = await sleep_until(deadline, clock=clock, sleep=sleep)
        if stop.is_set():
            break
        dt = measured_dt(now, last)
        last = now
        try:
            payload, deaths = process_tick(world, sessions, dt)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("tick failed, skipping to the next one")
            deadline = next_deadline(deadline, clock(), interval)
            continue

        if on_tick_ok is not None:
            on_tick_ok()

        deadline = next_deadline(deadline, clock(), interval)
        try:
            await emit(payload, deaths)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("broadcast failed, skipping to the next tick")

        tick_n += 1
        if tick_n % TICK_RATE == 0:
            wall = clock()
            elapsed = wall - hz_window
            hz = TICK_RATE / elapsed if elapsed > 0.0 else 0.0
            log.info(
                "tick %d players=%d sockets=%d hz=%.1f",
                tick_n,
                len(world.players),
                len(sessions),
                hz,
            )
            hz_window = wall
