"""Mass-cap burst into inert corpses."""

import math

import pytest
from conftest import add_player, advance

from server.config import (
    BOT_BURST_MASS,
    BOT_BURST_REMNANT_MASS,
    BURST_SPLIT_SECONDS,
    EAT_OVERLAP,
    FOOD_MASS,
    PLAYER_BURST_MASS,
    PLAYER_BURST_REMNANT_MASS,
    SPAWN_INVULN_SECONDS,
    TICK_RATE,
    burst_nav_gap,
)
from server.loop import process_tick
from server.models import Food
from server.protocol import (
    ClientSession,
    handle_join,
    parse_client_message,
    serialize_state,
)
from server.simulation import engulfment, radius_for_mass, step


def test_parse_join_bot_flag_is_strict_true():
    assert parse_client_message({"type": "join", "name": "A", "bot": True})["bot"] is True
    assert parse_client_message({"type": "join", "name": "A"})["bot"] is False
    assert parse_client_message({"type": "join", "name": "A", "bot": "yes"})["bot"] is False
    assert parse_client_message({"type": "join", "name": "A", "bot": 1})["bot"] is False


def test_join_bot_true_marks_the_player(world):
    session = ClientSession()
    handle_join(
        world,
        session,
        {"type": "join", "name": "A", "color": "#abcdef", "bot": True},
    )
    assert world.players[session.player_id].bot is True


def test_human_at_bot_burst_mass_does_not_peel(world):
    player = add_player(world, "human", 1000.0, 1000.0, mass=BOT_BURST_MASS)
    player.bot = False
    step(world, 1.0 / 30.0)
    assert len(world.players) == 1
    assert pytest.approx(sum(p.mass for p in player.pieces)) == BOT_BURST_MASS


def test_human_at_player_burst_mass_peels_to_remnant_and_inert_corpse(world):
    player = add_player(world, "human", 1000.0, 1000.0, mass=PLAYER_BURST_MASS)
    player.bot = False
    player.color = "#00aaff"
    step(world, 1.0 / 30.0)

    live = [p for p in world.players.values() if not p.inert]
    dead = [p for p in world.players.values() if p.inert]
    assert len(live) == 1
    assert len(dead) == 1
    assert pytest.approx(sum(p.mass for p in live[0].pieces)) == PLAYER_BURST_REMNANT_MASS
    assert pytest.approx(sum(p.mass for p in dead[0].pieces)) == (
        PLAYER_BURST_MASS - PLAYER_BURST_REMNANT_MASS
    )
    assert dead[0].color == "#00aaff"
    listed = next(p for p in serialize_state(world)["players"] if p["id"] == dead[0].id)
    assert listed["inert"] is True


def test_peak_mass_survives_a_player_burst(world):
    session = ClientSession()
    handle_join(world, session, {"type": "join", "name": "human", "color": "#00aaff"})
    player = world.players[session.player_id]
    player.bot = False
    player.pieces[0].mass = PLAYER_BURST_MASS
    player.last_total_mass = PLAYER_BURST_MASS

    payload, deaths = process_tick(world, [session], dt=1.0 / TICK_RATE)

    assert deaths == []
    live = next(row for row in payload["players"] if row["id"] == player.id)
    assert pytest.approx(sum(piece["mass"] for piece in live["pieces"])) == (
        PLAYER_BURST_REMNANT_MASS
    )
    assert pytest.approx(live["peak_mass"]) == PLAYER_BURST_MASS
    assert pytest.approx(session.peak_mass) == PLAYER_BURST_MASS


def test_game_over_peak_mass_is_the_pre_burst_high_water(world):
    session = ClientSession()
    handle_join(world, session, {"type": "join", "name": "human", "color": "#00aaff"})
    player = world.players[session.player_id]
    player.bot = False
    player.pieces[0].mass = PLAYER_BURST_MASS
    player.last_total_mass = PLAYER_BURST_MASS
    process_tick(world, [session], dt=1.0 / TICK_RATE)

    remnant = world.players[session.player_id]
    remnant.spawn_time = -SPAWN_INVULN_SECONDS
    x, y = remnant.pieces[0].x, remnant.pieces[0].y
    predator = add_player(world, "pred", x, y, mass=PLAYER_BURST_REMNANT_MASS * 3)
    predator.spawn_time = -SPAWN_INVULN_SECONDS

    _, deaths = process_tick(world, [session], dt=1.0 / TICK_RATE)

    assert [payload["peak_mass"] for _, payload in deaths] == [
        pytest.approx(PLAYER_BURST_MASS)
    ]


