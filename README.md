# blobby

An agar.io-style multiplayer game. Authoritative Python server, browser + Python-bot clients, WebSocket/JSON protocol. Runs as a POC on a local Ubuntu VM.

This repo is currently a scaffold — no game logic has been written yet. Everything the project intends to do, along with a phased checklist, lives in [GUIDEBOOK.md](GUIDEBOOK.md).

## Layout

- `server/` — Python game server (aiohttp + WebSocket, added over phases 1–2).
- `client/` — browser client (canvas + JS, added in phase 3).
- `bots/` — Python bot clients (added in phase 6).
- `tests/` — pytest suites (added as needed).

## Source of truth

The design and build order comes from [`../../../Downloads (E)/agario_build_plan.md`](../../Downloads%20(E)/agario_build_plan.md). `GUIDEBOOK.md` mirrors its phase list and adds ownership tags (Agent / Human / Both) plus checkboxes.

## Requirements (so far)

- Python 3.13
- git

Phase 2 adds `aiohttp` to `requirements.txt`.
