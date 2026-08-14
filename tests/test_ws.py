"""WebSocket round-trips against the aiohttp app."""

import asyncio
import logging
from contextlib import asynccontextmanager

from aiohttp.test_utils import TestClient, TestServer

from server.config import DEFAULT_COLOR, INITIAL_PLAYER_MASS, TICK_RATE
from server.main import create_app, emit_tick
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


async def _state_after_tick(app, ws, dt: float = DT) -> dict:
    await emit_tick(app, dt)
    payload = await asyncio.wait_for(ws.receive_json(), timeout=1)
    assert payload["type"] == "state"
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