def test_bot_at_burst_mass_peels_to_remnant_and_inert_corpse(world):
    player = add_player(world, "bot", 1000.0, 1000.0, mass=BOT_BURST_MASS)
    player.bot = True
    player.color = "#ff00aa"
    step(world, 1.0 / 30.0)

    live = [p for p in world.players.values() if not p.inert]
    dead = [p for p in world.players.values() if p.inert]
    assert len(live) == 1
    assert len(dead) == 1
    assert pytest.approx(sum(p.mass for p in live[0].pieces)) == BOT_BURST_REMNANT_MASS
    assert pytest.approx(sum(p.mass for p in dead[0].pieces)) == (
        BOT_BURST_MASS - BOT_BURST_REMNANT_MASS
    )
    assert dead[0].color == "#ff00aa"
    listed = next(p for p in serialize_state(world)["players"] if p["id"] == dead[0].id)
    assert listed["inert"] is True


def test_inert_cannot_eat_a_smaller_player(world):
    corpse = add_player(world, "dead", 1000.0, 1000.0, mass=20000)
    corpse.inert = True
    corpse.last_burst_split = world.now
    prey = add_player(world, "snack", 1000.0, 1000.0, mass=50)
    step(world, 1.0 / 30.0)
    assert prey.id in world.players
    assert prey.pieces
    assert pytest.approx(corpse.pieces[0].mass) == 20000


def test_inert_cannot_eat_food(world):
    corpse = add_player(world, "dead", 500.0, 500.0, mass=200)
    corpse.inert = True
    corpse.last_burst_split = world.now
    world.food["pellet"] = Food(id="pellet", x=500.0, y=500.0)
    world.food_target = 0
    step(world, 1.0 / 30.0)
    assert pytest.approx(corpse.pieces[0].mass) == 200
    assert "pellet" in world.food or len(world.food) >= 1


def test_larger_player_can_eat_inert(world):
    corpse = add_player(world, "dead", 1000.0, 1000.0, mass=40)
    corpse.inert = True
    corpse.last_burst_split = world.now
    predator = add_player(world, "eater", 1000.0, 1000.0, mass=200)
    assert engulfment(predator.pieces[0], corpse.pieces[0]) >= EAT_OVERLAP
    step(world, 1.0 / 30.0)
    assert not corpse.pieces
    assert pytest.approx(predator.pieces[0].mass) == 240


def test_inert_split_ignores_min_split_mass_and_does_not_remerge(world):
    corpse = add_player(world, "dead", 1000.0, 1000.0, mass=20)
    corpse.inert = True
    corpse.last_burst_split = -BURST_SPLIT_SECONDS
    step(world, 0.05)
    assert len(corpse.pieces) == 2
    assert all(pytest.approx(piece.mass) == 10 for piece in corpse.pieces)
    advance(world, BURST_SPLIT_SECONDS + 1.0, 0.05)
    assert len(corpse.pieces) >= 2


def test_inert_does_not_split_when_half_would_be_below_one(world):
    corpse = add_player(world, "dead", 1000.0, 1000.0, mass=1.5)
    corpse.inert = True
    corpse.last_burst_split = -BURST_SPLIT_SECONDS
    step(world, 0.05)
    assert len(corpse.pieces) == 1
    assert pytest.approx(corpse.pieces[0].mass) == 1.5


def test_inert_split_places_a_navigable_gap(world):
    mass = 800.0
    corpse = add_player(world, "dead", 1000.0, 1000.0, mass=mass)
    corpse.inert = True
    corpse.last_burst_split = -BURST_SPLIT_SECONDS
    step(world, 0.05)
    assert len(corpse.pieces) == 2
    a, b = corpse.pieces
    half_r = radius_for_mass(mass / 2.0)
    distance = math.hypot(a.x - b.x, a.y - b.y)
    assert distance >= 2.0 * half_r + burst_nav_gap() * 0.5


def test_socketless_inert_death_is_not_game_over(world):
    corpse = add_player(world, "dead", 1000.0, 1000.0, mass=40)
    corpse.inert = True
    corpse.last_burst_split = world.now
    predator = add_player(world, "eater", 1000.0, 1000.0, mass=200)
    session = ClientSession()
    session.player_id = predator.id
    _, deaths = process_tick(world, [session], 1.0 / 30.0)
    assert deaths == []
    assert corpse.id not in world.players


def test_food_mass_is_eight():
    assert FOOD_MASS == 8
