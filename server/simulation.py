"""Per-tick simulation. Pure functions over a World; no state of their own.

step(world, dt) performs, in order: advance sim time -> apply input and kick ->
cluster forces -> resolve collisions and clamp to bounds -> collide with food ->
collide with other players -> decay split velocity -> remerge -> respawn food.

Pieces have real bodies here, and every geometric test is a threshold on
`engulfment` rather than a plain circle touch, so contact and penetration can
mean different things. A player's own pieces rest overlapped at
OWN_PIECE_OVERLAP and are drawn together by the cluster forces; different
players' pieces are solid unless one can eat the other; eating and remerging
each need the deeper EAT_OVERLAP and MERGE_OVERLAP.
"""

import math

from server.config import (
    COHESION_SPEED,
    EAT_OVERLAP,
    EAT_RATIO,
    FOOD_MASS,
    MAX_PIECES,
    MERGE_OVERLAP,
    MERGE_PULL_SPEED,
    MIN_SPLIT_MASS,
    OWN_PIECE_OVERLAP,
    REMERGE_SECONDS,
    SEPARATION_PASSES,
    SPLIT_KICK_DECAY_SECONDS,
    SPLIT_KICK_SPEED,
    speed_for_mass,
)
from server.models import Piece, Player
from server.world import World, clamp_body_position


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
    """Unit vector, or a zero vector for anything unusable.

    Every geometric test below is a threshold comparison, and NaN compares false
    against all of them: a NaN position skips the `distance >= target` bail in
    `_project_apart`, which then writes NaN into whichever piece it was
    separating from, and skips the `engulfment < MERGE_OVERLAP` bail in
    `_remerge_pieces`, which then fuses pieces at any distance. One malformed
    input would spread through the world, so drop it here instead. `hypot`
    carries both NaN and infinite components into `length`, so one check covers
    every case.
    """
    length = math.hypot(dx, dy)
    if not math.isfinite(length) or length == 0.0:
        return 0.0, 0.0
    return dx / length, dy / length


def engulfment(a: Piece, b: Piece) -> float:
    """0.0 when the circles just touch, 1.0 when the smaller sits fully inside.

    Scale-free, so one number serves every geometric test in the simulation: a
    grazing contact and a near-total swallow are far apart on it regardless of
    whether the pieces are spawn-sized or the width of the screen.
    """
    ra, rb = radius_for_mass(a.mass), radius_for_mass(b.mass)
    depth = ra + rb - math.hypot(a.x - b.x, a.y - b.y)
    smaller = min(ra, rb)
    if smaller <= 0.0:
        # A massless piece has no body to sink into, so contact is already total.
        return 1.0 if depth >= 0.0 else 0.0
    return depth / (2.0 * smaller)


def _distance_for_engulfment(a: Piece, b: Piece, overlap: float) -> float:
    """The center distance at which `engulfment(a, b)` would read `overlap`."""
    ra, rb = radius_for_mass(a.mass), radius_for_mass(b.mass)
    return ra + rb - overlap * 2.0 * min(ra, rb)


def _can_eat(a: Piece, b: Piece) -> bool:
    """Whether `a` is heavy enough to eat `b`, ignoring where the two are."""
    return a.mass > b.mass * EAT_RATIO


def _merge_ready(world: World, piece: Piece) -> bool:
    return world.now - piece.split_time >= REMERGE_SECONDS


def _kick_active_during_tick(previous_now: float, piece: Piece) -> bool:
    """Whether this piece's split kick contributes displacement this tick.

    Measured at the start of the tick to match `_kick_integral`, which covers
    [previous_now, world.now]. The tick that consumes the last sliver of the kick
    counts, so the cluster forces never fight the kick even at its final tick.
    """
    return previous_now - piece.split_time < SPLIT_KICK_DECAY_SECONDS


def step(world: World, dt: float) -> None:
    previous_now = world.now
    world.now += dt

    previous_positions = {
        piece.piece_id: (piece.x, piece.y)
        for player in world.players.values()
        for piece in player.pieces
    }

    _apply_input_and_move(world, previous_now, dt)
    _cluster_forces(world, previous_now, dt)
    _resolve_collisions(world)
    _eat_food(world, previous_positions)
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


