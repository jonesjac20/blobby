# blobby

An agar.io-style multiplayer game. Authoritative Python server, browser + Python-bot clients, WebSocket/JSON protocol. Runs as a POC on a local Ubuntu VM.

Phase 1 is built: the simulation runs standalone, with no networking yet. Movement, food, the 1.25x eat rule, splitting, the split kick and remerging all work and are covered by tests. Everything the project intends to do, along with a phased checklist and where things currently stand, lives in [docs/GUIDEBOOK.md](docs/GUIDEBOOK.md).

## Run it

```
python -m pytest              # the Phase 1 test suite
python -m server.main         # the console harness: two players, ~18s is enough to see a split and a remerge
python -m tools.record --serve  # record every verification scenario and open the browser viewer
```

Only `pytest` is needed; the server itself is standard library so far.

```
pip install -r requirements.txt
```

## Layout

- `docs/` — the build guidebook and the source build plan it follows.
- `server/` — Python game server. Phase 1 is the simulation and a console harness; aiohttp and the WebSocket endpoint arrive in Phase 2.
- `client/` — browser code. Currently the Phase 1 verification viewer plus `render.js`, the shared canvas renderer the Phase 3 game client will import.
- `tools/` — the scenario recorder that feeds the viewer.
- `tests/` — pytest suites.
- `bots/` — Python bot clients (Phase 6).

## Source of truth

The design and build order come from [`docs/agario_build_plan.md`](docs/agario_build_plan.md), vendored so the repo is self-contained. `docs/GUIDEBOOK.md` mirrors its phase list, adds ownership tags (Agent / Human / Both) and checkboxes, and records every place this build has deliberately gone beyond the plan.

## Requirements

- Python 3.13
- git
