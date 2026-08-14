"""Tick clock: deadline sleep, measured dt, synchronous mutation.

The aiohttp process runs this loop as a task alongside the HTTP server.
Nothing here is a second clock — World.now still only advances in
simulation.step, by an interval the server measured itself.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence

from server import simulation
from server.config import MAX_TICK_SECONDS, SIMULATION_CLOCK_SOURCE, TICK_RATE
from server.protocol import ClientSession, serialize_state, update_and_eliminate
from server.world import World

Emit = Callable[[dict, list[tuple[ClientSession, dict]]], Awaitable[None]]


def next_deadline(previous: float, now: float, interval: float) -> float:
    """When the next tick should wake. Overruns slip rather than bursting."""
    nxt = previous + interval
    if nxt < now:
        return now + interval
    return nxt


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
) -> None:
    """Sleep to a 1/TICK_RATE deadline, step, then emit. Slips on overrun."""
    interval = 1.0 / TICK_RATE
    last = clock()
    deadline = last + interval
    while not stop.is_set():
        now = await sleep_until(deadline, clock=clock, sleep=sleep)
        if stop.is_set():
            break
        dt = min(now - last, MAX_TICK_SECONDS)
        last = now
        payload, deaths = process_tick(world, sessions, dt)
        deadline = next_deadline(deadline, clock(), interval)
        await emit(payload, deaths)
