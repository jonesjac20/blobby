"""Bot brain and light plumbing. No live server except the aiohttp test client."""

from __future__ import annotations

import asyncio

import pytest

from bots.brain import (
    FLEE_MEMORY_SECONDS,
    GRAZE_CELL,
    KIND_PEER,
    KIND_PREY,
    KIND_THREAT,
    PUNISH_REMERGE_FLOOR,
    STATE_FLEE,
    STATE_GRAZE,
    STATE_HUNT,
    STATE_RECOVER,
    FoodIndex,
    Personality,
    classify_piece,
    decide,
    new_memory,
    sacrifice_ok,
    split_lunge_ok,
)
from bots.simple_bot import assign_colors, distinct_names
from server.config import (
    EAT_RATIO,
    INITIAL_PLAYER_MASS,
    MAX_PIECES,
    MIN_SPLIT_MASS,
    NAME_MAX_LEN,
    WORLD_HEIGHT,
    WORLD_WIDTH,
)


def _piece(piece_id, x, y, mass, remerge_in=0.0):
    return {
        "piece_id": piece_id,
        "x": x,
        "y": y,
        "mass": mass,
        "remerge_in": remerge_in,
    }


def _player(player_id, pieces, *, protected=False, name="A", inert=False):
    return {
        "id": player_id,
        "name": name,
        "color": "#ffffff",
        "protected": protected,
        "inert": inert,
        "pieces": pieces,
    }


def _index(pellets, version=1):
    index = FoodIndex()
    index.update(version, pellets)
    return index


def _view(
    me_pieces,
    others=None,
    food=None,
    *,
    protected=False,
    personality=None,
    prev_positions=None,
    prev_centroid=None,
    food_index=None,
    tick_rate=30,
):
    me = _player("me", me_pieces, protected=protected, name="bot")
    players = [me, *(others or [])]
    index = food_index if food_index is not None else _index(food or [])
    return {
        "self_id": "me",
        "tick_rate": tick_rate,
        "world_width": WORLD_WIDTH,
        "world_height": WORLD_HEIGHT,
        "initial_player_mass": INITIAL_PLAYER_MASS,
        "players": players,
        "prev_positions": prev_positions or {},
        "prev_centroid": prev_centroid,
        "food_index": index,
        "personality": personality or Personality(),
    }


# --- classifier -----------------------------------------------------------


def test_classify_prey_threat_peer_and_protected():
    assert classify_piece(100, 100, 70, False) == KIND_PREY
    assert classify_piece(100, 100, 70, True) == KIND_PEER
    assert classify_piece(100, 100, 130, False) == KIND_THREAT
    # Peer band: 1/1.25 <= ratio <= 1.25
    assert classify_piece(100, 100, 100, False) == KIND_PEER
    assert classify_piece(100, 100, 124, False) == KIND_PEER


def test_classify_inert_is_never_a_threat():
    assert classify_piece(100, 100, 10000, False, True) == KIND_PEER
    assert classify_piece(100, 100, 70, False, True) == KIND_PREY
    assert classify_piece(100, 100, 70, True, True) == KIND_PREY


def test_decide_hunts_a_catchable_inert_instead_of_fleeing():
    memory = new_memory(0)
    corpse = _player("dead", [_piece("d", 230.0, 200.0, 40)], inert=True)
    dx, dy, split = decide(
        _view(
            [_piece("a", 200.0, 200.0, 80)],
            others=[corpse],
            food=[],
            personality=Personality(hunt_range=1.0),
        ),
        memory,
    )
    assert memory.state != STATE_FLEE
    assert split is False
    assert dx > 0.0


