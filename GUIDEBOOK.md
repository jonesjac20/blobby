# Blobby — Build Guidebook

Living checklist for building `blobby`, an agar.io-style multiplayer POC. Follows the phases in [`../../Downloads (E)/agario_build_plan.md`](../../Downloads%20(E)/agario_build_plan.md).

**How to use this doc.** Work top-to-bottom. Each phase must work before starting the next (section 8 of the source plan). Check items off as you go — the boxes are `- [ ]` and become `- [x]`. Every item has an ownership tag so you know at a glance whether the agent can do it from chat, whether it needs your hands, or whether it's a hand-off.

## Legend

- **[Agent]** — the agent can do this end-to-end from chat (write code, edit configs, run tests locally in the workspace).
- **[Human]** — requires your hands: VirtualBox GUI, router admin page, physically checking a browser tab, external-network testing, judgment calls on feel/aesthetics.
- **[Both]** — the agent writes it, you run it locally / verify it looks right / paste back any errors.

---

## Setup (once, before Phase 1)

- [ X ] **[Human]** Confirm Python 3.13 is on PATH (`python --version`).
- [ X ] **[Human]** Confirm git is installed and configured with your name/email.
- [ X ] **[Agent]** Repo scaffold (this doc, `server/`, `client/`, `bots/`, `tests/`, `.gitignore`, `README.md`, empty `requirements.txt`). *— Done as part of the scaffold pass.*
- [ X ] **[Human]** VirtualBox + Ubuntu VM already exist and boot. (Only needed for Phase 7.)
- [ X ] **[Human]** You can SSH into the VM through the existing external `2222 → VM:22` forward. (Only needed for Phase 7.)

---

## Phase 1 — Core simulation, no networking

Goal: run the tick loop in isolation and confirm movement, eating, and splitting behave per section 5 of the source plan.

### Fill in each server file

- [ X ] **[Agent]** `server/config.py` — constants from source plan section 5: `TICK_RATE = 30`, `MIN_SPLIT_MASS = 35`, `MAX_PIECES = 8`, `EAT_RATIO = 1.25`, `REMERGE_SECONDS = 12`, `SPLIT_KICK_DECAY_SECONDS = 0.5`. Also `WORLD_WIDTH`, `WORLD_HEIGHT`, `FOOD_COUNT`, `FOOD_MASS`, and a `speed_for_mass(mass)` function (agar.io style: speed decreases as mass grows).
- [ X ] **[Agent]** `server/models.py` — dataclasses only, no behavior: `Piece(piece_id, x, y, mass, vx, vy, split_time)` where `vx/vy` is the decaying split kick and `split_time` gates remerge; `Player(id, name, pieces, last_input)`; `Food(id, x, y)`.
- [ X ] **[Agent]** `server/world.py` — `World` holds `players` and `food` dicts; methods `spawn_player`, `spawn_food_to_target_count`, `remove_player`. IDs via `uuid.uuid4().hex[:8]`.
- [ X ] **[Agent]** `server/simulation.py` — pure `step(world, dt)` in this order:
  1. Apply each player's `last_input` as a normalized velocity scaled by `speed_for_mass(piece.mass)`, added to the decaying split kick.
  2. Integrate position.
  3. Cluster forces: draw a player's own pieces toward each other at `COHESION_SPEED`, or at `MERGE_PULL_SPEED` once both of a pair's remerge timers have cleared. Skipped for any pair whose split kick is still active, so cohesion never fights the kick.
  4. Resolve collisions by mass-weighted position projection, then clamp to world bounds. Own pieces settle at `OWN_PIECE_OVERLAP` depth; different players' pieces are solid, *unless* one can eat the other, in which case they are left free to interpenetrate.
  5. Player-food collision by distance ≤ piece radius (radius = `sqrt(mass / π)`).
  6. Player-player + own-piece checks using the `A.mass > B.mass * 1.25` rule from source plan section 5, and additionally requiring `EAT_OVERLAP` engulfment depth so a graze is a collision rather than a kill; own pieces never eat each other.
  7. Decay `vx/vy` toward zero over `SPLIT_KICK_DECAY_SECONDS`. Nothing but the split kick ever writes these, so they stay meaningful on the wire.
  8. Remerge same-player pieces whose `split_time` is older than `REMERGE_SECONDS` and whose bodies have sunk to `MERGE_OVERLAP` — deeper than pieces rest at, so the merge pull has to drag them the last of the way.
  9. Respawn food up to `FOOD_COUNT`.
  Also expose `try_split(world, player)` that aims the kick along `player.last_input` (the wire message carries no direction) and enforces `MIN_SPLIT_MASS` and `MAX_PIECES`.
