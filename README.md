# blobby

An agar.io-style multiplayer game. Authoritative Python server, browser + Python-bot clients, WebSocket/JSON protocol. Runs as a POC on a local Ubuntu VM.

Phase 3 is built: open `http://localhost:8000` for the canvas client (name, color, Play / Spectate). A second tab is Phase 4. Everything the project intends to do, along with a phased checklist and where things currently stand, lives in [docs/GUIDEBOOK.md](docs/GUIDEBOOK.md).

## Run it

```
pip install -r requirements-dev.txt
python -m pytest                 # simulation, protocol, and WebSocket tests
python -m server.main            # game server: client at http://localhost:8000, WebSocket at /ws
python -m bots.simple_bot --count 5
python -m bots.simple_bot --count 30   # living-lobby stress; tick Hz is logged by the server
python -m tools.probe_client --name A
python -m server.demo            # Phase 1 console harness: two hardcoded players, no networking
python -m tools.record --serve   # record every verification scenario and open the browser viewer
```

`BLOBBY_HOST` and `BLOBBY_PORT` override the bind address (default `0.0.0.0:8000`). `BLOBBY_DEBUG_SPAWN=x,y` pins every live join to that point so two tabs can reproduce spawn overlap; `BLOBBY_DEBUG_MASS=280` pins spawn mass so a life can split through the 8-piece cap without eating. Both are local feel-testing only, not protocol fields. A spectator probe uses `--spectate` and never sends `join`.

Runtime-only install (no pytest):

```
pip install -r requirements.txt
```

## Layout

- `docs/` — the build guidebook and the source build plan it follows.
- `server/` — Python game server. `main.py` is the aiohttp process, `protocol.py` the wire format and session state, `loop.py` the tick clock, `demo.py` the Phase 1 printout. `simulation.py` and `world.py` are Phase 1's, extended in Phase 2 for player color, RNG spawn points and spawn invulnerability.
- `client/` — browser code. `index.html` + `game.js` is the live client at `/`. `render.js` and `style.css` are shared with the Phase 1 verification viewer, which is served only by `python -m tools.record --serve`.
- `tools/` — the scenario recorder, plus `probe_client.py` for Phase 2 protocol checks.
- `tests/` — pytest suites.
- `bots/` — Python bot clients (Phase 6).

## Source of truth

The design and build order come from [`docs/agario_build_plan.md`](docs/agario_build_plan.md), vendored so the repo is self-contained. `docs/GUIDEBOOK.md` mirrors its phase list, adds ownership tags (Agent / Human / Both) and checkboxes, and records every place this build has deliberately gone beyond the plan.

## Requirements

- Python 3.13
- git
