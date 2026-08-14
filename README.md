# blobby

An agar.io-style multiplayer game. Authoritative Python server, browser + Python-bot clients, WebSocket/JSON protocol. Runs as a POC on a local Ubuntu VM.

Phase 2 is built: the simulation from Phase 1 now sits behind an aiohttp WebSocket server. A browser client is Phase 3. Everything the project intends to do, along with a phased checklist and where things currently stand, lives in [docs/GUIDEBOOK.md](docs/GUIDEBOOK.md).

## Run it

```
pip install -r requirements-dev.txt
python -m pytest                 # simulation, protocol, and WebSocket tests
python -m server.main            # game server: static files at /, WebSocket at /ws (0.0.0.0:8000)
python -m tools.probe_client --name A
python -m server.demo            # Phase 1 console harness: two hardcoded players, no networking
python -m tools.record --serve   # record every verification scenario and open the browser viewer
```

`BLOBBY_HOST` and `BLOBBY_PORT` override the bind address (default `0.0.0.0:8000`). A spectator probe uses `--spectate` and never sends `join`.

Runtime-only install (no pytest):

```
pip install -r requirements.txt
```

## Layout

- `docs/` — the build guidebook and the source build plan it follows.
- `server/` — Python game server. `main.py` is the aiohttp process; `demo.py` is the Phase 1 printout; `simulation.py` / `world.py` are unchanged from Phase 1.
- `client/` — browser code. Currently the Phase 1 verification viewer plus `render.js`, the shared canvas renderer the Phase 3 game client will import. Served by the game server at `/`.
- `tools/` — the scenario recorder, plus `probe_client.py` for Phase 2 protocol checks.
- `tests/` — pytest suites.
- `bots/` — Python bot clients (Phase 6).

## Source of truth

The design and build order come from [`docs/agario_build_plan.md`](docs/agario_build_plan.md), vendored so the repo is self-contained. `docs/GUIDEBOOK.md` mirrors its phase list, adds ownership tags (Agent / Human / Both) and checkboxes, and records every place this build has deliberately gone beyond the plan.

## Requirements

- Python 3.13
- git