- [ X ] **[Agent]** `server/main.py` — `asyncio` loop sleeping `1/TICK_RATE` per tick, calling `simulation.step`. Builds a `World` with 2 hardcoded players: **A** whose `last_input` is recomputed each tick to point at the nearest food, **B** moving in a slow circle. Every ~30 ticks (~1s), prints one summary line: `tick N | A pieces=[m1,m2] pos=(x,y) | B pieces=[m3] pos=(x,y) | food=K`. After ~3s, call `try_split` on A once so splitting → decay → remerge is visible. Ctrl+C exits cleanly.

### Verify each behavior

- [ ] **[Both]** Piece moves in the direction of its player's `last_input`.
- [ ] **[Both]** Speed decreases as mass grows (bigger blob is slower).
- [ ] **[Both]** Piece stays inside world bounds — no negative or off-world coordinates.
- [ ] **[Both]** Food gets eaten when a piece's circle covers the food's center; piece mass increases; food is removed and eventually respawns.
- [ ] **[Both]** Player-vs-player eat rule: `A.mass > B.mass * 1.25` is required. Equal or near-equal blobs don't eat each other.
- [ ] **[Both]** Player's own pieces never eat each other — they can only remerge.
- [ ] **[Both]** Different players' pieces collide solidly when neither can eat the other; a predator is not blocked by its prey.
- [ ] **[Both]** `try_split` refuses to split a piece under `MIN_SPLIT_MASS`.
- [ ] **[Both]** `try_split` refuses to split when the player already has `MAX_PIECES`.
- [ ] **[Both]** `try_split` is exponential in growth, halving in mass (i.e., it should split all possible pieces that have been split previously)
- [ ] **[Both]** A successful split produces two pieces of half mass, and the new one has a velocity kick toward the cursor direction.
- [ ] **[Both]** Split kick decays to zero over ~`SPLIT_KICK_DECAY_SECONDS`.
- [ ] **[Both]** Split halves pop apart on the kick, then drift back into contact on their own.
- [ ] **[Both]** Two same-player pieces remerge after `REMERGE_SECONDS` when their circles overlap.
- [ ] **[Both]** Once the remerge timer clears, the pair visibly sinks into each other before merging.
- [ ] **[Both]** `food` dict length stays at `FOOD_COUNT` over time.

### How to verify

From the project root:

```
python -m server.main
```

Expected console output over ~15 seconds:

- Player A's total mass steadily climbs as it eats food.
- Around t = 3s, one summary line shows A now has 2 pieces instead of 1.
- Over the next ~0.5s the two A coordinates diverge by ~30 units (that's the kick decaying), then close back up over the second or so after that (that's cohesion).
- Shortly after t = 15s, the two A pieces sink together and collapse back into one.
- The `food=K` count hovers near `FOOD_COUNT`.

Ctrl+C to stop.

### Phase 1 exit criteria

- [ ] **[Human]** All the verify boxes above are checked and nothing feels wrong. Only then move on to Phase 2.

---

## Phase 2 — WebSocket server (no browser client yet)

