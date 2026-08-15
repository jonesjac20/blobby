"""aiohttp game server: game files at `/`, WebSocket at `/ws`, tick loop.

Bind address is BLOBBY_HOST / BLOBBY_PORT (default 0.0.0.0:8000). The Phase 1
console harness lives in server.demo. The verification viewer is not served
here; `python -m tools.record --serve` still opens it on 8080.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from pathlib import Path

from aiohttp import WSMsgType, web

from server.config import HOST, PORT
from server.loop import process_tick, tick_loop
from server.protocol import ClientSession, FoodStream, handle_message, parse_client_message
from server.world import World

log = logging.getLogger("blobby")

CLIENT_DIR = Path(__file__).resolve().parent.parent / "client"
# Whitelist rather than a denylist: this process faces the internet in Phase 7,
# so a file added to client/ has to be published on purpose. viewer.html,
# recording.js and client/recordings/ stay off it.
PUBLIC_FILES = frozenset({"index.html", "game.js", "render.js", "style.css"})
WS_HEARTBEAT_SECONDS = 20.0

WORLD_KEY = web.AppKey("world", World)
SESSIONS_KEY = web.AppKey("sessions", list)
FOOD_KEY = web.AppKey("food_stream", FoodStream)
STOP_KEY = web.AppKey("stop_ticks", asyncio.Event)
TASK_KEY = web.AppKey("tick_task", asyncio.Task)


def _state_is_stale_for(session: ClientSession, payload: dict) -> bool:
    """Whether this snapshot would describe a world the socket cannot be in yet.

    A join can land in the middle of the loop below, between `serialize_state`
    and this socket's turn. Both orderings are wrong for a playing client:
    a snapshot naming the new player before `welcome` gives it an id it has
    not been told is its own, and the pre-join snapshot after `welcome` omits
    the player entirely, so a follow-cam finds nothing. Spectators have no
    player to be missing and always get the frame.
    """
    if session.player_id is None:
        return False
    if not session.welcome_sent:
        return True
    return all(player["id"] != session.player_id for player in payload["players"])


async def _emit(
    sessions: list[ClientSession],
    payload: dict,
    deaths: list[tuple[ClientSession, dict]],
    stream: FoodStream | None = None,
) -> None:
    for session in list(sessions):
        if session.ws is None or session.ws.closed:
            continue
        if stream is not None and session.food_version != stream.version:
            # Record the version only after a successful send. A raise that
            # `_emit` swallows then retries next tick; a join-window state
            # skip cannot desync food because food is not in `state`.
            try:
                await session.ws.send_json(stream.payload)
            except Exception:
                pass
            else:
                session.food_version = stream.version
        if _state_is_stale_for(session, payload):
            continue
        try:
            await session.ws.send_json(payload)
        except Exception:
            continue
    for session, message in deaths:
        # process_tick clears player_id before this await; a join in the
        # window above starts a new life. That game_over belongs to the
        # previous one and must not arrive after welcome.
        if session.player_id is not None:
            continue
        if session.ws is None or session.ws.closed:
            continue
        try:
            await session.ws.send_json(message)
        except Exception:
            continue


async def broadcast(
    app: web.Application, payload: dict, deaths: list[tuple[ClientSession, dict]]
) -> None:
    """Refresh the food field, then send this tick to every socket."""
    stream = app[FOOD_KEY]
    stream.refresh(app[WORLD_KEY])
    await _emit(app[SESSIONS_KEY], payload, deaths, stream)


async def emit_tick(app: web.Application, dt: float) -> None:
    """Drive one tick and broadcast. Tests use this instead of the wall clock."""
    payload, deaths = process_tick(app[WORLD_KEY], app[SESSIONS_KEY], dt)
    await broadcast(app, payload, deaths)


def _peer(request: web.Request) -> str:
    return request.remote or "?"


def _who(session: ClientSession) -> str:
    if session.player_id is not None:
        return f"player {session.name!r} id={session.player_id[:8]}"
    if session.name:
        return f"menu {session.name!r}"
    return "spectator"


async def index(_request: web.Request) -> web.FileResponse:
    return web.FileResponse(CLIENT_DIR / "index.html")


async def client_file(request: web.Request) -> web.FileResponse:
    name = request.match_info["name"]
    if name not in PUBLIC_FILES:
        raise web.HTTPNotFound()
    path = CLIENT_DIR / name
    if not path.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(path)


async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=WS_HEARTBEAT_SECONDS)
    await ws.prepare(request)
    session = ClientSession(ws=ws)
    sessions: list[ClientSession] = request.app[SESSIONS_KEY]
    world: World = request.app[WORLD_KEY]
    sessions.append(session)
    peer = _peer(request)
    log.info("connected peer=%s sockets=%d", peer, len(sessions))
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                parsed = parse_client_message(msg.data)
                if parsed is None:
                    continue
                reply = handle_message(world, session, parsed)
                if reply is not None:
                    await ws.send_json(reply)
                    if reply.get("type") == "welcome":
                        session.welcome_sent = True
                        log.info(
                            "join %s peer=%s players=%d sockets=%d",
                            _who(session),
                            peer,
                            len(world.players),
                            len(sessions),
                        )
            elif msg.type == WSMsgType.ERROR:
                log.info("websocket error peer=%s %s", peer, ws.exception())
                break
            elif msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSING):
                break
    finally:
        try:
            sessions.remove(session)
        except ValueError:
            pass
        who = _who(session)
        if session.player_id is not None:
            world.remove_player(session.player_id)
            session.player_id = None
        log.info("disconnected %s peer=%s sockets=%d", who, peer, len(sessions))
    return ws


def create_app(
    world: World | None = None, *, autotick: bool = True
) -> web.Application:
    if world is None:
        world = World(seed=time.time_ns())
        world.spawn_food_to_target_count()

    app = web.Application()
    app[WORLD_KEY] = world
    app[SESSIONS_KEY] = []
    app[FOOD_KEY] = FoodStream()
    app.router.add_get("/ws", websocket_handler)
    app.router.add_get("/", index)
    app.router.add_get("/{name}", client_file)

    if autotick:
        app.on_startup.append(_start_ticks)
        app.on_cleanup.append(_stop_ticks)
    return app


async def _start_ticks(app: web.Application) -> None:
    stop = asyncio.Event()
    app[STOP_KEY] = stop

    async def emit(payload: dict, deaths: list[tuple[ClientSession, dict]]) -> None:
        await broadcast(app, payload, deaths)

    app[TASK_KEY] = asyncio.create_task(
        tick_loop(app[WORLD_KEY], app[SESSIONS_KEY], emit=emit, stop=stop)
    )


async def _stop_ticks(app: web.Application) -> None:
    stop = app.get(STOP_KEY)
    if stop is not None:
        stop.set()
    task = app.get(TASK_KEY)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
    )
    web.run_app(create_app(), host=HOST, port=PORT)


if __name__ == "__main__":
    main()
