# bots

Bot clients added in phase 6. Each bot is an ordinary WebSocket client speaking the same `join` / `input` / `split` protocol as a human.

The decision loop is specified in [`docs/bot-logic.md`](../docs/bot-logic.md): four states (Graze / Hunt / Flee / Recover), limited vision, per-piece classification. Do not implement the source plan’s nearest-edible seeker; that file explains why. `bots/brain.py` is the pure `decide()`; `bots/simple_bot.py` is plumbing.

## Run

Start the server, then:

```
python -m bots.simple_bot --url http://127.0.0.1:8000/ws --name bot --count 5
python -m bots.simple_bot --count 30
```

One process, N sockets. Names are `bot`, `bot2`, `bot3`… Colors are random per client unless `--color` pins the first (or the only) one. On death the bot waits 3s and `join`s again with the same name and color. On disconnect it reconnects with 0.5s→8s backoff. Ctrl+C closes sockets and exits 0.

All bots in the process share one 100×100 graze food index, rebuilt when the food version changes.