Goal: put the tick loop behind an aiohttp WebSocket endpoint and confirm the protocol round-trips using a bare Python client that just prints received state.

- [ ] **[Agent]** Add `aiohttp` to `requirements.txt`.
- [ ] **[Human]** `pip install -r requirements.txt` (or into a venv).
- [ ] **[Agent]** Rewrite `server/main.py` as an aiohttp app: static file serving mounted at `/`, WebSocket upgrade at `/ws`, tick loop as an asyncio task alongside the HTTP server. Single port (8000).
- [ ] **[Agent]** Server → client broadcast every tick, using the exact shape from source plan section 4.
- [ ] **[Agent]** Client → server messages: `join`, `input`, `split`. Store `last_input` per player and consume it on the next tick.
- [ ] **[Agent]** Keep the tick's state-mutation section synchronous — no `await` mid-mutation (source plan section 3).
- [ ] **[Agent]** `tools/probe_client.py` — bare Python WebSocket client: connects, sends `join`, prints one line per received `state` message, sends fake `input` occasionally.
- [ ] **[Both]** Run server + probe client in separate terminals against `localhost:8000`. Confirm probe sees state broadcasts and its inputs are reflected in the state on the next tick.

### Phase 2 exit criteria

- [ ] **[Human]** Protocol round-trips cleanly with two probe clients connected at once. No log spam, no dropped connections on idle.

---

## Phase 3 — Canvas client (one browser tab)

Goal: one browser tab can move and eat food against a localhost server.

- [ ] **[Agent]** `client/index.html` — canvas element, minimal chrome, name-entry field.
- [ ] **[Agent]** `client/style.css` — full-window canvas, no scrollbars.
- [ ] **[Agent]** `client/game.js`:
  - `requestAnimationFrame` render loop, decoupled from server tick rate.
  - WebSocket connection to `/ws`; send `join` on open.
  - Mouse position → normalized `dx/dy` relative to player center; send throttled to ~20/sec.
  - Camera centered on the player's piece centroid.
  - Zoom scales out as total mass grows.
  - **Interpolation between the last two received state snapshots** (source plan section 6 flags this as non-optional).
- [ ] **[Human]** Start server, open `http://localhost:8000`, confirm you can move around and eat food. Mass shown numerically somewhere for sanity.

### Phase 3 exit criteria

- [ ] **[Human]** Movement feels smooth (no visible 30Hz stutter — that means interpolation is working). Food gets eaten reliably.

---

## Phase 4 — Two browser tabs

Goal: two players can see and eat each other.

- [ ] **[Human]** Open two browser tabs at `http://localhost:8000`, use different names.
- [ ] **[Human]** Confirm each tab renders the other player.
- [ ] **[Human]** Grow one blob well past the other and confirm it can eat the smaller one (subject to the 1.25 mass ratio).
- [ ] **[Both]** Fix any bugs surfaced (state broadcast omissions, race conditions, wrong ownership checks, etc.).

### Phase 4 exit criteria

- [ ] **[Human]** Two-player eating works both directions when the mass ratio is met. Neither tab desyncs after a few minutes of play.

---

## Phase 5 — Splitting on the client

Goal: spacebar splits, following the section 5 rules verified in Phase 1.

- [ ] **[Agent]** `game.js`: spacebar sends `{"type": "split"}`.
- [ ] **[Human]** Grow a blob above `MIN_SPLIT_MASS` (35), press space, confirm you see two pieces flying apart.
- [ ] **[Human]** Confirm split is refused (nothing happens) when under 35 mass.
- [ ] **[Human]** Confirm split is refused when already at 8 pieces.
- [ ] **[Human]** Confirm the split kick visually decays over ~0.5s.
- [ ] **[Human]** Wait ~12s and confirm pieces remerge.
- [ ] **[Human]** With two tabs open: split, then eat a smaller player's piece with the split fragment. Confirm ratio rule still applies.

### Phase 5 exit criteria