def _cluster_forces(world: World, previous_now: float, dt: float) -> None:
    """Draw each player's own pieces together: cohesion, then the merge pull.

    Deliberately position-level and never written to `vx/vy`, so those fields
    keep meaning exactly "split kick" for the wire format and the debug arrows.

    Runs before `_resolve_collisions` so the projection always has the last word.
    That is what pins a settled pair to exactly OWN_PIECE_OVERLAP at any tick
    rate: cohesion may overshoot, and the projection corrects it the same way
    every time.
    """
    for player in world.players.values():
        pieces = player.pieces
        if len(pieces) < 2:
            continue

        pulls = [[0.0, 0.0] for _ in pieces]
        # Per piece, because one pair may be merging while another only coheres.
        caps = [0.0] * len(pieces)

        for i, a in enumerate(pieces):
            a_ready = _merge_ready(world, a)
            a_kicking = _kick_active_during_tick(previous_now, a)
            for j in range(i + 1, len(pieces)):
                b = pieces[j]
                if a_ready and _merge_ready(world, b):
                    speed = MERGE_PULL_SPEED
                    # All the way in; the remerge fires at MERGE_OVERLAP long
                    # before the two centers actually meet.
                    target = 0.0
                elif a_kicking or _kick_active_during_tick(previous_now, b):
                    continue
                else:
                    speed = COHESION_SPEED
                    target = _distance_for_engulfment(a, b, OWN_PIECE_OVERLAP)

                dx, dy = b.x - a.x, b.y - a.y
                distance = math.hypot(dx, dy)
                gap = distance - target
                if distance == 0.0 or gap <= 0.0:
                    continue

                # Halved because both pieces close on each other, and capped at
                # the gap so the pair lands on the target instead of oscillating
                # around it.
                move = min(speed * dt, gap / 2.0)
                ux, uy = dx / distance, dy / distance
                pulls[i][0] += ux * move
                pulls[i][1] += uy * move
                pulls[j][0] -= ux * move
                pulls[j][1] -= uy * move
                caps[i] = max(caps[i], speed * dt)
                caps[j] = max(caps[j], speed * dt)

        for piece, (dx, dy), cap in zip(pieces, pulls, caps):
            length = math.hypot(dx, dy)
            if length == 0.0:
                continue
            # A piece in an eight-way cluster is pulled by seven neighbours; the
            # cap stops it from travelling seven times as fast as a lone pair.
            if length > cap:
                dx, dy = dx * cap / length, dy * cap / length
            piece.x += dx
            piece.y += dy


def _resolve_collisions(world: World) -> None:
    """Push overlapping bodies apart, then clamp every disc into the world.

    Position projection rather than impulses: it carries no dt, so a settled
    configuration is identical at every tick rate.

    The clamp insets each center by its radius, so a piece cannot sit with
    half its body hanging outside the arena. A piece crushed into a corner by
    that clamp may keep some residual overlap. Bounds win over separation,
    which is the right way round.
    """
    bodies = [
        (player.id, piece)
        for player in world.players.values()
        for piece in player.pieces
    ]

    for _ in range(SEPARATION_PASSES):
        for i, (owner_a, a) in enumerate(bodies):
            for j in range(i + 1, len(bodies)):
                owner_b, b = bodies[j]
                if owner_a == owner_b:
                    # A mergeable pair is trying to sink into each other.
                    if _merge_ready(world, a) and _merge_ready(world, b):
                        continue
                    target = _distance_for_engulfment(a, b, OWN_PIECE_OVERLAP)
                elif _can_eat(a, b) or _can_eat(b, a):
                    # Never projected, or the predator could never reach the
                    # EAT_OVERLAP depth that `_eat_other_players` waits for.
                    continue
                else:
                    target = _distance_for_engulfment(a, b, 0.0)

                _project_apart(a, b, target, j)

    for _, piece in bodies:
        piece.x, piece.y = clamp_body_position(piece.x, piece.y, piece.mass)