def test_decide_hunts_edible_inert_instead_of_grazing_pellets():
    """A sitting corpse is worth more than nearby food, even if we were grazing away."""
    memory = new_memory(0)
    corpse = _player("dead", [_piece("d", 280.0, 200.0, 40)], inert=True)
    dx, dy, split = decide(
        _view(
            [_piece("a", 200.0, 200.0, 80)],
            others=[corpse],
            food=[(180.0, 200.0)],
            prev_centroid=(210.0, 200.0),
            personality=Personality(hunt_range=0.6),
        ),
        memory,
    )
    assert memory.state == STATE_HUNT
    assert split is False
    assert dx > 0.0
    assert abs(dy) < 0.2


def test_decide_hunts_one_edible_inert_fragment_of_a_split_corpse():
    """Inert never remelts, so sibling mass must not block a catchable fragment."""
    memory = new_memory(0)
    corpse = _player(
        "dead",
        [
            _piece("meal", 240.0, 200.0, 40),
            _piece("wall", 400.0, 200.0, 20000),
        ],
        inert=True,
    )
    dx, dy, split = decide(
        _view(
            [_piece("a", 200.0, 200.0, 80)],
            others=[corpse],
            food=[(190.0, 200.0)],
        ),
        memory,
    )
    assert memory.state == STATE_HUNT
    assert split is False
    assert dx > 0.0


def test_decide_grazes_when_inert_is_too_big_to_eat():
    memory = new_memory(0)
    corpse = _player("dead", [_piece("d", 230.0, 200.0, 10000)], inert=True)
    dx, dy, split = decide(
        _view(
            [_piece("a", 200.0, 200.0, 80)],
            others=[corpse],
            food=[(180.0, 200.0)],
        ),
        memory,
    )
    assert memory.state == STATE_GRAZE
    assert split is False
    assert dx < 0.0


# --- graze ----------------------------------------------------------------


def test_graze_picks_nearest_in_3x3_not_the_global_nearest():
    cx, cy = 150.0, 150.0
    local = (160.0, 150.0)
    far = (2000.0, 2000.0)
    memory = new_memory(0)
    dx, dy, split = decide(
        _view([_piece("a", cx, cy, 50)], food=[local, far]),
        memory,
    )
    assert split is False
    assert memory.state == STATE_GRAZE
    assert dx > 0.9
    assert abs(dy) < 0.2


def test_graze_does_not_sit_at_origin_when_a_local_pellet_exists():
    memory = new_memory(0)
    dx, dy, _ = decide(
        _view([_piece("a", 150.0, 150.0, 50)], food=[(180.0, 150.0)]),
        memory,
    )
    assert (dx, dy) != (0.0, 0.0)


