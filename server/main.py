"""Phase 1 harness: runs the tick loop with two hardcoded players, no networking.

Player A steers toward the nearest food every tick; player B walks a slow
circle. A is split once at t = 3s so the split -> kick decay -> remerge cycle is
visible in the console summary. In Phase 2 this file becomes the aiohttp
entrypoint.
"""

import asyncio
import math
import time

from server import simulation
from server.config import (
    MAX_TICK_SECONDS,
    SIMULATION_CLOCK_SOURCE,
    TICK_RATE,
    WORLD_HEIGHT,
    WORLD_WIDTH,
)
from server.models import Player
from server.world import World

SPLIT_AT_SECONDS = 3.0
SUMMARY_EVERY_TICKS = 30
CIRCLE_PERIOD_SECONDS = 6.0
# The demo players start already grown. A spawn-sized blob has a radius of only
# ~3.6 units, so it sweeps up food too slowly for mass growth to be legible over
# the ~20s this harness is meant to be watched for.
DEMO_MASS = 200


def centroid(player: Player) -> tuple[float, float]:
    if not player.pieces:
        return 0.0, 0.0
    total = sum(p.mass for p in player.pieces)
    if total == 0:
        return player.pieces[0].x, player.pieces[0].y
    x = sum(p.x * p.mass for p in player.pieces) / total
    y = sum(p.y * p.mass for p in player.pieces) / total
    return x, y

# Handles bot input, calculates the direction this player should move in to reach the nearest food
def input_toward_nearest_food(world: World, player: Player) -> tuple[float, float]:
    if not world.food or not player.pieces:
        return 0.0, 0.0
    cx, cy = centroid(player)
    nearest = min(world.food.values(), key=lambda f: (f.x - cx) ** 2 + (f.y - cy) ** 2)
    dx, dy = nearest.x - cx, nearest.y - cy
    length = math.hypot(dx, dy)
    if length == 0.0:
        return 0.0, 0.0
    return dx / length, dy / length


def _summary_line(world: World, tick: int, a: Player, b: Player) -> str:
    parts = [f"tick {tick}"]
    for player in (a, b):
        masses = ",".join(f"{p.mass:.0f}" for p in player.pieces)
        x, y = centroid(player)
        summary = f"{player.name} pieces=[{masses}] pos=({x:.0f},{y:.0f})"
        if len(player.pieces) > 1:
            # A centroid hides the split kick entirely, so spell the halves out
            # while they are apart.
            spread = " ".join(f"({p.x:.0f},{p.y:.0f})" for p in player.pieces)
            summary += f" at={spread}"
        parts.append(summary)
    parts.append(f"food={len(world.food)}")
    return " | ".join(parts)

# Main game loop
async def run() -> None:
    world = World(seed=time.time_ns())
    # Equal masses and opposite corners of the world, so neither can eat the
    # other and the log stays about movement, splitting and remerging.
    player_a = world.spawn_player("A", WORLD_WIDTH / 4, WORLD_HEIGHT / 2, DEMO_MASS)
    player_b = world.spawn_player("B", WORLD_WIDTH * 3 / 4, WORLD_HEIGHT / 2, DEMO_MASS)
    world.spawn_food_to_target_count()

    tick = 0
    has_split = False
    last = SIMULATION_CLOCK_SOURCE()

    while True:
        await asyncio.sleep(1.0 / TICK_RATE)

        # Sim time advances by measured elapsed time, not by a fixed 1/TICK_RATE,
        # so the timers stay honest when a tick runs long. Phase 2 keeps this.
        now = SIMULATION_CLOCK_SOURCE()
        dt = min(now - last, MAX_TICK_SECONDS)
        last = now
        tick += 1

        player_a.last_input = input_toward_nearest_food(world, player_a)
        angle = 2.0 * math.pi * world.now / CIRCLE_PERIOD_SECONDS
        player_b.last_input = (math.cos(angle), math.sin(angle))

        simulation.step(world, dt)

        if not has_split and world.now >= SPLIT_AT_SECONDS:
            # A's input already points at the nearest food this tick, so the
            # split kick fires that way too.
            simulation.try_split(world, player_a)
            has_split = True

        if tick % SUMMARY_EVERY_TICKS == 0:
            print(_summary_line(world, tick, player_a, player_b))


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
