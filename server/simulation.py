"""Per-tick simulation. Pure functions over a World; no state of their own.

step(world, dt) performs, in order: advance sim time -> apply input -> move ->
collide with food -> collide with other players -> decay split velocity ->
remerge -> respawn food.
"""

import math

from server.config import (
    EAT_RATIO,
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
from server.models import Piece, Player
from server.world import World


def radius_for_mass(mass: float) -> float:
    return math.sqrt(max(mass, 0.0) / math.pi)


def _kick_envelope(elapsed: float) -> float:
    """Fraction of the initial split kick still active `elapsed` seconds in."""
    if elapsed <= 0.0:
        return 1.0
    if elapsed >= SPLIT_KICK_DECAY_SECONDS:
        return 0.0
    return 1.0 - elapsed / SPLIT_KICK_DECAY_SECONDS


def _kick_integral(start_elapsed: float, end_elapsed: float) -> float:
    """Integral of the kick envelope over [start_elapsed, end_elapsed], in seconds.

    Integrating the envelope analytically rather than multiplying the
    instantaneous velocity by dt is what makes total kick displacement identical
    at every tick rate: consecutive intervals telescope exactly.
    """
    decay = SPLIT_KICK_DECAY_SECONDS
    a = min(max(start_elapsed, 0.0), decay)
    b = min(max(end_elapsed, 0.0), decay)
    if b <= a:
        return 0.0
    return (b - b * b / (2.0 * decay)) - (a - a * a / (2.0 * decay))


def _normalized(dx: float, dy: float) -> tuple[float, float]:
    length = math.hypot(dx, dy)
    if length == 0.0:
        return 0.0, 0.0
    return dx / length, dy / length


def _overlaps(a: Piece, b: Piece) -> bool:
    return math.hypot(a.x - b.x, a.y - b.y) < radius_for_mass(a.mass) + radius_for_mass(
        b.mass
    )


def step(world: World, dt: float) -> None:
    previous_now = world.now
    world.now += dt

    _apply_input_and_move(world, previous_now, dt)
    _eat_food(world)
    _eat_other_players(world)
    _decay_split_kicks(world)
    _remerge_pieces(world)
    world.spawn_food_to_target_count()


def _apply_input_and_move(world: World, previous_now: float, dt: float) -> None:
    for player in world.players.values():
        input_x, input_y = _normalized(*player.last_input)
        for piece in player.pieces:
            speed = speed_for_mass(piece.mass)
            kick_seconds = _kick_integral(
                previous_now - piece.split_time, world.now - piece.split_time
            )

            piece.x += input_x * speed * dt + piece.initial_kick_vx * kick_seconds
            piece.y += input_y * speed * dt + piece.initial_kick_vy * kick_seconds

            piece.x = min(max(piece.x, 0.0), WORLD_WIDTH)
            piece.y = min(max(piece.y, 0.0), WORLD_HEIGHT)


def _eat_food(world: World) -> None:
    eaten: set[str] = set()
    for player in world.players.values():
        for piece in player.pieces:
            radius = radius_for_mass(piece.mass)
            for food in world.food.values():
                if food.id in eaten:
                    continue
                if math.hypot(piece.x - food.x, piece.y - food.y) <= radius:
                    piece.mass += FOOD_MASS
                    radius = radius_for_mass(piece.mass)
                    eaten.add(food.id)
    for food_id in eaten:
        del world.food[food_id]


def _eat_other_players(world: World) -> None:
    """Cross-player piece eating. A player's own pieces never eat each other."""
    eaten: set[str] = set()
    players = list(world.players.values())
    for index, attacker in enumerate(players):
        for defender in players[index + 1 :]:
            for a in attacker.pieces:
                if a.piece_id in eaten:
                    continue
                for b in defender.pieces:
                    if b.piece_id in eaten or not _overlaps(a, b):
                        continue
                    if a.mass > b.mass * EAT_RATIO:
                        a.mass += b.mass
                        eaten.add(b.piece_id)
                    elif b.mass > a.mass * EAT_RATIO:
                        b.mass += a.mass
                        eaten.add(a.piece_id)
                        break

    if eaten:
        for player in players:
            player.pieces = [p for p in player.pieces if p.piece_id not in eaten]


def _decay_split_kicks(world: World) -> None:
    for player in world.players.values():
        for piece in player.pieces:
            remaining = _kick_envelope(world.now - piece.split_time)
            piece.vx = piece.initial_kick_vx * remaining
            piece.vy = piece.initial_kick_vy * remaining


def _remerge_pieces(world: World) -> None:
    for player in world.players.values():
        merged = True
        while merged:
            merged = False
            for i in range(len(player.pieces)):
                for j in range(i + 1, len(player.pieces)):
                    a, b = player.pieces[i], player.pieces[j]
                    if world.now - a.split_time < REMERGE_SECONDS:
                        continue
                    if world.now - b.split_time < REMERGE_SECONDS:
                        continue
                    if not _overlaps(a, b):
                        continue

                    total = a.mass + b.mass
                    a.x = (a.x * a.mass + b.x * b.mass) / total
                    a.y = (a.y * a.mass + b.y * b.mass) / total
                    a.mass = total
                    a.split_time = min(a.split_time, b.split_time)
                    a.vx = a.vy = 0.0
                    a.initial_kick_vx = a.initial_kick_vy = 0.0
                    del player.pieces[j]
                    merged = True
                    break
                if merged:
                    break


def try_split(
    world: World, player: Player, cursor_dx: float, cursor_dy: float
) -> int:
    """Split every eligible piece toward the cursor. Returns pieces created.

    Largest pieces split first, so when MAX_PIECES limits how many splits fit,
    the biggest blobs are the ones that break apart.
    """
    unit_x, unit_y = _normalized(cursor_dx, cursor_dy)
    if unit_x == 0.0 and unit_y == 0.0:
        return 0

    created = 0
    for parent in sorted(player.pieces, key=lambda p: p.mass, reverse=True):
        if len(player.pieces) >= MAX_PIECES:
            break
        if parent.mass < MIN_SPLIT_MASS:
            continue

        half = parent.mass / 2.0
        parent.mass = half
        parent.split_time = world.now
        # A leftover kick from an earlier split would be revived by the
        # split_time reset above, so clear it.
        parent.vx = parent.vy = 0.0
        parent.initial_kick_vx = parent.initial_kick_vy = 0.0

        player.pieces.append(
            Piece(
                piece_id=world.new_id(),
                x=parent.x,
                y=parent.y,
                mass=half,
                vx=SPLIT_KICK_SPEED * unit_x,
                vy=SPLIT_KICK_SPEED * unit_y,
                initial_kick_vx=SPLIT_KICK_SPEED * unit_x,
                initial_kick_vy=SPLIT_KICK_SPEED * unit_y,
                split_time=world.now,
            )
        )
        created += 1

    return created
