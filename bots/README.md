# bots

Bot clients added in phase 6. Each bot is an ordinary WebSocket client speaking the same `join` / `input` / `split` protocol as a human. A fleet socket may own many lives; browsers stay one-socket-one-life.

The decision loop is specified in [`docs/bot-logic.md`](../docs/bot-logic.md): four states (Graze / Hunt / Flee / Recover), limited vision, per-piece classification. Do not implement the source plan’s nearest-edible seeker; that file explains why. `bots/brain.py` is the pure `decide()`; `bots/simple_bot.py` is plumbing (`BotClient` plus CLI); `bots/fleet.py` is the shared-socket client.

## Run

Start the server, then:

```
python -m bots.simple_bot --url http://127.0.0.1:8000/ws --name bot --count 5
python -m bots.simple_bot --count 17
python -m bots.simple_bot --count 17 --sockets
```

Default is **one shared WebSocket** for the whole table: one `state`/`food` parse, then `decide()` per live id, with `"id"` on `input`/`split`. That is the live lobby (Compose and PR-preview sidecars use it). `--sockets` restores one WebSocket per bot — the original fidelity load test, still useful when you want `sockets=` to match player count.

Names are `bot`, `bot2`, `bot3`… Colors are random per client unless `--color` pins the first (or the only) one. On death that slot waits 3s and `join`s again with the same name and color (siblings keep playing). On disconnect the process reconnects with 0.5s→8s backoff. Ctrl+C closes sockets and exits 0.

Production Compose and PR-preview Fargate start the same client as a sidecar (`--count 17`, shared socket) so those lobbies are populated without running this by hand. Local servers still need the command above.

A second host running this client (`--url http://<game-public-ip>:8000/ws`) is optional later if the sidecar still contends with `process_tick`. It does **not** replace the shared socket: without it the game still `_emit`s the same snapshot once per bot socket.

All bots in the process share one 100×100 graze food index, rebuilt when the food version changes.