def _project_apart(a: Piece, b: Piece, target: float, fallback_index: int) -> None:
    """Separate to `target` center distance, moving the lighter piece further."""
    dx, dy = b.x - a.x, b.y - a.y
    distance = math.hypot(dx, dy)
    if distance >= target:
        return

    if distance == 0.0:
        # Coincident centers, as an exact zero-input split produces, have no
        # separation axis. Deriving one from the piece's slot in the world keeps
        # it reproducible, which a random or hash-derived angle would not be.
        angle = 2.0 * math.pi * fallback_index / MAX_PIECES
        ux, uy = math.cos(angle), math.sin(angle)
    else:
        ux, uy = dx / distance, dy / distance

    push = target - distance
    total = a.mass + b.mass
    a_share = push * (b.mass / total) if total > 0.0 else push / 2.0
    b_share = push - a_share
    a.x -= ux * a_share
    a.y -= uy * a_share
    b.x += ux * b_share
    b.y += uy * b_share


def _distance_point_to_segment(
    px: float, py: float, ax: float, ay: float, bx: float, by: float
) -> float:
    """Distance from point (px, py) to the closest point on segment AB."""
    abx, aby = bx - ax, by - ay
    apx, apy = px - ax, py - ay
    ab2 = abx * abx + aby * aby
    if ab2 == 0.0:
        return math.hypot(apx, apy)
    t = max(0.0, min(1.0, (apx * abx + apy * aby) / ab2))
    return math.hypot(px - (ax + t * abx), py - (ay + t * aby))


def _eat_food(
    world: World, previous_positions: dict[str, tuple[float, float]]
) -> None:
    """Eat pellets whose center the piece's disc covered at any point this tick.

    A light fragment can travel more than its own diameter in one tick, so
    sampling only the post-move center would skip food sitting on the path.
    The segment is start-of-tick to post-clamp; a stationary piece degenerates
    to the original point test.
    """
    eaten: set[str] = set()
    for player in world.players.values():
        for piece in player.pieces:
            radius = radius_for_mass(piece.mass)
            start = previous_positions.get(piece.piece_id, (piece.x, piece.y))
            for food in world.food.values():
                if food.id in eaten:
                    continue
                if (
                    _distance_point_to_segment(
                        food.x, food.y, start[0], start[1], piece.x, piece.y
                    )
                    <= radius
                ):
                    piece.mass += FOOD_MASS
                    radius = radius_for_mass(piece.mass)
                    eaten.add(food.id)
    for food_id in eaten:
        del world.food[food_id]


def _eat_other_players(world: World) -> None:
    """Cross-player piece eating. A player's own pieces never eat each other.

    Touching is not enough: `_resolve_collisions` leaves an edible pair free to
    interpenetrate, and the kill only lands once the prey's center has reached
    the predator's rim at EAT_OVERLAP. A graze is a collision.
    """
    eaten: set[str] = set()
    players = list(world.players.values())
    for index, attacker in enumerate(players):
        for defender in players[index + 1 :]:
            for a in attacker.pieces:
                if a.piece_id in eaten:
                    continue
                for b in defender.pieces:
                    if b.piece_id in eaten or engulfment(a, b) < EAT_OVERLAP:
                        continue
                    if _can_eat(a, b):
                        a.mass += b.mass
                        eaten.add(b.piece_id)
                    elif _can_eat(b, a):
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
    """Fuse same-player pieces that have both timed out and sunk far enough in.

    MERGE_OVERLAP sits past the OWN_PIECE_OVERLAP a cluster rests at, so a pair
    cannot merge just by being in contact when the timer clears; the merge pull
    in `_cluster_forces` has to drag them the rest of the way.
    """
    for player in world.players.values():
        merged = True
        while merged:
            merged = False
            for i in range(len(player.pieces)):
                for j in range(i + 1, len(player.pieces)):
                    a, b = player.pieces[i], player.pieces[j]
                    if not _merge_ready(world, a) or not _merge_ready(world, b):
                        continue
                    if engulfment(a, b) < MERGE_OVERLAP:
                        continue

                    total = a.mass + b.mass
                    if total <= 0.0:
                        continue
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


def try_split(world: World, player: Player) -> int:
    """Split every eligible piece along `player.last_input`. Returns pieces created.

    The client's split message carries no direction (build plan section 4), so
    the stored input is the only thing that can aim the kick. Zero input still
    splits, just without a kick: the halves land on each other and the separation
    pass in `_resolve_collisions` pushes them apart on the next tick.

    Largest pieces split first, so when MAX_PIECES limits how many splits fit,
    the biggest blobs are the ones that break apart.
    """
    unit_x, unit_y = _normalized(*player.last_input)

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