- [ ] **[Human]** Splitting feels responsive and matches the rules from source plan section 5.

---

## Phase 6 — Bots

Goal: N Python bot clients playing autonomously.

- [ ] **[Agent]** `bots/simple_bot.py` — WebSocket client using the same `join`/`input`/`split` protocol. CLI args for name, server URL, count.
- [ ] **[Agent]** Decision loop: from the most recent received state, move toward the nearest edible entity (food or a smaller player); flee if a larger player is closer than the nearest edible target. No pathfinding.
- [ ] **[Both]** Run 3–5 bots against a local server, then open a browser tab as a human and play against them.
- [ ] **[Human]** Confirm bots don't deadlock, don't spin in place, and don't crash on player disconnect.

### Phase 6 exit criteria

- [ ] **[Human]** World feels alive with bots present. Bots survive server restarts (the client reconnects, or its process exits cleanly).

---

## Phase 7 — Move to the VM, expose externally

Goal: reachable from outside the LAN, independent of the existing SSH forward.

### On the VM

- [ ] **[Human]** Check the VM's VirtualBox adapter mode (Settings → Network). Note whether it's **Bridged** or **NAT** — routing setup below depends on this.
- [ ] **[Both]** Copy the repo to the VM (`git clone` inside the VM is easiest once the VM has internet).
- [ ] **[Agent]** Write a short `scripts/vm_bootstrap.sh` — installs Python 3.13 (or 3.12), `pip`, sets up a venv, `pip install -r requirements.txt`, ufw rule.
- [ ] **[Human]** Run `scripts/vm_bootstrap.sh` on the VM.
- [ ] **[Human]** `sudo ufw allow 8000/tcp` on the VM (also done by the bootstrap script; verify with `sudo ufw status`).
- [ ] **[Human]** Start the server on the VM. Ideally under a systemd unit or `tmux`/`screen` so it survives your SSH session.

### On VirtualBox (only if adapter is NAT)

- [ ] **[Human]** VM Settings → Network → Advanced → Port Forwarding: add `host:8000 → guest:8000`.

### On the router

- [ ] **[Human]** Add a **new** port forward, independent of the existing `2222 → VM:22` SSH rule. Do not modify the SSH rule.
  - Bridged adapter: `external:8000 → <VM LAN IP>:8000`.
  - NAT adapter: `external:8000 → <host LAN IP>:8000`.

### External test

- [ ] **[Human]** From a machine on the LAN, hit `http://<VM LAN IP>:8000` (bridged) or `http://<host LAN IP>:8000` (NAT). Confirm the client loads and connects.
- [ ] **[Human]** Compare the router's reported WAN IP to what a site like whatismyip.com sees. Mismatch = your ISP has you behind carrier-grade NAT and no port forward will work.
- [ ] **[Human]** If not behind CGNAT: from a device on cellular (off Wi-Fi), hit `http://<WAN IP>:8000`. Confirm it loads.
- [ ] **[Both]** If behind CGNAT or the router refuses: fall back to ngrok or a Cloudflare Tunnel and re-test. This is `[Both]` because the agent can write the tunnel setup script but you have to sign in.

### Phase 7 exit criteria

- [ ] **[Human]** Someone off your LAN loads the game URL and plays a round. That's the POC done.

---

## Deferred — do not build unless explicitly asked

Copied verbatim from source plan section 9 so nothing sneaks in early:

- UDP / WebRTC DataChannel transport.
- Area-of-interest broadcast culling.
- DDNS / stable URL.
- TLS / `wss://`.
- Viruses, mass ejection, mass-scaled remerge timers.
- Protobuf serialization in place of the JSON payloads. WebSocket already carries binary frames, so this is a drop-in swap at the serialization layer only, no change to transport or message semantics. Defer until message shapes are stable and protocol iteration has slowed, since binary schemas add recompile friction during active field changes and remove the ability to read wire messages in the browser's network tab while debugging.