def test_graze_picks_a_closer_pellet_in_a_neighbor_cell_over_one_in_its_own():
    """3×3 neighborhood: a pellet just over the cell edge beats a farther one in-cell.

    Without the 8 neighbors, greedy-in-current-cell would steer left toward
    `same_cell` even though `neighbor` is a few units away.
    """
    y = 150.0
    bot_x = GRAZE_CELL - 2.0
    neighbor = (GRAZE_CELL + 2.0, y)
    same_cell = (10.0, y)
    assert int(bot_x // GRAZE_CELL) == int(same_cell[0] // GRAZE_CELL)
    assert int(bot_x // GRAZE_CELL) != int(neighbor[0] // GRAZE_CELL)
    assert abs(neighbor[0] - bot_x) < abs(same_cell[0] - bot_x)

    memory = new_memory(0)
    dx, dy, _ = decide(
        _view([_piece("a", bot_x, y, 50)], food=[neighbor, same_cell]),
        memory,
    )
    assert memory.graze_target == neighbor
    assert dx > 0.9
    assert abs(dy) < 0.2


def test_food_index_rebuilds_once_per_version():
    index = FoodIndex()
    pellets = [[10, 10], [20, 20]]
    index.update(3, pellets)
    index.update(3, pellets)
    index.update(4, pellets)
    assert index.rebuilds == 2


# --- vision / flee memory -------------------------------------------------


def test_a_player_outside_vision_is_not_hunt_prey():
    memory = new_memory(0)
    far = _player("ogre", [_piece("g", 2500.0, 2500.0, 20)])
    _, _, split = decide(
        _view([_piece("a", 200.0, 200.0, 80)], others=[far], food=[(210.0, 200.0)]),
        memory,
    )
    assert memory.state == STATE_GRAZE
    assert split is False


def test_never_seen_giant_is_ignored():
    memory = new_memory(0)
    giant = _player("g", [_piece("g", 2500.0, 200.0, 5000)])
    decide(
        _view([_piece("a", 200.0, 200.0, 50)], others=[giant], food=[(220.0, 200.0)]),
        memory,
    )
    assert memory.state == STATE_GRAZE
    assert memory.last_threat is None


def test_flee_keeps_steering_from_last_threat_after_it_leaves_vision():
    memory = new_memory(0)
    threat = _player("t", [_piece("t", 250.0, 200.0, 200)])
    # Closing toward us so Flee trips on approaching, not only panic radius.
    view = _view(
        [_piece("a", 200.0, 200.0, 50)],
        others=[threat],
        prev_positions={"t": (260.0, 200.0), "a": (200.0, 200.0)},
        food=[(180.0, 200.0)],
    )
    dx, dy, _ = decide(view, memory)
    assert memory.state == STATE_FLEE
    assert memory.last_threat is not None
    assert dx < 0

    gone = _view(
        [_piece("a", 200.0, 200.0, 50)],
        others=[_player("t", [_piece("t", 2500.0, 200.0, 200)])],
        food=[(180.0, 200.0)],
    )
    dx, dy, _ = decide(gone, memory)
    assert memory.state == STATE_FLEE
    assert dx < 0
    # Off-screen piece must not refresh the ghost to 2500.
    assert memory.last_threat[0] == pytest.approx(250.0)


def test_flee_memory_expires_into_graze():
    memory = new_memory(0)
    memory.state = STATE_FLEE
    memory.last_threat = (400.0, 200.0)
    memory.last_threat_ticks = 0
    memory.ticks_in_state = 100
    view = _view(
        [_piece("a", 200.0, 200.0, 50)],
        food=[(220.0, 200.0)],
        tick_rate=10,
    )
    # 1.5s * 10Hz = 15 ticks
    for _ in range(int(FLEE_MEMORY_SECONDS * 10) + 2):
        decide(view, memory)
    assert memory.state == STATE_GRAZE
    assert memory.last_threat is None


# --- dwell / transitions --------------------------------------------------


def test_dwell_holds_hunt_when_prey_flickers_away():
    memory = new_memory(0)
    prey = _player("p", [_piece("p", 230.0, 200.0, 20)])
    view_hunt = _view(
        [_piece("a", 200.0, 200.0, 80)],
        others=[prey],
        food=[(190.0, 200.0)],
        tick_rate=10,
        personality=Personality(hunt_range=1.0),
    )
    decide(view_hunt, memory)
    assert memory.state == STATE_HUNT
    view_graze = _view(
        [_piece("a", 200.0, 200.0, 80)],
        food=[(190.0, 200.0)],
        tick_rate=10,
    )
    decide(view_graze, memory)
    assert memory.state == STATE_HUNT
    memory.ticks_in_state = 100
    decide(view_graze, memory)
    assert memory.state == STATE_GRAZE


def test_flee_interrupts_graze_immediately():
    memory = new_memory(0)
    decide(
        _view([_piece("a", 200.0, 200.0, 50)], food=[(220.0, 200.0)]),
        memory,
    )
    assert memory.state == STATE_GRAZE
    threat = _player("t", [_piece("t", 205.0, 200.0, 200)])
    decide(
        _view(
            [_piece("a", 200.0, 200.0, 50)],
            others=[threat],
            food=[(220.0, 200.0)],
        ),
        memory,
    )
    assert memory.state == STATE_FLEE


def test_recover_when_split_and_remerge_pending():
    memory = new_memory(0)
    decide(
        _view(
            [
                _piece("a", 200.0, 200.0, 40, remerge_in=8.0),
                _piece("b", 220.0, 200.0, 40, remerge_in=8.0),
            ],
            food=[(210.0, 200.0)],
        ),
        memory,
    )
    assert memory.state == STATE_RECOVER


# --- punish bands ---------------------------------------------------------


def test_punish_band_turns_flee_into_hunt():
    """1.25M < P < 1.6M who split: fragments are prey."""
    memory = new_memory(0)
    memory.state = STATE_FLEE
    memory.last_threat = (250.0, 200.0)
    memory.ticks_in_state = 100
    us = 100.0
    fragment = us * 1.4 / 2.0  # P = 1.4M
    other = _player(
        "p",
        [
            _piece("p1", 240.0, 200.0, fragment, remerge_in=8.0),
            _piece("p2", 260.0, 200.0, fragment, remerge_in=8.0),
        ],
    )
    decide(
        _view(
            [_piece("a", 200.0, 200.0, us)],
            others=[other],
            food=[(180.0, 200.0)],
            personality=Personality(hunt_range=1.0),
        ),
        memory,
    )
    assert fragment * 2 / us < 1.6
    assert us > fragment * EAT_RATIO
    assert memory.state == STATE_HUNT


def test_split_predator_at_or_above_1_6_stays_flee():
    memory = new_memory(0)
    memory.state = STATE_FLEE
    memory.last_threat = (250.0, 200.0)
    memory.last_threat_ticks = 0
    memory.ticks_in_state = 100
    us = 100.0
    fragment = us * 2.0 / 2.0  # P = 2.0M → peers
    other = _player(
        "p",
        [
            _piece("p1", 240.0, 200.0, fragment, remerge_in=8.0),
            _piece("p2", 260.0, 200.0, fragment, remerge_in=8.0),
        ],
    )
    decide(
        _view(
            [_piece("a", 200.0, 200.0, us)],
            others=[other],
            food=[(180.0, 200.0)],
        ),
        memory,
    )
    assert memory.state == STATE_FLEE


def test_mixed_cluster_stays_flee():
    memory = new_memory(0)
    us = 100.0
    other = _player(
        "p",
        [
            _piece("core", 230.0, 200.0, 200.0, remerge_in=8.0),
            _piece("snack", 240.0, 200.0, 20.0, remerge_in=8.0),
        ],
    )
    decide(
        _view(
            [_piece("a", 200.0, 200.0, us)],
            others=[other],
            food=[(180.0, 200.0)],
        ),
        memory,
    )
    assert memory.state == STATE_FLEE


def test_punish_requires_remerge_floor():
    memory = new_memory(0)
    us = 100.0
    fragment = 70.0
    other = _player(
        "p",
        [
            _piece("p1", 240.0, 200.0, fragment, remerge_in=PUNISH_REMERGE_FLOOR),
            _piece("p2", 260.0, 200.0, fragment, remerge_in=0.5),
        ],
    )
    decide(
        _view(
            [_piece("a", 200.0, 200.0, us)],
            others=[other],
            food=[(220.0, 200.0)],
            personality=Personality(hunt_range=1.0),
        ),
        memory,
    )
    assert memory.state == STATE_GRAZE


# --- split-lunge / sacrifice ---------------------------------------------


def _lunge_kwargs(ours, prey, threats=None, **extra):
    defaults = dict(
        ours=ours,
        prey=prey,
        threats=threats or [],
        personality=Personality(),
        protected=False,
        in_recover=False,
        vision_r=400.0,
        cx=ours[0]["x"],
        cy=ours[0]["y"],
    )
    defaults.update(extra)
    return defaults


def test_split_lunge_each_checklist_line_can_fail():
    ours = [_piece("a", 200.0, 200.0, 200)]
    prey = _piece("p", 208.0, 200.0, 40)
    assert split_lunge_ok(**_lunge_kwargs(ours, prey)) is True

    tiny = [_piece("a", 200.0, 200.0, MIN_SPLIT_MASS - 1)]
    assert split_lunge_ok(**_lunge_kwargs(tiny, prey)) is False

    capped = [_piece(f"p{i}", 200.0, 200.0, 80) for i in range(MAX_PIECES)]
    assert split_lunge_ok(**_lunge_kwargs(capped, prey)) is False

    too_small_half = [_piece("a", 200.0, 200.0, 80)]
    fat_prey = _piece("p", 210.0, 200.0, 50)
    assert split_lunge_ok(**_lunge_kwargs(too_small_half, fat_prey)) is False

    far = _piece("p", 800.0, 200.0, 20)
    assert split_lunge_ok(**_lunge_kwargs(ours, far, vision_r=100.0)) is False

    threat = [_piece("t", 220.0, 200.0, 400)]
    assert split_lunge_ok(**_lunge_kwargs(ours, prey, threats=threat)) is False

    assert split_lunge_ok(**_lunge_kwargs(ours, {**prey, "protected": True})) is False
    assert split_lunge_ok(**_lunge_kwargs(ours, prey, in_recover=True)) is False
    assert (
        split_lunge_ok(
            **_lunge_kwargs(ours, prey, personality=Personality(split_willingness=0.0))
        )
        is False
    )


def test_sacrifice_checklist_and_timid_disables_it():
    ours = [_piece("a", 200.0, 200.0, 80)]
    threat = {
        **_piece("t", 205.0, 200.0, 200),
        "kind": KIND_THREAT,
    }
    ok = sacrifice_ok(
        ours,
        [threat],
        {"t": (204.0, 200.0)},
        1.0 / 30.0,
        Personality(),
        WORLD_WIDTH,
        WORLD_HEIGHT,
        -1.0,
        0.0,
        0.0,
        0.0,
    )
    assert ok is True
    timid = sacrifice_ok(
        ours,
        [threat],
        {"t": (204.0, 200.0)},
        1.0 / 30.0,
        Personality(split_willingness=0.0),
        WORLD_WIDTH,
        WORLD_HEIGHT,
        -1.0,
        0.0,
        0.0,
        0.0,
    )
    assert timid is False


def test_spawn_window_does_not_lunge():
    memory = new_memory(0)
    prey = _player("p", [_piece("p", 230.0, 200.0, 20)])
    _, _, split = decide(
        _view(
            [_piece("a", 200.0, 200.0, 80)],
            others=[prey],
            food=[(190.0, 200.0)],
            protected=True,
            personality=Personality(hunt_range=1.0, split_willingness=1.0),
        ),
        memory,
    )
    assert split is False
    assert memory.state != STATE_HUNT


def test_futile_chase_of_fleeing_prey_stays_graze():
    memory = new_memory(0)
    prey = _player("p", [_piece("p", 350.0, 200.0, 20)])
    decide(
        _view(
            [_piece("a", 200.0, 200.0, 80)],
            others=[prey],
            prev_positions={"p": (330.0, 200.0)},
            food=[(210.0, 200.0)],
            personality=Personality(hunt_range=1.0, split_willingness=0.0),
        ),
        memory,
    )
    assert memory.state == STATE_GRAZE


# --- plumbing helpers -----------------------------------------------------


def test_distinct_names_suffix_and_truncate():
    names = distinct_names("bot", 5)
    assert names == ["bot", "bot2", "bot3", "bot4", "bot5"]
    long = distinct_names("a" * NAME_MAX_LEN, 2)
    assert len(long[0]) == NAME_MAX_LEN
    assert len(long[1]) == NAME_MAX_LEN
    assert long[1].endswith("2")


def test_assign_colors_random_and_pinned_single():
    rng = __import__("random").Random(0)
    colors = assign_colors(3, None, rng)
    assert len(set(colors)) == 3
    assert all(c.startswith("#") and len(c) == 7 for c in colors)
    pinned = assign_colors(1, "#ff00aa", rng)
    assert pinned == ["#ff00aa"]


def test_join_holds_food_and_sends_input_after_welcome():
    from aiohttp.test_utils import TestClient, TestServer

    from server.config import DEFAULT_COLOR
    from server.main import create_app, emit_tick
    from server.world import World

    async def body():
        world = World(seed=0, food_target=0)
        world.food["p"] = __import__("server.models", fromlist=["Food"]).Food(
            id="p", x=100, y=100
        )
        app = create_app(world, autotick=False)
        async with TestServer(app) as server:
            async with TestClient(server) as client:
                ws = await client.ws_connect("/ws")
                await ws.send_json(
                    {"type": "join", "name": "bot", "color": DEFAULT_COLOR}
                )
                welcome = await asyncio.wait_for(ws.receive_json(), timeout=1)
                assert welcome["type"] == "welcome"
                await emit_tick(app, 1.0 / 30.0)
                first = await asyncio.wait_for(ws.receive_json(), timeout=1)
                second = await asyncio.wait_for(ws.receive_json(), timeout=1)
                kinds = {first["type"], second["type"]}
                assert "food" in kinds
                assert "state" in kinds
                await ws.send_json({"type": "input", "dx": 1.0, "dy": 0.0})
                await emit_tick(app, 1.0 / 30.0)
                await ws.close()

    asyncio.run(body())


def test_game_over_does_not_join_until_respawn_delay(monkeypatch):
    import bots.simple_bot as simple_bot
    from aiohttp.test_utils import TestClient, TestServer

    from server.config import SPAWN_INVULN_SECONDS
    from server.main import create_app, emit_tick
    from server.models import Food
    from server.world import World

    monkeypatch.setattr(simple_bot, "RESPAWN_SECONDS", 0.2)

    async def body():
        world = World(seed=0, food_target=0)
        world.food["pellet"] = Food(id="pellet", x=10, y=10)
        app = create_app(world, autotick=False)
        async with TestServer(app) as server:
            async with TestClient(server) as client:
                from bots.brain import FoodIndex, Personality
                from bots.simple_bot import BotClient

                stop = asyncio.Event()
                http = client.session
                bot = BotClient(
                    url=str(client.make_url("/ws")),
                    name="prey",
                    color="#00ff00",
                    personality=Personality(split_willingness=0.0),
                    food_index=FoodIndex(),
                    seed=1,
                    http=http,
                    stop=stop,
                )
                task = asyncio.create_task(bot.run())
                await asyncio.sleep(0.05)
                # Let the bot join.
                for _ in range(5):
                    await emit_tick(app, 1.0 / 30.0)
                    await asyncio.sleep(0.01)
                    if bot.self_id and bot.self_id in world.players:
                        break
                prey = world.players[bot.self_id]
                prey.spawn_time = -SPAWN_INVULN_SECONDS
                hunter = world.spawn_player("hunter", prey.pieces[0].x, prey.pieces[0].y, 400)
                hunter.spawn_time = -SPAWN_INVULN_SECONDS
                joins_before = bot.self_id
                await emit_tick(app, 1.0 / 30.0)
                await asyncio.sleep(0.05)
                mid_id = bot.self_id
                assert mid_id is None
                await asyncio.sleep(0.25)
                for _ in range(8):
                    await emit_tick(app, 1.0 / 30.0)
                    await asyncio.sleep(0.01)
                    if bot.self_id:
                        break
                assert bot.self_id is not None
                assert bot.self_id != joins_before
                stop.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(body())


def test_two_clients_get_different_names_and_colors():
    names = distinct_names("bot", 2)
    colors = assign_colors(2, None, __import__("random").Random(1))
    assert names[0] != names[1]
    assert colors[0] != colors[1]
