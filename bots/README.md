# bots

Bot clients added in phase 6. Each bot is an ordinary WebSocket client speaking the same `join` / `input` / `split` protocol as a human.

The decision loop is specified in [`docs/bot-logic.md`](../docs/bot-logic.md): four states (Graze / Hunt / Flee / Recover), limited vision, per-piece classification. Do not implement the source plan’s nearest-edible seeker; that file explains why.
