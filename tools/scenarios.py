"""Scripted scenarios, one per Phase 1 [Both] checklist item in GUIDEBOOK.md.

Each scenario stages the world so a single behaviour is unmistakable, runs the
real `simulation.step`, and records every tick. Nothing here reimplements game
logic; these are only setups and camera hints.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass, field

from server import simulation
from server.config import (
    FOOD_COUNT,
    MAX_PIECES,
    MIN_SPLIT_MASS,
    REMERGE_SECONDS,
    SPLIT_KICK_DECAY_SECONDS,
    SPLIT_KICK_SPEED,
    TICK_RATE,
    WORLD_HEIGHT,
    WORLD_WIDTH,
)
from server.main import (
    CIRCLE_PERIOD_SECONDS,
    DEMO_MASS,
    SPLIT_AT_SECONDS,
    input_toward_nearest_food,
)
from server.models import Food, Piece, Player
from server.world import World

DT = 1.0 / TICK_RATE
CENTER = (WORLD_WIDTH / 2, WORLD_HEIGHT / 2)


class Recorder:
    """Runs a world tick by tick and captures a frame per tick.

    Frames hold the exact server -> client shape from section 4 of the build
    plan, so the renderer they feed is the same one the Phase 3 client will use.
    Food is delta encoded purely to keep the generated files small; the viewer
    expands it back to a full list before anything sees it.
    """

    def __init__(self, seed: int = 0, food_target: int = 0) -> None:
        self.world = World(seed=seed, food_target=food_target)
        self.frames: list[dict] = []
        self._pending: list[str] = []
        self._food_ids: set[str] = set()
        self._previous: dict[str, tuple[int, float]] = {}

    def spawn(
        self,
        name: str,
        x: float,
        y: float,
        mass: float,
        last_input: tuple[float, float] = (0.0, 0.0),
    ) -> Player:
        player = self.world.spawn_player(name, x, y, mass)
        player.last_input = last_input
        return player

    def add_piece(
        self, player: Player, x: float, y: float, mass: float, fresh_split: bool = True
    ) -> Piece:
        piece = Piece(piece_id=self.world.new_id(), x=x, y=y, mass=mass)
        if fresh_split:
            # Start the remerge timer now, so staged pieces behave like the
            # product of a split rather than instantly recombining.
            piece.split_time = self.world.now
        player.pieces.append(piece)
        return piece

    def add_food(self, x: float, y: float) -> Food:
        food = Food(id=self.world.new_id(), x=x, y=y)
        self.world.food[food.id] = food
        return food

    def note(self, message: str) -> None:
        self._pending.append(message)

    def split(self, player: Player, dx: float, dy: float) -> int:
        """Split toward (dx, dy) the way a client does: aim, then tap split.

        The split message carries no direction on the wire, so `try_split` reads
        `last_input`. The previous input is put back afterwards, so a scenario
        that stages a stationary blob keeps one and the split stays isolated.
        """
        previous = player.last_input
        player.last_input = (dx, dy)
        before = len(player.pieces)
        created = simulation.try_split(self.world, player)
        player.last_input = previous
        if created:
            self.note(f"{player.name}: split {before} -> {len(player.pieces)} pieces")
        else:
            self.note(f"{player.name}: SPLIT REFUSED (still {before} pieces)")
        return created

    def run(self, seconds: float, on_tick: Callable[[World], None] | None = None) -> None:
        if not self.frames:
            self.capture()
        for _ in range(round(seconds * TICK_RATE)):
            if on_tick is not None:
                on_tick(self.world)
            simulation.step(self.world, DT)
            self.capture()

    def capture(self) -> None:
        world = self.world
        players = []
        debug_pieces = {}
        for player in world.players.values():
            pieces = []
            # A lone piece is always past its timer, and flagging it would just
            # put an outline on every solo blob in every scenario.
            can_merge = len(player.pieces) > 1
            for piece in player.pieces:
                age = world.now - piece.split_time
                pieces.append(
                    {
                        "piece_id": piece.piece_id,
                        "x": round(piece.x, 2),
                        "y": round(piece.y, 2),
                        "mass": round(piece.mass, 2),
                    }
                )
                debug_pieces[piece.piece_id] = {
                    "vx": round(piece.vx, 2),
                    "vy": round(piece.vy, 2),
                    "age": round(age, 2),
                    "merge_ready": can_merge and age >= REMERGE_SECONDS,
                }
            players.append({"id": player.id, "name": player.name, "pieces": pieces})

        frame: dict = {"t": round(world.now, 4), "players": players}

        current = set(world.food)
        if not self.frames:
            frame["food"] = [
                [f.id, round(f.x, 1), round(f.y, 1)] for f in world.food.values()
            ]
        else:
            added = current - self._food_ids
            removed = self._food_ids - current
            if added:
                frame["food_added"] = [
                    [world.food[i].id, round(world.food[i].x, 1), round(world.food[i].y, 1)]
                    for i in sorted(added)
                ]
            if removed:
                frame["food_removed"] = sorted(removed)
        self._food_ids = current

        events = self._pending + self._detect_events()
        self._pending = []
        if events:
            frame["events"] = events

        frame["debug"] = {
            "pieces": debug_pieces,
            "inputs": {
                p.id: [round(p.last_input[0], 3), round(p.last_input[1], 3)]
                for p in world.players.values()
            },
        }
        self.frames.append(frame)

    def _detect_events(self) -> list[str]:
        """Infer structural events by diffing against the previous frame.

        Splits are announced explicitly by `split`; everything else (remerges,
        pieces being eaten, eliminations) is derived so scenarios don't have to
        predict what the simulation will do.
        """
        events = []
        for player in self.world.players.values():
            count = len(player.pieces)
            mass = sum(p.mass for p in player.pieces)
            previous = self._previous.get(player.id)
            if previous is not None:
                prev_count, prev_mass = previous
                if count < prev_count:
                    if mass >= prev_mass - 1e-6:
                        events.append(
                            f"{player.name}: REMERGED {prev_count} -> {count} pieces"
                        )
                    else:
                        events.append(
                            f"{player.name}: lost a piece ({prev_count} -> {count})"
                        )
                        if count == 0:
                            events.append(f"{player.name}: ELIMINATED")
                elif count == prev_count and mass > prev_mass + 1.5:
                    # Food is worth 1 mass; a bigger jump means it ate a player.
                    events.append(f"{player.name}: ATE a piece (+{mass - prev_mass:.0f})")
            self._previous[player.id] = (count, mass)
        return events


@dataclass
class Scenario:
    id: str
    title: str
    checklist: str
    expect: str
    build: Callable[[], Recorder]
    # World-space rectangle the camera should frame. Defaults to the whole world.
    view: tuple[float, float, float, float] = (0.0, 0.0, WORLD_WIDTH, WORLD_HEIGHT)
    speed: float = 1.0
    tags: list[str] = field(default_factory=list)


def _view_around(x: float, y: float, size: float) -> tuple[float, float, float, float]:
    return (x - size / 2, y - size / 2, size, size)


# --- one builder per checklist item ---------------------------------------


def _input_direction() -> Recorder:
    rec = Recorder()
    cx, cy = CENTER
    for i in range(8):
        angle = i * math.pi / 4
        dx, dy = math.cos(angle), math.sin(angle)
        rec.spawn(
            f"{round(math.degrees(angle))}deg",
            cx + dx * 90,
            cy + dy * 90,
            mass=120,
            last_input=(dx, dy),
        )
    rec.note("8 blobs, each with a different last_input, all equal mass")
    rec.run(6.0)
    return rec


def _speed_vs_mass() -> Recorder:
    rec = Recorder()
    for index, mass in enumerate((40, 250, 1500)):
        rec.spawn(f"mass {mass}", 120.0, 300.0 + index * 300.0, mass, last_input=(1.0, 0.0))
    rec.note("Three blobs released together, all with last_input = (1, 0)")
    rec.run(6.0)
    return rec


def _world_bounds() -> Recorder:
    rec = Recorder()
    rec.spawn("to (0,0)", 320.0, 320.0, 40, last_input=(-1.0, -1.0))
    rec.spawn("to (max,max)", 880.0, 880.0, 40, last_input=(1.0, 1.0))
    rec.note("Both blobs are driven straight at opposite corners and held there")
    rec.run(8.0)
    return rec


def _food_eating() -> Recorder:
    lane = 15
    rec = Recorder(food_target=lane)
    for i in range(lane):
        rec.add_food(260.0 + i * 60.0, CENTER[1])
    eater = rec.spawn("eater", 180.0, CENTER[1], mass=200, last_input=(1.0, 0.0))
    rec.note(f"{lane} pellets in a lane; food_target={lane} so eaten pellets respawn")
    rec.note(f"starting mass {eater.pieces[0].mass:.0f}")
    rec.run(11.0)
    return rec


def _eat_ratio() -> Recorder:
    rec = Recorder()
    rec.spawn("124 (1.24x)", 380.0, 380.0, 124, last_input=(1.0, 0.0))
    rec.spawn("100", 580.0, 380.0, 100, last_input=(-1.0, 0.0))
    rec.spawn("126 (1.26x)", 380.0, 820.0, 126, last_input=(1.0, 0.0))
    rec.spawn("100 ", 580.0, 820.0, 100, last_input=(-1.0, 0.0))
    rec.note("Top pair is 1.24x (below the threshold), bottom pair is 1.26x")
    rec.run(4.0)
    return rec


def _own_pieces_no_eat() -> Recorder:
    rec = Recorder()
    cx, cy = CENTER
    player = rec.spawn("one player", cx - 4.0, cy, mass=400)
    player.pieces[0].split_time = rec.world.now
    rec.add_piece(player, cx + 8.0, cy, mass=30)
    rec.note("400 mass and 30 mass overlapping, both owned by the same player")
    rec.note("400 > 30 * 1.25, so a cross-player pair here would be eaten instantly")
    rec.run(5.0)
    return rec


def _split_refused_small() -> Recorder:
    rec = Recorder()
    small = rec.spawn(f"mass {MIN_SPLIT_MASS - 1}", 450.0, CENTER[1], MIN_SPLIT_MASS - 1)
    big = rec.spawn(f"mass {MIN_SPLIT_MASS + 1}", 750.0, CENTER[1], MIN_SPLIT_MASS + 1)
    rec.run(1.0)
    rec.note(f"both attempt to split; MIN_SPLIT_MASS is {MIN_SPLIT_MASS}")
    rec.split(small, 0.0, -1.0)
    rec.split(big, 0.0, -1.0)
    rec.run(3.0)
    return rec


def _split_refused_max() -> Recorder:
    rec = Recorder()
    cx, cy = CENTER
    player = rec.spawn("8 pieces", cx + 70.0, cy, mass=100)
    player.pieces[0].split_time = rec.world.now
    for i in range(1, MAX_PIECES):
        angle = i * 2 * math.pi / MAX_PIECES
        rec.add_piece(player, cx + math.cos(angle) * 70.0, cy + math.sin(angle) * 70.0, 100)
    rec.run(1.0)
    rec.note(f"already at MAX_PIECES ({MAX_PIECES}), every piece is well over 35 mass")
    rec.split(player, 1.0, 0.0)
    rec.run(3.0)
    return rec


def _exponential_split() -> Recorder:
    rec = Recorder()
    cx, cy = CENTER
    player = rec.spawn("mass 280", cx, cy, mass=280)
    rec.note("one piece of 280; each press splits every eligible piece at once")
    rec.run(1.5)
    # Every piece takes the same kick direction on a given press, so the varied
    # angles here stand in for a player swinging their cursor between presses.
    # They also fan the cluster out, which makes the count easier to read.
    for degrees in (0, 90, 200, 300):
        angle = math.radians(degrees)
        rec.split(player, math.cos(angle), math.sin(angle))
        rec.run(1.5)
    return rec


def _split_halves_and_kick() -> Recorder:
    rec = Recorder()
    cx, cy = CENTER
    player = rec.spawn("mass 200", cx - 30.0, cy, mass=200)
    rec.run(1.0)
    rec.split(player, 1.0, 0.0)
    rec.run(3.0)
    return rec


def _kick_decay() -> Recorder:
    rec = Recorder()
    cx, cy = CENTER
    player = rec.spawn("mass 200", cx - 10.0, cy, mass=200)
    rec.run(0.5)
    rec.note(f"kick decays linearly over {SPLIT_KICK_DECAY_SECONDS}s; watch the arrow")
    rec.split(player, 1.0, 0.0)
    rec.run(2.0)
    return rec


def _remerge() -> Recorder:
    rec = Recorder()
    cx, cy = CENTER
    player = rec.spawn("mass 200", cx, cy, mass=200)
    rec.run(1.0)
    rec.split(player, 1.0, 0.0)
    rec.note(f"halves stay in contact; remerge timer is {REMERGE_SECONDS}s")
    rec.run(REMERGE_SECONDS + 2.0)
    return rec


def _solid_collision() -> Recorder:
    rec = Recorder()
    cx, cy = CENTER
    rec.spawn("100", cx - 130.0, cy - 110.0, mass=100, last_input=(1.0, 0.0))
    rec.spawn("110", cx + 130.0, cy - 110.0, mass=110, last_input=(-1.0, 0.0))
    rec.spawn("100 ", cx - 130.0, cy + 110.0, mass=100, last_input=(1.0, 0.0))
    rec.spawn("400", cx + 130.0, cy + 110.0, mass=400, last_input=(-1.0, 0.0))
    rec.note("both pairs drive straight into each other and never stop pushing")
    rec.note("top pair is 1.1x (under the eat ratio); bottom pair is 4x (over it)")
    rec.run(7.0)
    return rec


def _split_cohesion() -> Recorder:
    rec = Recorder()
    cx, cy = CENTER
    player = rec.spawn("mass 200", cx - 15.0, cy, mass=200)
    rec.run(1.0)
    rec.split(player, 1.0, 0.0)
    rec.note("no input after the split; only the cluster forces move the halves")
    rec.run(5.0)
    return rec


def _merge_pull() -> Recorder:
    rec = Recorder()
    cx, cy = CENTER
    # Staged at the resting distance the cluster forces settle a pair at, so the
    # clip opens on a body already at rest and the only motion is the pull.
    player = rec.spawn("half", cx - 4.8, cy, mass=100)
    rec.add_piece(player, cx + 4.8, cy, mass=100)
    # Backdated so the timer clears one second in, rather than making the clip
    # sit through the full 12s wait that the `remerge` scenario already shows.
    for piece in player.pieces:
        piece.split_time = rec.world.now - REMERGE_SECONDS + 1.0
    rec.note("resting in contact, remerge timer clears at t=1s")
    rec.run(3.0)
    return rec


def _food_count_stable() -> Recorder:
    rec = Recorder(food_target=FOOD_COUNT)
    rec.world.spawn_food_to_target_count()
    grazer = rec.spawn("grazer", CENTER[0], CENTER[1], mass=900)
    rec.note(f"full field of {FOOD_COUNT} pellets; watch the food counter hold steady")

    def steer(world: World) -> None:
        grazer.last_input = input_toward_nearest_food(world, grazer)

    rec.run(10.0, on_tick=steer)
    return rec


def _demo() -> Recorder:
    """The same scenario server/main.py prints, but recorded for the viewer."""
    rec = Recorder(food_target=FOOD_COUNT)
    rec.world.spawn_food_to_target_count()
    player_a = rec.spawn("A", WORLD_WIDTH / 4, WORLD_HEIGHT / 2, DEMO_MASS)
    player_b = rec.spawn("B", WORLD_WIDTH * 3 / 4, WORLD_HEIGHT / 2, DEMO_MASS)

    def steer(world: World) -> None:
        player_a.last_input = input_toward_nearest_food(world, player_a)
        angle = 2.0 * math.pi * world.now / CIRCLE_PERIOD_SECONDS
        player_b.last_input = (math.cos(angle), math.sin(angle))

    rec.run(SPLIT_AT_SECONDS, on_tick=steer)
    rec.split(player_a, 1.0, 0.0)
    rec.run(REMERGE_SECONDS + 3.0, on_tick=steer)
    return rec


SCENARIOS: list[Scenario] = [
    Scenario(
        id="input_direction",
        title="Movement follows last_input",
        checklist="Piece moves in the direction of its player's `last_input`.",
        expect=(
            "Eight equal blobs fan straight outward, each along its own arrow. "
            "No blob drifts sideways or lags."
        ),
        build=_input_direction,
    ),
    Scenario(
        id="speed_vs_mass",
        title="Speed decreases as mass grows",
        checklist="Speed decreases as mass grows (bigger blob is slower).",
        expect=(
            "All three start together with identical input. The 40 blob pulls "
            "clearly ahead, 250 trails it, and 1500 barely moves."
        ),
        build=_speed_vs_mass,
    ),
    Scenario(
        id="world_bounds",
        title="Pieces stay inside world bounds",
        checklist="Piece stays inside world bounds - no negative or off-world coordinates.",
        expect=(
            "Both blobs drive into opposite corners and stop dead at the border. "
            "The HUD coordinates clamp to 0 and 1200 and never pass them."
        ),
        build=_world_bounds,
    ),
    Scenario(
        id="food_eating",
        title="Food is eaten, mass grows, food respawns",
        checklist=(
            "Food gets eaten when a piece's circle covers the food's center; piece "
            "mass increases; food is removed and eventually respawns."
        ),
        expect=(
            "The blob eats along the lane. Each pellet vanishes as the circle "
            "covers it, mass ticks up by 1, and a replacement appears elsewhere "
            "so the food counter holds at 15."
        ),
        build=_food_eating,
        view=(0.0, 400.0, WORLD_WIDTH, 400.0),
    ),
    Scenario(
        id="eat_ratio",
        title="Eat rule needs a 1.25x mass ratio",
        checklist=(
            "Player-vs-player eat rule: `A.mass > B.mass * 1.25` is required. Equal "
            "or near-equal blobs don't eat each other."
        ),
        expect=(
            "The top pair (124 vs 100, ratio 1.24) collides solidly and shoves "
            "instead of eating - both masses hold. The bottom pair (126 vs 100, "
            "ratio 1.26) sinks in on contact: the 100 disappears and the 126 "
            "becomes 226."
        ),
        build=_eat_ratio,
    ),
    Scenario(
        id="own_pieces_no_eat",
        title="Own pieces never eat each other",
        checklist="Player's own pieces never eat each other - they can only remerge.",
        expect=(
            "A 400 and a 30 belonging to one player settle into contact and sit "
            "there for five seconds. Both masses stay put. Nothing is eaten and, "
            "since the remerge timer has not elapsed, nothing merges either."
        ),
        build=_own_pieces_no_eat,
        view=_view_around(*CENTER, 220.0),
    ),
    Scenario(
        id="split_refused_small",
        title="Split refused below MIN_SPLIT_MASS",
        checklist="`try_split` refuses to split a piece under `MIN_SPLIT_MASS`.",
        expect=(
            "Both blobs try to split at t=1s. The 34 does nothing at all. The 36 "
            "splits into two 18s, proving the attempt itself was valid."
        ),
        build=_split_refused_small,
        view=(300.0, 350.0, 600.0, 600.0),
    ),
    Scenario(
        id="split_refused_max",
        title="Split refused at MAX_PIECES",
        checklist="`try_split` refuses to split when the player already has `MAX_PIECES`.",
        expect=(
            "Eight pieces of 100, every one far above the 35 threshold, try to "
            "split at t=1s. Nothing happens: still eight pieces, still 100 each."
        ),
        build=_split_refused_max,
        view=_view_around(*CENTER, 320.0),
    ),
    Scenario(
        id="exponential_split",
        title="Splitting is exponential, halving in mass",
        checklist=(
            "`try_split` is exponential in growth, halving in mass (i.e., it should "
            "split all possible pieces that have been split previously)"
        ),
        expect=(
            "One 280 becomes 2x140, then 4x70, then 8x35 - every existing piece "
            "splits on each press. The fourth press is refused at the 8 piece cap. "
            "Total mass reads 280 throughout."
        ),
        build=_exponential_split,
        view=_view_around(*CENTER, 260.0),
    ),
    Scenario(
        id="split_halves_and_kick",
        title="Split halves mass and kicks the new piece",
        checklist=(
            "A successful split produces two pieces of half mass, and the new one "
            "has a velocity kick toward the cursor direction."
        ),
        expect=(
            "The 200 becomes two 100s at the same spot. Only one of them carries a "
            "velocity arrow, pointing right along the cursor direction. The other "
            "is shoved a little the opposite way as the two bodies unstack."
        ),
        build=_split_halves_and_kick,
        view=_view_around(*CENTER, 200.0),
    ),
    Scenario(
        id="kick_decay",
        title="Split kick decays to zero",
        checklist="Split kick decays to zero over ~`SPLIT_KICK_DECAY_SECONDS`.",
        expect=(
            f"Plays at quarter speed. The velocity arrow on the kicked piece "
            f"starts at {SPLIT_KICK_SPEED:.0f} and shrinks linearly to exactly 0 "
            f"over {SPLIT_KICK_DECAY_SECONDS}s. After that the only thing still "
            f"moving the halves is cohesion drawing them back together."
        ),
        build=_kick_decay,
        view=_view_around(*CENTER, 120.0),
        speed=0.25,
    ),
    Scenario(
        id="solid_collision",
        title="Different players' pieces are solid",
        checklist=(
            "Different players' pieces collide solidly when neither can eat the "
            "other; a predator is not blocked by its prey."
        ),
        expect=(
            "The top pair meets in the middle and jams there, the 110 slowly "
            "shoving the 100 backwards - neither ever enters the other. The bottom "
            "pair is not blocked at all: the 400 sinks into the 100 and eats it."
        ),
        build=_solid_collision,
        view=_view_around(*CENTER, 420.0),
    ),
    Scenario(
        id="split_cohesion",
        title="Split halves drift back into contact",
        checklist="Split halves pop apart on the kick, then drift back into contact.",
        expect=(
            "Plays at half speed. The halves fly roughly three blob widths apart "
            "while the kick lasts, hang at full spread for an instant, then slide "
            "back together and settle overlapping as one body. Nothing is steering "
            "them - that return trip is cohesion on its own."
        ),
        build=_split_cohesion,
        view=_view_around(*CENTER, 140.0),
        speed=0.5,
    ),
    Scenario(
        id="remerge",
        title="Pieces remerge after REMERGE_SECONDS",
        checklist=(
            "Two same-player pieces remerge after `REMERGE_SECONDS` when their "
            "circles overlap."
        ),
        expect=(
            "Split at t=1s into two 100s that pop apart and settle back into "
            "contact. They hold there until t=13s, exactly 12s later, when the "
            "outline appears and they sink together into a single 200."
        ),
        build=_remerge,
        view=_view_around(*CENTER, 160.0),
    ),
    Scenario(
        id="merge_pull",
        title="The remerge is a pull, not a snap",
        checklist=(
            "Once the remerge timer clears, the pair visibly sinks into each other "
            "before merging."
        ),
        expect=(
            "Plays at a third speed on a staged pair whose timer is set to clear "
            "at t=1s. Until then they hold at resting overlap. The dashed outline "
            "appears the instant the timer clears, and you can watch them sink "
            "the rest of the way in over the next few tenths of a second."
        ),
        build=_merge_pull,
        view=_view_around(*CENTER, 60.0),
        speed=0.35,
    ),
    Scenario(
        id="food_count_stable",
        title="Food count holds at FOOD_COUNT",
        checklist="`food` dict length stays at `FOOD_COUNT` over time.",
        expect=(
            f"A large grazer eats continuously for ten seconds. Its mass climbs "
            f"steadily while the food counter never leaves {FOOD_COUNT}."
        ),
        build=_food_count_stable,
    ),
    Scenario(
        id="demo",
        title="Free-running demo (server/main.py)",
        checklist="The run described under 'How to verify' in GUIDEBOOK.md.",
        expect=(
            "A chases the nearest food while B circles. A splits at t=3s and the "
            "halves recombine at t=15s. Food holds at 600 the whole time."
        ),
        build=_demo,
        tags=["demo"],
    ),
]

BY_ID = {scenario.id: scenario for scenario in SCENARIOS}
