"""Protocol helpers: parse, serialize, join, death. No WebSocket."""

import json

import pytest
from conftest import add_player

from server.config import (
    BASE_SPEED,
    DEFAULT_COLOR,
    DEFAULT_NAME,
    FOOD_MASS,
    INITIAL_PLAYER_MASS,
    NAME_MAX_LEN,
    REMERGE_SECONDS,
    SPAWN_INVULN_SECONDS,
    SPEED_FALLOFF,
    SPEED_FLOOR_FRACTION,
    TICK_RATE,
    WORLD_HEIGHT,
    WORLD_WIDTH,
)
from server import simulation
from server.loop import process_tick
from server.models import Food
from server.protocol import (
    ClientSession,
    FoodStream,
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

    assert msg["name"] == DEFAULT_NAME
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
    assert payload["world"] == {"width": WORLD_WIDTH, "height": WORLD_HEIGHT}
    assert payload["tickRate"] == TICK_RATE
    assert payload["initialPlayerMass"] == INITIAL_PLAYER_MASS
    assert payload["baseSpeed"] == BASE_SPEED
    assert payload["speedFalloff"] == SPEED_FALLOFF
    assert payload["speedFloorFraction"] == SPEED_FLOOR_FRACTION
    assert payload["players"] == [
        {
            "id": player.id,
            "name": "A",
            "color": "#ff0000",
            "protected": False,
            "pieces": [
                {
                    "piece_id": player.pieces[0].piece_id,
                    "x": 100.0,
                    "y": 200.0,
                    "mass": 40,
                    "remerge_in": 0,
                }
            ],
        }
    ]
    assert "food" not in payload


def test_join_spawns_at_initial_mass_and_welcome_id_matches(world):
    session = ClientSession()
    reply = handle_join(
        world, session, {"type": "join", "name": "A", "color": "#abcdef"}
    )
    player = world.players[session.player_id]

    assert reply == {
        "type": "welcome",
        "id": player.id,
        "world": {"width": WORLD_WIDTH, "height": WORLD_HEIGHT},
        "tickRate": TICK_RATE,
        "initialPlayerMass": INITIAL_PLAYER_MASS,
        "baseSpeed": BASE_SPEED,
        "speedFalloff": SPEED_FALLOFF,
        "speedFloorFraction": SPEED_FLOOR_FRACTION,
    }
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


def _join_named(world: World, name: str) -> tuple[ClientSession, str]:
    session = ClientSession()
    handle_join(world, session, {"type": "join", "name": name, "color": DEFAULT_COLOR})
    return session, world.players[session.player_id].name


def test_a_taken_name_is_suffixed_rather_than_duplicated(world):
    _, first = _join_named(world, "jack")
    _, second = _join_named(world, "jack")
    _, third = _join_named(world, "jack")

    assert [first, second, third] == ["jack", "jack (2)", "jack (3)"]


def test_name_collision_ignores_case(world):
    _, first = _join_named(world, "jack")
    _, second = _join_named(world, "JACK")

    # The requested casing survives; only the collision test is case-blind.
    assert first == "jack"
    assert second == "JACK (2)"


def test_a_name_is_free_again_once_its_owner_is_gone(world):
    session, first = _join_named(world, "jack")
    world.remove_player(session.player_id)
    _, second = _join_named(world, "jack")

    assert first == second == "jack"


def test_a_suffixed_name_still_fits_the_length_cap(world):
    longest = "a" * NAME_MAX_LEN
    _, first = _join_named(world, longest)
    _, second = _join_named(world, longest)

    assert first == longest
    assert second == f"{'a' * (NAME_MAX_LEN - 4)} (2)"
    assert len(second) == NAME_MAX_LEN


def test_the_suffix_itself_is_not_duplicated(world):
    """A player literally named "jack (2)" must not collide into itself."""
    _, first = _join_named(world, "jack (2)")
    _, second = _join_named(world, "jack")
    _, third = _join_named(world, "jack")

    assert [first, second, third] == ["jack (2)", "jack", "jack (3)"]


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


def test_peak_mass_counts_mass_gained_on_the_tick_that_kills_you(world):
    """Eat a pellet and get eaten in the same step; the pellet still counts.

    `update_and_eliminate` runs after `simulation.step`, so the victim's pieces
    are already gone by the time peak mass is read. Reading `sum(pieces)` there
    reports the previous tick's total and silently loses the last mouthful.
    """
    session = ClientSession()
    handle_join(world, session, {"type": "join", "name": "prey", "color": DEFAULT_COLOR})
    prey = world.players[session.player_id]
    prey.spawn_time = -SPAWN_INVULN_SECONDS
    prey.pieces[0].mass = 100.0
    predator = add_player(world, "pred", prey.pieces[0].x, prey.pieces[0].y, mass=200)
    world.food["pellet"] = Food(
        id="pellet", x=prey.pieces[0].x, y=prey.pieces[0].y
    )

    _, deaths = process_tick(world, [session], dt=1.0 / TICK_RATE)

    assert world.food == {}
    assert predator.pieces[0].mass == pytest.approx(200 + 100 + FOOD_MASS)
    assert [payload["peak_mass"] for _, payload in deaths] == [100 + FOOD_MASS]


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


def test_join_grants_spawn_protection(world):
    session = ClientSession()
    world.now = 8.0
    handle_join(world, session, {"type": "join", "name": "A", "color": DEFAULT_COLOR})

    assert world.players[session.player_id].spawn_time == 8.0


def test_serialize_state_marks_a_live_join_protected_until_the_window_closes(world):
    """The ring on screen is this flag. A staged player stays false."""
    session = ClientSession()
    handle_join(world, session, {"type": "join", "name": "A", "color": DEFAULT_COLOR})
    player = world.players[session.player_id]

    listed = next(p for p in serialize_state(world)["players"] if p["id"] == player.id)
    assert listed["protected"] is True

    world.now = player.spawn_time + SPAWN_INVULN_SECONDS
    listed = next(p for p in serialize_state(world)["players"] if p["id"] == player.id)
    assert listed["protected"] is False


def test_serialize_state_keeps_protected_after_a_split(world):
    """The ring is a player flag, so every fragment of a protected split draws it."""
    session = ClientSession()
    handle_join(world, session, {"type": "join", "name": "A", "color": DEFAULT_COLOR})
    player = world.players[session.player_id]
    player.last_input = (1.0, 0.0)
    assert simulation.try_split(world, player) == 1

    listed = next(p for p in serialize_state(world)["players"] if p["id"] == player.id)
    assert listed["protected"] is True
    assert len(listed["pieces"]) == 2


def test_serialize_state_reports_remerge_remaining_after_a_split(world):
    """The countdown is remaining duration, not a timestamp the client can hurry."""
    session = ClientSession()
    handle_join(world, session, {"type": "join", "name": "A", "color": DEFAULT_COLOR})
    player = world.players[session.player_id]
    player.last_input = (1.0, 0.0)
    assert simulation.try_split(world, player) == 1

    listed = next(p for p in serialize_state(world)["players"] if p["id"] == player.id)
    assert [piece["remerge_in"] for piece in listed["pieces"]] == [
        pytest.approx(REMERGE_SECONDS),
        pytest.approx(REMERGE_SECONDS),
    ]

    world.now += 3.0
    listed = next(p for p in serialize_state(world)["players"] if p["id"] == player.id)
    assert [piece["remerge_in"] for piece in listed["pieces"]] == [
        pytest.approx(REMERGE_SECONDS - 3.0),
        pytest.approx(REMERGE_SECONDS - 3.0),
    ]

    world.now += REMERGE_SECONDS
    listed = next(p for p in serialize_state(world)["players"] if p["id"] == player.id)
    assert [piece["remerge_in"] for piece in listed["pieces"]] == [0, 0]


def test_join_honours_debug_spawn_env(world, monkeypatch):
    monkeypatch.setenv("BLOBBY_DEBUG_SPAWN", "100,200")
    session = ClientSession()
    handle_join(world, session, {"type": "join", "name": "A", "color": DEFAULT_COLOR})
    piece = world.players[session.player_id].pieces[0]
    assert piece.x == pytest.approx(100.0)
    assert piece.y == pytest.approx(200.0)


def test_join_ignores_a_malformed_debug_spawn_env(monkeypatch):
    monkeypatch.setenv("BLOBBY_DEBUG_SPAWN", "nope")
    pinned = World(seed=0, food_target=0)
    handle_join(
        pinned, ClientSession(), {"type": "join", "name": "A", "color": DEFAULT_COLOR}
    )
    monkeypatch.delenv("BLOBBY_DEBUG_SPAWN")
    natural = World(seed=0, food_target=0)
    handle_join(
        natural, ClientSession(), {"type": "join", "name": "A", "color": DEFAULT_COLOR}
    )
    a = next(iter(pinned.players.values())).pieces[0]
    b = next(iter(natural.players.values())).pieces[0]
    assert (a.x, a.y) == (b.x, b.y)


def test_join_honours_debug_mass_env(world, monkeypatch):
    monkeypatch.setenv("BLOBBY_DEBUG_MASS", "280")
    session = ClientSession()
    handle_join(world, session, {"type": "join", "name": "A", "color": DEFAULT_COLOR})
    player = world.players[session.player_id]

    assert player.pieces[0].mass == 280
    assert session.peak_mass == 280


def test_join_ignores_a_malformed_debug_mass_env(world, monkeypatch):
    monkeypatch.setenv("BLOBBY_DEBUG_MASS", "nope")
    session = ClientSession()
    handle_join(world, session, {"type": "join", "name": "A", "color": DEFAULT_COLOR})
    player = world.players[session.player_id]

    assert player.pieces[0].mass == INITIAL_PLAYER_MASS
    assert session.peak_mass == INITIAL_PLAYER_MASS


def test_a_joining_player_is_not_eaten_until_spawn_protection_expires(world):
    """The scenario the protection exists for: joining on top of a grown player.

    The hunter is spawned and left to outlive its own protection, then the prey
    joins directly inside it. Spawn positions come from the RNG, so this is a
    real join outcome, not a contrived one.
    """
    tick = 1.0 / TICK_RATE
    hunter_session = ClientSession()
    handle_join(
        world, hunter_session, {"type": "join", "name": "hunter", "color": DEFAULT_COLOR}
    )
    hunter = world.players[hunter_session.player_id]
    hunter.pieces[0].mass = 200
    while world.now < SPAWN_INVULN_SECONDS:
        process_tick(world, [hunter_session], tick)

    prey_session = ClientSession()
    handle_join(
        world, prey_session, {"type": "join", "name": "prey", "color": DEFAULT_COLOR}
    )
    prey_id = prey_session.player_id
    sessions = [hunter_session, prey_session]

    def stack_prey_on_hunter() -> None:
        prey = world.players[prey_id]
        prey.pieces[0].x = hunter.pieces[0].x
        prey.pieces[0].y = hunter.pieces[0].y

    joined_at = world.now
    while world.now < joined_at + SPAWN_INVULN_SECONDS - tick:
        stack_prey_on_hunter()
        _, deaths = process_tick(world, sessions, tick)
        assert deaths == []
        assert prey_id in world.players

    # Protection has lapsed; the same stacking is now fatal.
    stack_prey_on_hunter()
    _, deaths = process_tick(world, sessions, tick)

    assert [session for session, _ in deaths] == [prey_session]
    assert deaths[0][1]["type"] == "game_over"
    assert prey_id not in world.players


def test_process_tick_advances_sim_time_and_snapshots(world):
    add_player(world, "A", 100.0, 100.0)
    payload, deaths = process_tick(world, [], dt=0.05)

    assert world.now == pytest.approx(0.05)
    assert deaths == []
    assert payload["type"] == "state"
    assert len(payload["players"]) == 1
    assert "food" not in payload


def test_food_stream_sends_rounded_pairs_and_only_bumps_on_change(world):
    stream = FoodStream()
    world.food["a"] = Food(id="a", x=100.6, y=200.4)

    stream.refresh(world)
    assert stream.version == 1
    assert stream.payload == {
        "type": "food",
        "version": 1,
        "food": [[101, 200]],
    }
    assert json.loads(stream.encoded) == stream.payload

    stream.refresh(world)
    assert stream.version == 1

    del world.food["a"]
    stream.refresh(world)
    assert stream.version == 2
    assert stream.payload == {"type": "food", "version": 2, "food": []}


def test_state_frame_without_food_is_under_4kb():
    world = World(seed=1)
    world.spawn_food_to_target_count()
    world.spawn_player("A")
    payload = serialize_state(world)
    assert "food" not in payload
    assert len(json.dumps(payload)) < 4000
