"""Protocol helpers: parse, serialize, join, death. No WebSocket."""

import pytest
from conftest import add_player

from server.config import DEFAULT_COLOR, INITIAL_PLAYER_MASS, NAME_MAX_LEN
from server.loop import process_tick
from server.protocol import (
    ClientSession,
    handle_join,
    handle_message,
    parse_client_message,
    serialize_state,
    update_and_eliminate,
)
from server.world import World


def test_parse_join_normalizes_name_and_color():
    msg = parse_client_message(
        {"type": "join", "name": "  Alice  ", "color": "#FFAA00"}
    )

    assert msg == {"type": "join", "name": "Alice", "color": "#ffaa00"}


def test_parse_join_rejects_empty_and_invalid_color():
    msg = parse_client_message({"type": "join", "name": "   ", "color": "red"})

    assert msg["name"] == "blob"
    assert msg["color"] == DEFAULT_COLOR


def test_parse_join_truncates_long_names():
    msg = parse_client_message({"type": "join", "name": "a" * 40})

    assert msg["name"] == "a" * NAME_MAX_LEN


def test_parse_input_requires_finite_numbers():
    assert parse_client_message({"type": "input", "dx": 1, "dy": -0.5}) == {
        "type": "input",
        "dx": 1.0,
        "dy": -0.5,
    }
    assert parse_client_message({"type": "input", "dx": float("nan"), "dy": 0}) is None
    assert parse_client_message({"type": "input", "dx": float("inf"), "dy": 0}) is None
    assert parse_client_message({"type": "input", "dx": True, "dy": 0}) is None
    assert parse_client_message({"type": "input", "dx": "1", "dy": 0}) is None
    assert parse_client_message('{"type": "input", "dx": null, "dy": 0}') is None


def test_parse_drops_malformed_json_and_unknown_types():
    assert parse_client_message("not json") is None
    assert parse_client_message({"type": "explode"}) is None
    assert parse_client_message({"type": "split"}) == {"type": "split"}


def test_serialize_state_matches_wire_shape_and_includes_color(world):
    player = add_player(world, "A", 100.0, 200.0, mass=40)
    player.color = "#ff0000"
    world.food.clear()

    payload = serialize_state(world)

    assert payload["type"] == "state"
    assert payload["players"] == [
        {
            "id": player.id,
            "name": "A",
            "color": "#ff0000",
            "pieces": [
                {
                    "piece_id": player.pieces[0].piece_id,
                    "x": 100.0,
                    "y": 200.0,
                    "mass": 40,
                }
            ],
        }
    ]
    assert payload["food"] == []


def test_join_spawns_at_initial_mass_and_welcome_id_matches(world):
    session = ClientSession()
    reply = handle_join(
        world, session, {"type": "join", "name": "A", "color": "#abcdef"}
    )
    player = world.players[session.player_id]

    assert reply == {"type": "welcome", "id": player.id}
    assert player.name == "A"
    assert player.color == "#abcdef"
    assert player.pieces[0].mass == INITIAL_PLAYER_MASS
    assert session.peak_mass == INITIAL_PLAYER_MASS
    assert session.spawn_sim_time == world.now


def test_second_join_while_alive_is_ignored(world):
    session = ClientSession()
    first = handle_join(
        world, session, {"type": "join", "name": "A", "color": DEFAULT_COLOR}
    )
    second = handle_join(
        world, session, {"type": "join", "name": "B", "color": "#000000"}
    )

    assert second is None
    assert len(world.players) == 1
    assert world.players[first["id"]].name == "A"


def test_input_and_split_are_ignored_when_not_playing(world):
    session = ClientSession()
    handle_message(world, session, {"type": "input", "dx": 1.0, "dy": 0.0})
    handle_message(world, session, {"type": "split"})

    assert world.players == {}


def test_empty_piece_list_sends_game_over_and_removes_the_player(world):
    session = ClientSession()
    welcome = handle_join(
        world, session, {"type": "join", "name": "A", "color": DEFAULT_COLOR}
    )
    player = world.players[welcome["id"]]
    session.peak_mass = 80.0
    session.spawn_sim_time = 0.0
    world.now = 2.5
    player.pieces.clear()

    deaths = update_and_eliminate(world, [session])

    assert welcome["id"] not in world.players
    assert session.player_id is None
    assert deaths == [
        (
            session,
            {
                "type": "game_over",
                "peak_mass": 80.0,
                "survival_seconds": 2.5,
            },
        )
    ]


def test_empty_piece_player_without_a_session_is_still_removed(world):
    player = add_player(world, "ghost", 100.0, 100.0)
    player.pieces.clear()

    deaths = update_and_eliminate(world, [])

    assert deaths == []
    assert player.id not in world.players


def test_join_after_death_respawns(world):
    session = ClientSession()
    first = handle_join(
        world, session, {"type": "join", "name": "A", "color": "#ff0000"}
    )
    world.players[first["id"]].pieces.clear()
    update_and_eliminate(world, [session])

    second = handle_join(
        world, session, {"type": "join", "name": "A", "color": "#00ff00"}
    )

    assert second["id"] != first["id"]
    assert second["id"] in world.players
    assert world.players[second["id"]].color == "#00ff00"


def test_process_tick_advances_sim_time_and_snapshots(world):
    add_player(world, "A", 100.0, 100.0)
    payload, deaths = process_tick(world, [], dt=0.05)

    assert world.now == pytest.approx(0.05)
    assert deaths == []
    assert payload["type"] == "state"
    assert len(payload["players"]) == 1
