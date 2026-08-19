"""Pure `decide(view, memory)` brain. No sockets, no wall clock.

See docs/bot-logic.md. Hard numbers come from server.config; named feel
parameters live here and are retuned after play.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from server.config import (
    EAT_OVERLAP,
    EAT_RATIO,
    INITIAL_PLAYER_MASS,
    MAX_PIECES,
    MIN_SPLIT_MASS,
    SPLIT_KICK_DECAY_SECONDS,
    WORLD_HEIGHT,
    WORLD_WIDTH,
    split_kick_speed,
)
from server.simulation import radius_for_mass

GRAZE_CELL = 100.0
VISION_BASE_SPAN = 420.0
VISION_EDGE_HYSTERESIS = 20.0
APPROACHING_SPEED = 15.0
STATE_DWELL_SECONDS = 0.4
FLEE_MEMORY_SECONDS = 1.5
PUNISH_REMERGE_FLOOR = 3.0
FLEE_PANIC_RADII = 2.0
WALL_MARGIN = 80.0
WANDER_ARRIVE = 24.0

STATE_FLEE = "flee"
STATE_RECOVER = "recover"
STATE_HUNT = "hunt"
STATE_GRAZE = "graze"
PRIORITY = {
    STATE_FLEE: 3,
    STATE_RECOVER: 2,
    STATE_HUNT: 1,
    STATE_GRAZE: 0,
}

KIND_PREY = "prey"
KIND_THREAT = "threat"
KIND_PEER = "peer"


@dataclass(frozen=True)
class Personality:
    vision_scale: float = 1.0
    hunt_range: float = 0.85
    split_willingness: float = 1.0
    flee_padding: float = 0.0


# Cycle these so `--count 24` is not a clone army.
PERSONALITIES: tuple[Personality, ...] = (
    Personality(1.0, 0.85, 1.0, 0.0),
    Personality(1.2, 0.7, 0.0, 30.0),
    Personality(0.9, 1.0, 1.0, 0.0),
    Personality(1.1, 0.8, 0.5, 15.0),
    Personality(1.0, 0.6, 1.0, 10.0),
)


@dataclass
class Memory:
    state: str = STATE_GRAZE
    ticks_in_state: int = 0
    graze_target: tuple[float, float] | None = None
    wander_waypoint: tuple[float, float] | None = None
    vision_ids: set[str] = field(default_factory=set)
    last_threat: tuple[float, float] | None = None
    last_threat_ticks: int = 0
    rng: random.Random = field(default_factory=random.Random)


def new_memory(seed: int | None = None) -> Memory:
    return Memory(rng=random.Random(seed))


class FoodIndex:
    """100×100 graze buckets. Rebuilt when the food version changes, once per process."""

    def __init__(self) -> None:
        self.version: object = None
        self.cells: dict[tuple[int, int], list[tuple[float, float]]] = {}
        self.pellets: list[tuple[float, float]] = []
        self.rebuilds: int = 0

    def update(self, version: object, pellets: list) -> None:
        if version == self.version:
            return
        self.version = version
        self.rebuilds += 1
        self.pellets = []
        self.cells = {}
        for item in pellets:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                x, y = float(item[0]), float(item[1])
            else:
                x, y = float(item["x"]), float(item["y"])
            self.pellets.append((x, y))
            key = (int(math.floor(x / GRAZE_CELL)), int(math.floor(y / GRAZE_CELL)))
            self.cells.setdefault(key, []).append((x, y))

    def neighborhood(self, x: float, y: float) -> list[tuple[float, float]]:
        cx = int(math.floor(x / GRAZE_CELL))
        cy = int(math.floor(y / GRAZE_CELL))
        out: list[tuple[float, float]] = []
        # Check the cells in the 3x3 grid centered on the given coordinates (cx, cy)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                out.extend(self.cells.get((cx + dx, cy + dy), ()))
        return out


def _normalized(dx: float, dy: float) -> tuple[float, float]:
    # Normalize the direction vector (dx, dy) to a unit vector (i.e., a vector of length 1)
    length = math.hypot(dx, dy)
    if not math.isfinite(length) or length == 0.0:
        return 0.0, 0.0
    return dx / length, dy / length


def _centroid(pieces: list[dict]) -> tuple[float, float]:
    # Calculate the centroid of the given pieces (i.e., the center of mass of the pieces)
    total = sum(piece["mass"] for piece in pieces)
    if total <= 0:
        return pieces[0]["x"], pieces[0]["y"]
    x = sum(piece["x"] * piece["mass"] for piece in pieces) / total
    y = sum(piece["y"] * piece["mass"] for piece in pieces) / total
    return x, y

def vision_radius(
    total_mass: float,
    personality: Personality,
    initial_mass: float = INITIAL_PLAYER_MASS,
) -> float:
    # Calculate the vision radius based on the total mass of the pieces
    mass = max(total_mass, initial_mass)
    span = VISION_BASE_SPAN * (mass / initial_mass) ** 0.25
    return 0.5 * span * personality.vision_scale


def classify_piece(
    our_best: float,
    our_weakest: float,
    piece_mass: float,
    protected: bool,
    inert: bool = False,
) -> str:
    """Threat wins if both labels could apply (split body vs one foreign disc).

    Inert corpses cannot eat, so they are never a threat — only prey or peer.
    """
    if inert:
        if our_best > piece_mass * EAT_RATIO:
            return KIND_PREY
        return KIND_PEER
    if piece_mass > our_weakest * EAT_RATIO:
        return KIND_THREAT
    if (not protected) and our_best > piece_mass * EAT_RATIO:
        return KIND_PREY
    return KIND_PEER


def _piece_velocity(
    piece: dict, prev_positions: dict[str, tuple[float, float]], dt: float
) -> tuple[float, float]:
    prev = prev_positions.get(piece["piece_id"])
    if prev is None or dt <= 0.0:
        return 0.0, 0.0
    return (piece["x"] - prev[0]) / dt, (piece["y"] - prev[1]) / dt


def closing_speed(
    ox: float,
    oy: float,
    px: float,
    py: float,
    vx: float,
    vy: float,
    ovx: float = 0.0,
    ovy: float = 0.0,
) -> float:
    """Positive when they are moving toward us."""
    dx, dy = px - ox, py - oy
    dist = math.hypot(dx, dy)
    if dist <= 1e-9:
        return 0.0
    rvx, rvy = vx - ovx, vy - ovy
    return -(dx * rvx + dy * rvy) / dist


def _eat_distance(mass_a: float, mass_b: float) -> float:
    """Center distance at which engulfment reads EAT_OVERLAP."""
    ra, rb = radius_for_mass(mass_a), radius_for_mass(mass_b)
    return ra + rb - EAT_OVERLAP * 2.0 * min(ra, rb)


def _wall_repulsion(
    x: float, y: float, width: float, height: float, margin: float = WALL_MARGIN
) -> tuple[float, float]:
    dx = dy = 0.0
    if x < margin:
        dx += margin - x
    if x > width - margin:
        dx -= x - (width - margin)
    if y < margin:
        dy += margin - y
    if y > height - margin:
        dy -= y - (height - margin)
    return dx, dy


def _fallback_steer(
    dx: float, dy: float, wall_x: float = 0.0, wall_y: float = 0.0
) -> tuple[float, float]:
    """Overlapping a body is not 'nothing to steer toward'."""
    if dx == 0.0 and dy == 0.0:
        return (wall_x or 1.0), wall_y
    return dx, dy


def _shield_repulsion(
    cx: float, cy: float, foreign: list[dict], our_best: float
) -> tuple[float, float]:
    """Steer off spawn-protected meals so we do not sit on the shield."""
    dx = dy = 0.0
    for item in foreign:
        if not item.get("protected"):
            continue
        if not (our_best > item["mass"] * EAT_RATIO):
            continue
        away_x = cx - item["x"]
        away_y = cy - item["y"]
        dist = item["dist"]
        keep = radius_for_mass(item["mass"]) + radius_for_mass(our_best)
        if dist < 1e-9:
            dx += keep
            continue
        if dist < keep:
            strength = keep - dist
            dx += (away_x / dist) * strength
            dy += (away_y / dist) * strength
    return dx, dy


def _kick_hits_wall(
    x: float,
    y: float,
    dx: float,
    dy: float,
    displacement: float,
    width: float,
    height: float,
    radius: float,
) -> bool:
    ux, uy = _normalized(dx, dy)
    if ux == 0.0 and uy == 0.0:
        return True
    nx = x + ux * displacement
    ny = y + uy * displacement
    return (
        nx < radius
        or ny < radius
        or nx > width - radius
        or ny > height - radius
    )


def _is_trapped(
    px: float,
    py: float,
    cx: float,
    cy: float,
    width: float,
    height: float,
    margin: float = WALL_MARGIN,
) -> bool:
    near_left = px < margin
    near_right = px > width - margin
    near_top = py < margin
    near_bot = py > height - margin
    if not (near_left or near_right or near_top or near_bot):
        return False
    if near_left and cx > px:
        return True
    if near_right and cx < px:
        return True
    if near_top and cy > py:
        return True
    if near_bot and cy < py:
        return True
    return False


def _kick_displacement(parent_mass: float) -> float:
    return split_kick_speed(parent_mass) * SPLIT_KICK_DECAY_SECONDS / 2.0


def _lunge_hitter(ours: list[dict], prey: dict) -> dict | None:
    eligible = [piece for piece in ours if piece["mass"] >= MIN_SPLIT_MASS]
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda piece: (piece["x"] - prey["x"]) ** 2 + (piece["y"] - prey["y"]) ** 2,
    )


def split_lunge_ok(
    ours: list[dict],
    prey: dict,
    threats: list[dict],
    personality: Personality,
    *,
    protected: bool,
    in_recover: bool,
    vision_r: float,
    cx: float,
    cy: float,
) -> bool:
    del vision_r, cx, cy
    if personality.split_willingness <= 0.0 or protected:
        return False
    if in_recover:
        return False
    if len(ours) >= MAX_PIECES:
        return False
    if prey.get("protected"):
        return False
    hitter = _lunge_hitter(ours, prey)
    if hitter is None:
        return False
    half = hitter["mass"] / 2.0
    if not (half > prey["mass"] * EAT_RATIO):
        return False
    eligible = [piece for piece in ours if piece["mass"] >= MIN_SPLIT_MASS]
    halves = [piece["mass"] / 2.0 for piece in eligible]
    if len(eligible) > 1:
        for half_mass in halves:
            for threat in threats:
                if threat["mass"] > half_mass * EAT_RATIO:
                    return False
    else:
        for threat in threats:
            if threat["mass"] > half * EAT_RATIO:
                return False
    dist = math.hypot(hitter["x"] - prey["x"], hitter["y"] - prey["y"])
    need = max(0.0, dist - radius_for_mass(half))
    return need <= _kick_displacement(hitter["mass"])


def sacrifice_ok(
    ours: list[dict],
    threats: list[dict],
    prev_positions: dict[str, tuple[float, float]],
    dt: float,
    personality: Personality,
    width: float,
    height: float,
    flee_dx: float,
    flee_dy: float,
    ovx: float,
    ovy: float,
) -> bool:
    if personality.split_willingness <= 0.0:
        return False
    if len(ours) != 1:
        return False
    piece = ours[0]
    if piece["mass"] < MIN_SPLIT_MASS:
        return False
    eaters = [
        threat
        for threat in threats
        if threat["mass"] > piece["mass"] * EAT_RATIO
    ]
    if not eaters:
        return False
    imminent = False
    for threat in eaters:
        vx, vy = _piece_velocity(threat, prev_positions, dt)
        dist = math.hypot(threat["x"] - piece["x"], threat["y"] - piece["y"])
        eat_at = _eat_distance(threat["mass"], piece["mass"])
        if dist <= eat_at:
            imminent = True
            break
        closing = closing_speed(
            piece["x"], piece["y"], threat["x"], threat["y"], vx, vy, ovx, ovy
        )
        gap = dist - eat_at
        if closing > 0.0 and gap / closing <= 1.0:
            imminent = True
            break
    if not imminent:
        return False
    disp = _kick_displacement(piece["mass"])
    child_r = radius_for_mass(piece["mass"] / 2.0)
    if _kick_hits_wall(
        piece["x"], piece["y"], flee_dx, flee_dy, disp, width, height, child_r
    ):
        return False
    return True


def _pick_graze_target(
    cx: float,
    cy: float,
    index: FoodIndex,
    memory: Memory,
) -> tuple[float, float] | None:
    local = index.neighborhood(cx, cy)
    if not local:
        memory.graze_target = None
        return None
    current = memory.graze_target
    if current is not None:
        for pellet in local:
            if abs(pellet[0] - current[0]) < 1.0 and abs(pellet[1] - current[1]) < 1.0:
                if math.hypot(pellet[0] - cx, pellet[1] - cy) > 1e-6:
                    return pellet
                break
    nearest = min(local, key=lambda pellet: (pellet[0] - cx) ** 2 + (pellet[1] - cy) ** 2)
    if math.hypot(nearest[0] - cx, nearest[1] - cy) <= 1e-6:
        others = [p for p in local if p is not nearest]
        if not others:
            memory.graze_target = None
            return None
        nearest = min(
            others, key=lambda pellet: (pellet[0] - cx) ** 2 + (pellet[1] - cy) ** 2
        )
    memory.graze_target = nearest
    return nearest


def _wander(
    cx: float,
    cy: float,
    width: float,
    height: float,
    radius: float,
    memory: Memory,
) -> tuple[float, float]:
    waypoint = memory.wander_waypoint
    if waypoint is None or math.hypot(waypoint[0] - cx, waypoint[1] - cy) < WANDER_ARRIVE:
        inset = 40.0
        span = max(radius, 80.0)
        wx = memory.rng.uniform(max(inset, cx - span), min(width - inset, cx + span))
        wy = memory.rng.uniform(max(inset, cy - span), min(height - inset, cy + span))
        waypoint = (wx, wy)
        memory.wander_waypoint = waypoint
    return waypoint[0] - cx, waypoint[1] - cy


def decide(view: dict, memory: Memory) -> tuple[float, float, bool]:
    """Return (dx, dy, split). Mutates `memory`. No sockets, no wall-clock."""
    self_id = view.get("self_id")
    players = view.get("players") or []
    me = next((player for player in players if player.get("id") == self_id), None)
    if me is None or not me.get("pieces"):
        return 0.0, 0.0, False

    ours = me["pieces"]
    cx, cy = _centroid(ours)
    total_mass = sum(piece["mass"] for piece in ours)
    our_best = max(piece["mass"] for piece in ours)
    our_weakest = min(piece["mass"] for piece in ours)
    protected = bool(me.get("protected"))
    tick_rate = float(view.get("tick_rate") or 30)
    dt = 1.0 / tick_rate if tick_rate > 0.0 else 1.0 / 30.0
    width = float(view.get("world_width") or WORLD_WIDTH)
    height = float(view.get("world_height") or WORLD_HEIGHT)
    initial_mass = float(view.get("initial_player_mass") or INITIAL_PLAYER_MASS)
    personality: Personality = view.get("personality") or Personality()
    prev_positions: dict[str, tuple[float, float]] = view.get("prev_positions") or {}
    food_index: FoodIndex = view.get("food_index") or FoodIndex()

    vis_r = vision_radius(total_mass, personality, initial_mass)
    prev_centroid = view.get("prev_centroid")
    # ovx and ovy are the velocity of the bot's centroid between the current and previous tick
    if prev_centroid is None:
        ovx = ovy = 0.0
    else:
        ovx = (cx - prev_centroid[0]) / dt
        ovy = (cy - prev_centroid[1]) / dt

    seen: set[str] = set()
    foreign: list[dict] = []
    for player in players:
        if player.get("id") == self_id:
            continue
        owner_protected = bool(player.get("protected"))
        owner_inert = bool(player.get("inert"))
        for piece in player.get("pieces") or []:
            dist = math.hypot(piece["x"] - cx, piece["y"] - cy)

            # was is a boolean indicating if the piece seen was already seen in a previous tick
            was = piece["piece_id"] in memory.vision_ids
            if not (
                dist <= vis_r + VISION_EDGE_HYSTERESIS
                if was
                else dist <= vis_r
            ):
                # If the piece is not within the vision radius, skip it
                continue
            # Add the piece to the set of seen pieces
            seen.add(piece["piece_id"])
            # Calculate the velocity of the piece
            vx, vy = _piece_velocity(piece, prev_positions, dt)
            # Classify the piece as a threat, prey, or neutral
            kind = classify_piece(
                our_best,
                our_weakest,
                piece["mass"],
                owner_protected,
                owner_inert,
            )
            # Calculate the approaching speed of the foreign piece
            closing = closing_speed(cx, cy, piece["x"], piece["y"], vx, vy, ovx, ovy)
            foreign.append(
                {
                    **piece,
                    "owner_id": player["id"],
                    "protected": owner_protected,
                    "inert": owner_inert,
                    "kind": kind,
                    "closing": closing,
                    "dist": dist,
                    "remerge_in": float(piece.get("remerge_in") or 0.0),
                    "vx": vx,
                    "vy": vy,
                }
            )
    memory.vision_ids = seen

    # Separate the foreign pieces into threats and prey
    threats = [item for item in foreign if item["kind"] == KIND_THREAT]
    prey = [item for item in foreign if item["kind"] == KIND_PREY]
    by_owner: dict[str, list[dict]] = {}
    for item in foreign:
        by_owner.setdefault(item["owner_id"], []).append(item)
    # mixed_cluster is a boolean indicating if there are both threats and prey in the foreign pieces
    mixed_cluster = any(
        any(part["kind"] == KIND_THREAT for part in parts)
        and any(part["kind"] == KIND_PREY for part in parts)
        for parts in by_owner.values()
    )
    panic_r = (
        FLEE_PANIC_RADII * radius_for_mass(total_mass) + personality.flee_padding
    )

    live_threat = None
    for item in threats:
        in_reach = item["dist"] <= panic_r + radius_for_mass(item["mass"])
        dangerous = (
            protected
            or mixed_cluster
            or item["closing"] > APPROACHING_SPEED
            or in_reach
        )
        if dangerous:
            if live_threat is None or item["mass"] > live_threat["mass"]:
                live_threat = item

    # live_threat is the closest threat to the bot's centroid (i.e., the most dangerous)
    if live_threat is not None:
        memory.last_threat = (live_threat["x"], live_threat["y"])
        memory.last_threat_ticks = 0
    elif memory.last_threat is not None:
        memory.last_threat_ticks += 1

    flee_ticks = FLEE_MEMORY_SECONDS * tick_rate
    # ghost_alive is a boolean indicating if the last threat is still alive (i.e., it has not been seen in the last FLEE_MEMORY_SECONDS ticks)
    ghost_alive = (
        memory.last_threat is not None and memory.last_threat_ticks < flee_ticks
    )
    # If the last threat is not alive, reset the last threat and the last threat ticks
    if not ghost_alive:
        memory.last_threat = None
        memory.last_threat_ticks = 0

    # If there is prey in vision (a split predator we can eat) and there are no threats, drop the flee ghost.
    # Mixed clusters still have a live threat and stay in Flee mode.
    if prey and not threats:
        memory.last_threat = None
        ghost_alive = False

    # want_flee is a boolean indicating if the bot wants to flee (i.e., if there is a live threat or a ghost threat)
    want_flee = live_threat is not None or ghost_alive

    max_remerge = max((piece.get("remerge_in") or 0.0) for piece in ours)
    recovering_cluster = len(ours) > 1 and max_remerge > 0.0

    def _split_prey_ready(item: dict) -> bool:
        # Inert never remelts. Eat any fragment we can; ignore sibling mass.
        if item.get("inert"):
            return True
        # parts is a list of pieces owned by the same player as the given item
        parts = by_owner.get(item["owner_id"], [item])
        if len(parts) <= 1:
            return True
        # fused is the total mass of the pieces owned by the same player as the given item
        fused = sum(part["mass"] for part in parts)
        if fused <= our_weakest * EAT_RATIO:
            return True
        return max(part["remerge_in"] for part in parts) > PUNISH_REMERGE_FLOOR

    """_punish_target is a function that returns the closest prey piece that is not owned by the bot's player"""
    def _punish_target() -> dict | None:
        # Iterate over all the pieces owned by other players
        for parts in by_owner.values():
            # If any of the pieces is a threat, skip it
            if any(part["kind"] == KIND_THREAT for part in parts):
                continue
            if len(parts) <= 1:
                continue
            meals = [part for part in parts if part["kind"] == KIND_PREY]
            if len(meals) != len(parts) or not meals:
                continue
            if not _split_prey_ready(meals[0]):
                continue
            return min(meals, key=lambda part: part["dist"])
        return None

    def _catchable(item: dict, *, allow_lunge: bool) -> bool:
        if item["protected"] or protected:
            return False
        if not _split_prey_ready(item):
            return False
        # Corpse pieces do not flee. If we can eat one in vision, walk it down
        # instead of grazing pellets — they are almost always worth more.
        if item.get("inert"):
            return True
        hunt_cap = vis_r * personality.hunt_range
        walking = item["closing"] > -APPROACHING_SPEED and item["dist"] <= hunt_cap
        trapped = _is_trapped(item["x"], item["y"], cx, cy, width, height)
        lunge = allow_lunge and split_lunge_ok(
            ours,
            item,
            threats,
            personality,
            protected=protected,
            in_recover=memory.state == STATE_RECOVER,
            vision_r=vis_r,
            cx=cx,
            cy=cy,
        )
        return walking or trapped or lunge

    punish = None if protected else _punish_target()
    free_meals = []
    hunt_target = None
    if not protected:
        free_meals = [
            item
            for item in prey
            if any(piece["mass"] > item["mass"] * EAT_RATIO for piece in ours)
            and _catchable(item, allow_lunge=False)
        ]
        catchable = [item for item in prey if _catchable(item, allow_lunge=True)]
        inert_meals = [item for item in catchable if item.get("inert")]
        if inert_meals:
            hunt_target = min(inert_meals, key=lambda item: item["dist"])
        elif punish is not None:
            hunt_target = punish
        elif catchable:
            hunt_target = min(catchable, key=lambda item: item["dist"])

    desired = STATE_GRAZE
    if want_flee:
        desired = STATE_FLEE
    elif recovering_cluster and not free_meals:
        desired = STATE_RECOVER
    elif hunt_target is not None or free_meals:
        desired = STATE_HUNT
        if hunt_target is None:
            hunt_target = min(free_meals, key=lambda item: item["dist"])
    elif recovering_cluster:
        desired = STATE_RECOVER

    # dwell_ticks is the number of ticks the bot will stay in the current decision state
    dwell_ticks = STATE_DWELL_SECONDS * tick_rate
    # If the desired state is different from the current state, or the bot has been in the current state for too long, update the state
    if desired != memory.state:
        # If the desired state has higher priority than the current state, or the bot has been in the current state for too long, update the state
        if PRIORITY[desired] > PRIORITY[memory.state] or memory.ticks_in_state >= dwell_ticks:
            memory.state = desired
            memory.ticks_in_state = 0
        else:
            memory.ticks_in_state += 1
    else:
        memory.ticks_in_state += 1

    split = False
    dx = dy = 0.0

    if memory.state == STATE_FLEE:
        tx, ty = memory.last_threat if memory.last_threat is not None else (cx, cy)
        away_x, away_y = cx - tx, cy - ty
        wall_x, wall_y = _wall_repulsion(cx, cy, width, height)
        dx, dy = away_x + wall_x, away_y + wall_y
        dx, dy = _fallback_steer(dx, dy, wall_x, wall_y)
        split = (not protected) and sacrifice_ok(
            ours,
            threats,
            prev_positions,
            dt,
            personality,
            width,
            height,
            dx,
            dy,
            ovx,
            ovy,
        )
    elif memory.state == STATE_HUNT and hunt_target is not None:
        lunge = (
            (not hunt_target.get("inert"))
            and split_lunge_ok(
                ours,
                hunt_target,
                threats,
                personality,
                protected=protected,
                in_recover=False,
                vision_r=vis_r,
                cx=cx,
                cy=cy,
            )
            and personality.split_willingness > 0.0
        )
        if recovering_cluster and hunt_target in free_meals:
            lunge = False
        split = lunge
        if lunge:
            hitter = _lunge_hitter(ours, hunt_target)
            aim = hitter if hitter is not None else {"x": cx, "y": cy}
            dx = hunt_target["x"] - aim["x"]
            dy = hunt_target["y"] - aim["y"]
            dx, dy = _fallback_steer(dx, dy)
        else:
            dx = hunt_target["x"] - cx
            dy = hunt_target["y"] - cy
            sx, sy = _shield_repulsion(cx, cy, foreign, our_best)
            dx += sx
            dy += sy
            dx, dy = _fallback_steer(dx, dy)
    elif memory.state == STATE_RECOVER:
        target = _pick_graze_target(cx, cy, food_index, memory)
        if target is not None:
            dx, dy = target[0] - cx, target[1] - cy
        else:
            dx, dy = 0.0, 0.0
        wall_x, wall_y = _wall_repulsion(cx, cy, width, height, margin=40.0)
        dx += 0.15 * wall_x
        dy += 0.15 * wall_y
    else:
        if protected:
            campers = [
                item
                for item in foreign
                if item["dist"] < vis_r * 0.5
            ]
            if campers:
                nearest = min(campers, key=lambda item: item["dist"])
                dx, dy = cx - nearest["x"], cy - nearest["y"]
            else:
                target = _pick_graze_target(cx, cy, food_index, memory)
                if target is not None:
                    dx, dy = target[0] - cx, target[1] - cy
                else:
                    dx, dy = _wander(cx, cy, width, height, vis_r, memory)
        else:
            target = _pick_graze_target(cx, cy, food_index, memory)
            if target is not None:
                dx, dy = target[0] - cx, target[1] - cy
            else:
                dx, dy = _wander(cx, cy, width, height, vis_r, memory)
        sx, sy = _shield_repulsion(cx, cy, foreign, our_best)
        dx += sx
        dy += sy
        wall_x, wall_y = _wall_repulsion(cx, cy, width, height, margin=40.0)
        dx += 0.2 * wall_x
        dy += 0.2 * wall_y
        dx, dy = _fallback_steer(dx, dy, wall_x, wall_y)

    dx, dy = _normalized(dx, dy)
    return dx, dy, split
