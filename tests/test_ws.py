"""WebSocket round-trips against the aiohttp app."""

import asyncio
import logging
from contextlib import asynccontextmanager

from aiohttp.test_utils import TestClient, TestServer

from server.config import (
    DEFAULT_COLOR,
    INITIAL_PLAYER_MASS,
    SPAWN_INVULN_SECONDS,
    TICK_RATE,
)
from server.models import Food
from server.main import _emit, create_app, emit_tick
from server.protocol import (
    ClientSession,
    FoodStream,
    handle_join,
    serialize_state,
    update_and_eliminate,
)
from server.world import World

DT = 1.0 / TICK_RATE


@asynccontextmanager
async def connected_app(world: World | None = None):
    if world is None:
        world = World(seed=0, food_target=0)
    app = create_app(world, autotick=False)
    async with TestServer(app) as server:
        async with TestClient(server) as client:
            yield app, client


async def _flush() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


async def _until(predicate, timeout: float = 1.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("timed out waiting for websocket handler")


async def _join(ws, name: str, color: str = DEFAULT_COLOR) -> dict:
    await ws.send_json({"type": "join", "name": name, "color": color})
    reply = await asyncio.wait_for(ws.receive_json(), timeout=1)
    assert reply["type"] == "welcome"
    return reply


def _expire_spawn_protection(*players) -> None:
    """Age past the spawn invulnerability a live `join` grants.

    These tests stage a kill on the tick after joining, which the protection
    exists to prevent. They are about what the protocol does once someone dies,
    so opt out of the timer rather than simulating three seconds of it.
    """
    for player in players:
        player.spawn_time = -SPAWN_INVULN_SECONDS


async def _state_after_tick(app, ws, dt: float = DT) -> dict:
    await emit_tick(app, dt)
    while True:
        payload = await asyncio.wait_for(ws.receive_json(), timeout=1)
        if payload["type"] == "state":
            return payload


def test_join_spawns_and_welcome_id_matches_state():
    async def body():
        async with connected_app() as (app, client):
            ws = await client.ws_connect("/ws")
            welcome = await _join(ws, "A", "#ff0000")
            state = await _state_after_tick(app, ws)
            await ws.close()

        assert len(state["players"]) == 1
        player = state["players"][0]
        assert player["id"] == welcome["id"]
        assert player["name"] == "A"
        assert player["color"] == "#ff0000"
        assert player["pieces"][0]["mass"] == INITIAL_PLAYER_MASS

    asyncio.run(body())


def test_spectator_sees_state_and_is_not_a_player():
    async def body():
        async with connected_app() as (app, client):
            spectator = await client.ws_connect("/ws")
            player_ws = await client.ws_connect("/ws")
            await _join(player_ws, "A")
            await emit_tick(app, DT)
            spec_state = await asyncio.wait_for(spectator.receive_json(), timeout=1)
            play_state = await asyncio.wait_for(player_ws.receive_json(), timeout=1)
            await spectator.close()
            await player_ws.close()

        assert spec_state == play_state
        assert [p["name"] for p in spec_state["players"]] == ["A"]

    asyncio.run(body())


def test_input_moves_on_the_next_tick():
    async def body():
        world = World(seed=0, food_target=0)
        async with connected_app(world) as (app, client):
            ws = await client.ws_connect("/ws")
            welcome = await _join(ws, "A")
            before = await _state_after_tick(app, ws)
            start = next(
                p["pieces"][0]["x"]
                for p in before["players"]
                if p["id"] == welcome["id"]
            )
            await ws.send_json({"type": "input", "dx": 1.0, "dy": 0.0})
            await _until(lambda: world.players[welcome["id"]].last_input == (1.0, 0.0))
            after = await _state_after_tick(app, ws)
            end = next(
                p["pieces"][0]["x"] for p in after["players"] if p["id"] == welcome["id"]
            )
            await ws.close()

        assert end > start

    asyncio.run(body())


def test_non_finite_input_does_not_land_in_last_input():
    async def body():
        world = World(seed=0, food_target=0)
        async with connected_app(world) as (app, client):
            ws = await client.ws_connect("/ws")
            welcome = await _join(ws, "A")
            await ws.send_str('{"type": "input", "dx": "nope", "dy": 0}')
            await _flush()
            player = world.players[welcome["id"]]
            await ws.close()

        assert player.last_input == (0.0, 0.0)

    asyncio.run(body())


def test_split_reaches_try_split():
    async def body():
        world = World(seed=0, food_target=0)
        async with connected_app(world) as (app, client):
            ws = await client.ws_connect("/ws")
            welcome = await _join(ws, "A")
            await ws.send_json({"type": "input", "dx": 1.0, "dy": 0.0})
            await ws.send_json({"type": "split"})
            await _until(lambda: len(world.players[welcome["id"]].pieces) == 2)
            player = world.players[welcome["id"]]
            assert len(player.pieces) == 2
            state = await _state_after_tick(app, ws)
            await ws.close()

        listed = next(p for p in state["players"] if p["id"] == welcome["id"])
        assert len(listed["pieces"]) == 2

    asyncio.run(body())


def test_last_piece_eat_sends_game_over_and_drops_the_player():
    async def body():
        world = World(seed=0, food_target=0)
        async with connected_app(world) as (app, client):
            predator_ws = await client.ws_connect("/ws")
            prey_ws = await client.ws_connect("/ws")
            predator_id = (await _join(predator_ws, "pred"))["id"]
            prey_id = (await _join(prey_ws, "prey"))["id"]
            predator = world.players[predator_id]
            prey = world.players[prey_id]
            prey.pieces[0].x = predator.pieces[0].x
            prey.pieces[0].y = predator.pieces[0].y
            predator.pieces[0].mass = 200
            prey.pieces[0].mass = 40
            _expire_spawn_protection(predator, prey)

            await emit_tick(app, DT)
            predator_state = await asyncio.wait_for(predator_ws.receive_json(), timeout=1)
            prey_first = await asyncio.wait_for(prey_ws.receive_json(), timeout=1)
            prey_second = await asyncio.wait_for(prey_ws.receive_json(), timeout=1)
            await predator_ws.close()
            await prey_ws.close()

        ids = {predator_state["type"], prey_first["type"], prey_second["type"]}
        assert "state" in ids
        assert "game_over" in ids
        state = predator_state if predator_state["type"] == "state" else prey_first
        if state["type"] != "state":
            state = prey_second
        assert [p["id"] for p in state["players"]] == [predator_id]
        game_over = next(
            msg
            for msg in (prey_first, prey_second)
            if msg["type"] == "game_over"
        )
        assert game_over["peak_mass"] >= 40
        assert game_over["survival_seconds"] >= 0
        assert prey_id not in world.players

    asyncio.run(body())


def test_close_removes_the_blob():
    async def body():
        world = World(seed=0, food_target=0)
        async with connected_app(world) as (app, client):
            ws = await client.ws_connect("/ws")
            welcome = await _join(ws, "A")
            assert welcome["id"] in world.players
            await ws.close()
            for _ in range(50):
                if welcome["id"] not in world.players:
                    break
                await asyncio.sleep(0.01)
            remaining = list(world.players)

        assert remaining == []

    asyncio.run(body())


def test_second_join_while_alive_does_not_duplicate():
    async def body():
        world = World(seed=0, food_target=0)
        async with connected_app(world) as (app, client):
            ws = await client.ws_connect("/ws")
            welcome = await _join(ws, "A")
            await ws.send_json({"type": "join", "name": "B", "color": "#000000"})
            await _flush()
            assert len(world.players) == 1
            state = await _state_after_tick(app, ws)
            assert len(state["players"]) == 1
            assert state["players"][0]["id"] == welcome["id"]
            assert state["players"][0]["name"] == "A"
            await ws.close()

    asyncio.run(body())


def test_join_after_death_respawns():
    async def body():
        world = World(seed=0, food_target=0)
        async with connected_app(world) as (app, client):
            predator_ws = await client.ws_connect("/ws")
            prey_ws = await client.ws_connect("/ws")
            predator_id = (await _join(predator_ws, "pred"))["id"]
            prey_id = (await _join(prey_ws, "prey"))["id"]
            predator = world.players[predator_id]
            prey = world.players[prey_id]
            prey.pieces[0].x = predator.pieces[0].x
            prey.pieces[0].y = predator.pieces[0].y
            predator.pieces[0].mass = 200
            _expire_spawn_protection(predator, prey)

            await emit_tick(app, DT)
            # Drain the death tick: state to both, game_over to prey.
            await asyncio.wait_for(predator_ws.receive_json(), timeout=1)
            first = await asyncio.wait_for(prey_ws.receive_json(), timeout=1)
            if first["type"] != "game_over":
                await asyncio.wait_for(prey_ws.receive_json(), timeout=1)

            welcome = await _join(prey_ws, "prey", "#00ff00")
            state = await _state_after_tick(app, predator_ws)
            await asyncio.wait_for(prey_ws.receive_json(), timeout=1)
            await predator_ws.close()
            await prey_ws.close()

        ids = {p["id"] for p in state["players"]}
        assert predator_id in ids
        assert welcome["id"] in ids
        assert prey_id not in ids
        revived = next(p for p in state["players"] if p["id"] == welcome["id"])
        assert revived["color"] == "#00ff00"

    asyncio.run(body())


def test_malformed_json_does_not_drop_the_connection():
    async def body():
        async with connected_app() as (app, client):
            ws = await client.ws_connect("/ws")
            await ws.send_str("not json")
            welcome = await _join(ws, "A")
            await ws.close()

        assert welcome["type"] == "welcome"

    asyncio.run(body())


def test_static_client_files_are_served_at_root():
    async def body():
        async with connected_app() as (app, client):
            response = await client.get("/render.js")
            assert response.status == 200
            body_text = await response.text()
            assert "interpolateStates" in body_text

    asyncio.run(body())


def test_connect_join_and_disconnect_are_logged(caplog):
    caplog.set_level(logging.INFO, logger="blobby")

    async def body():
        async with connected_app() as (app, client):
            ws = await client.ws_connect("/ws")
            await _join(ws, "A")
            await ws.close()
            await _flush()

    asyncio.run(body())

    text = caplog.text
    assert "connected" in text
    assert "join" in text and "player 'A'" in text
    assert "disconnected" in text


def test_stale_game_over_is_dropped_if_the_socket_already_respawned():
    """Join during the state broadcast must not deliver the previous life's game_over."""

    async def body():
        world = World(seed=0, food_target=0)
        session = ClientSession()
        handle_join(
            world, session, {"type": "join", "name": "A", "color": DEFAULT_COLOR}
        )
        world.players[session.player_id].pieces.clear()
        deaths = update_and_eliminate(world, [session])
        assert session.player_id is None
        assert deaths

        sent: list[dict] = []

        class FakeWS:
            closed = False

            async def send_json(self, payload: dict) -> None:
                if payload.get("type") == "state":
                    handle_join(
                        world,
                        session,
                        {"type": "join", "name": "A", "color": DEFAULT_COLOR},
                    )
                sent.append(payload)

        session.ws = FakeWS()
        await _emit([session], serialize_state(world), deaths)

        assert session.player_id is not None
        assert [message["type"] for message in sent] == ["state"]

    asyncio.run(body())


def _recording_session(world: World, joined: bool) -> tuple[ClientSession, list[dict]]:
    session = ClientSession()
    sent: list[dict] = []

    class FakeWS:
        closed = False

        async def send_json(self, payload: dict) -> None:
            sent.append(payload)

    session.ws = FakeWS()
    if joined:
        handle_join(
            world, session, {"type": "join", "name": "A", "color": DEFAULT_COLOR}
        )
    return session, sent


def test_state_naming_the_player_is_held_until_welcome_is_sent():
    """A join lands mid-broadcast; the snapshot names an id the client has not been given."""

    async def body():
        world = World(seed=0, food_target=0)
        session, sent = _recording_session(world, joined=True)
        assert session.welcome_sent is False

        await _emit([session], serialize_state(world), [])

        assert sent == []

        session.welcome_sent = True
        await _emit([session], serialize_state(world), [])

        assert [message["type"] for message in sent] == ["state"]

    asyncio.run(body())


def test_state_snapshotted_before_the_join_is_not_sent_after_welcome():
    """The follow-cam id must never be missing from the first state a player receives."""

    async def body():
        world = World(seed=0, food_target=0)
        session, sent = _recording_session(world, joined=False)
        stale = serialize_state(world)
        handle_join(
            world, session, {"type": "join", "name": "A", "color": DEFAULT_COLOR}
        )
        session.welcome_sent = True

        await _emit([session], stale, [])

        assert sent == []

        await _emit([session], serialize_state(world), [])

        assert [message["players"][0]["id"] for message in sent] == [session.player_id]

    asyncio.run(body())


def test_a_spectator_receives_every_frame():
    """The guard keys off a missing own player, which a spectator never has."""

    async def body():
        world = World(seed=0, food_target=0)
        player, _ = _recording_session(world, joined=True)
        spectator, sent = _recording_session(world, joined=False)

        await _emit([spectator], serialize_state(world), [])

        assert [message["type"] for message in sent] == ["state"]
        assert [p["id"] for p in sent[0]["players"]] == [player.player_id]

    asyncio.run(body())


def test_the_first_state_after_welcome_contains_the_welcomed_id():
    async def body():
        async with connected_app() as (app, client):
            ws = await client.ws_connect("/ws")
            welcome = await _join(ws, "A")
            state = await _state_after_tick(app, ws)
            await ws.close()

        assert welcome["id"] in {p["id"] for p in state["players"]}

    asyncio.run(body())


def test_two_players_each_see_the_other():
    async def body():
        async with connected_app() as (app, client):
            first = await client.ws_connect("/ws")
            second = await client.ws_connect("/ws")
            first_id = (await _join(first, "A", "#ff0000"))["id"]
            second_id = (await _join(second, "B", "#00ff00"))["id"]
            first_state = await _state_after_tick(app, first)
            second_state = await asyncio.wait_for(second.receive_json(), timeout=1)
            await first.close()
            await second.close()

        assert {p["id"] for p in first_state["players"]} == {first_id, second_id}
        assert first_state == second_state

    asyncio.run(body())


def test_join_without_a_color_uses_the_default():
    async def body():
        async with connected_app() as (app, client):
            ws = await client.ws_connect("/ws")
            await ws.send_json({"type": "join", "name": "A"})
            welcome = await asyncio.wait_for(ws.receive_json(), timeout=1)
            state = await _state_after_tick(app, ws)
            await ws.close()

        assert welcome["type"] == "welcome"
        listed = next(p for p in state["players"] if p["id"] == welcome["id"])
        assert listed["color"] == DEFAULT_COLOR

    asyncio.run(body())


def test_root_serves_the_menu():
    async def body():
        async with connected_app() as (app, client):
            response = await client.get("/")
            text = await response.text()
            assert response.status == 200
            assert "Play" in text
            assert "Spectate" in text
            assert "Game Over" in text
            assert 'id="game-canvas"' in text

    asyncio.run(body())


def test_game_client_files_are_served():
    async def body():
        async with connected_app() as (app, client):
            for path in ("/index.html", "/game.js", "/render.js", "/style.css"):
                response = await client.get(path)
                assert response.status == 200, path

    asyncio.run(body())


def test_game_js_is_wired_to_the_protocol():
    async def body():
        async with connected_app() as (app, client):
            response = await client.get("/game.js")
            text = await response.text()

        assert "WebSocket" in text
        assert "interpolateStates" in text
        assert '"join"' in text
        assert "game_over" in text
        assert "location.host" in text
        assert "hostname" not in text

    asyncio.run(body())


def test_render_js_keeps_player_color():
    async def body():
        async with connected_app() as (app, client):
            response = await client.get("/render.js")
            text = await response.text()

        assert "...player" in text or "...player," in text
        assert "...next" in text
        assert "colorsFromHex" in text
        assert "0.75" in text
        assert "screenToWorld" in text

    asyncio.run(body())


def test_viewer_and_recordings_are_not_public():
    async def body():
        async with connected_app() as (app, client):
            for path in (
                "/viewer.html",
                "/viewer.js",
                "/recording.js",
                "/recordings/index.json",
            ):
                response = await client.get(path)
                assert response.status == 404, path

    asyncio.run(body())


def test_food_is_sent_once_then_not_resent_while_unchanged():
    async def body():
        world = World(seed=0, food_target=0)
        world.food["a"] = Food(id="a", x=10.4, y=20.6)
        async with connected_app(world) as (app, client):
            ws = await client.ws_connect("/ws")
            await emit_tick(app, DT)
            food = await asyncio.wait_for(ws.receive_json(), timeout=1)
            state = await asyncio.wait_for(ws.receive_json(), timeout=1)
            await emit_tick(app, DT)
            second = await asyncio.wait_for(ws.receive_json(), timeout=1)
            await ws.close()

        assert food == {"type": "food", "version": 1, "food": [[10, 21]]}
        assert state["type"] == "state"
        assert "food" not in state
        assert second["type"] == "state"

    asyncio.run(body())


def test_eating_a_pellet_resends_food():
    async def body():
        world = World(seed=0, food_target=0)
        world.food["pellet"] = Food(id="pellet", x=500.0, y=500.0)
        async with connected_app(world) as (app, client):
            ws = await client.ws_connect("/ws")
            await emit_tick(app, DT)
            first = await asyncio.wait_for(ws.receive_json(), timeout=1)
            await asyncio.wait_for(ws.receive_json(), timeout=1)
            world.spawn_player("A", 500.0, 500.0, mass=40)
            await emit_tick(app, DT)
            resent = await asyncio.wait_for(ws.receive_json(), timeout=1)
            await asyncio.wait_for(ws.receive_json(), timeout=1)
            await ws.close()

        assert first["type"] == "food"
        assert first["food"] == [[500, 500]]
        assert resent == {"type": "food", "version": 2, "food": []}
        assert world.food == {}

    asyncio.run(body())


def test_a_late_joiner_receives_the_current_food_field():
    async def body():
        world = World(seed=0, food_target=0)
        world.food["a"] = Food(id="a", x=30.0, y=40.0)
        async with connected_app(world) as (app, client):
            first = await client.ws_connect("/ws")
            await emit_tick(app, DT)
            await asyncio.wait_for(first.receive_json(), timeout=1)
            await asyncio.wait_for(first.receive_json(), timeout=1)
            second = await client.ws_connect("/ws")
            await emit_tick(app, DT)
            late_food = await asyncio.wait_for(second.receive_json(), timeout=1)
            late_state = await asyncio.wait_for(second.receive_json(), timeout=1)
            early_state = await asyncio.wait_for(first.receive_json(), timeout=1)
            await first.close()
            await second.close()

        assert late_food == {"type": "food", "version": 1, "food": [[30, 40]]}
        assert late_state["type"] == "state"
        assert early_state["type"] == "state"

    asyncio.run(body())


def test_a_failed_food_send_is_retried_without_advancing_the_cursor():
    async def body():
        world = World(seed=0, food_target=0)
        world.food["a"] = Food(id="a", x=1.0, y=2.0)
        stream = FoodStream()
        stream.refresh(world)
        session = ClientSession()
        sent: list[dict] = []
        attempts = {"n": 0}

        class FakeWS:
            closed = False

            async def send_json(self, payload: dict) -> None:
                if payload.get("type") == "food" and attempts["n"] == 0:
                    attempts["n"] += 1
                    raise ConnectionError("dropped")
                sent.append(payload)

        session.ws = FakeWS()
        await _emit([session], serialize_state(world), [], stream)
        assert session.food_version == 0
        assert [message["type"] for message in sent] == ["state"]

        await _emit([session], serialize_state(world), [], stream)
        assert session.food_version == 1
        assert [message["type"] for message in sent] == ["state", "food", "state"]
        assert sent[1] == stream.payload

    asyncio.run(body())
