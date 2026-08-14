# Agar.io Clone — Build Plan

## Context
Authoritative-server multiplayer game, agar.io mechanics, running as a POC on an Ubuntu server inside VirtualBox. Server is Python, owns all game state. Clients are a browser (human players, canvas + JS) and Python bot scripts, both talking the same WebSocket/JSON protocol. Persistent open world: players join and leave freely, there is no match start/end. Splitting is in scope for this POC; viruses and mass ejection are not.

## 1. Repo / process layout
- `server/` — Python, single aiohttp app. Serves the static client files and the WebSocket endpoint from the same process and the same port.
- `client/` — `index.html`, `game.js` (canvas render loop, input capture, WebSocket client), `style.css`.
- `bots/` — Python scripts, each one an ordinary WebSocket client driven by a decision loop instead of user input.
- Single port for everything (recommend 8000), so only one firewall rule and one port-forward rule are needed.

## 2. Networking / infra
- An existing forward, external `2222 → VM:22`, handles SSH only. Leave it alone; do not reuse it for game traffic.
- Add a second, independent forward for the game port (8000). Its shape depends on the VM's VirtualBox adapter mode:
  - **Bridged adapter**: VM has its own LAN IP. Router rule: `external:8000 → <VM LAN IP>:8000`.
  - **NAT adapter**: VM is invisible to the router. Requires a VirtualBox-level port-forward rule (`host:8000 → guest:8000`) in the VM's Network settings, plus a router rule `external:8000 → <host LAN IP>:8000`. Mirror whichever shape was used for the existing SSH forward.
- `ufw allow 8000/tcp` on the Ubuntu VM.
- Before debugging a "connection refused" from outside the LAN, check for CGNAT: compare the router's reported WAN IP against what a site like whatismyip.com sees. Mismatch means the ISP has you behind carrier-grade NAT and no port forward will work; use a tunnel (ngrok, Cloudflare Tunnel) instead.
- No TLS for this POC. Plain `ws://` is fine; browsers don't restrict plaintext WebSocket the way they restrict some other APIs over HTTP.

## 3. Server responsibilities
- aiohttp app: static files at `/`, WebSocket upgrade at `/ws`.
- One asyncio task running a fixed-tick game loop at 30Hz.
- Authoritative world state: `players` (id → `{name, pieces: [...], last_input}`), `food` (id → `{x, y}`).
- Per tick, in order: apply each player's latest stored input, run movement, run collisions (player-food, player-player, split-piece eating), decay split velocity, check remerge timers, respawn eaten food, broadcast state to all connected sockets.
- Keep the tick's state-mutation section synchronous (no `await` mid-mutation) so no locks are needed around shared world state.

## 4. Protocol (JSON over WebSocket)

Client → Server:
```json
{"type": "join", "name": "string"}          // once, on connect
{"type": "input", "dx": 0.0, "dy": 0.0}      // throttled to ~20/sec, not on every mousemove
{"type": "split"}                            // on spacebar
```

Server → Client, broadcast every tick:
```json
{
  "type": "state",
  "players": [
    {"id": "...", "name": "...", "pieces": [{"piece_id": "...", "x": 0, "y": 0, "mass": 0}]}
  ],
  "food": [{"id": "...", "x": 0, "y": 0}]
}
```

No area-of-interest culling for this POC — broadcast full world state to every client.

## 5. Game mechanics parameters
- Tick rate: 30Hz.
- Minimum mass to split: 35.
- Split behavior: piece becomes two pieces of half mass; the new piece gets a velocity kick toward the cursor that decays to zero over ~0.5s.
- Max pieces per player: 8.
- Remerge timer: flat 10–15s (not mass-scaled — that's a later refinement).
- Eat rule: piece A eats piece B only if `A.mass > B.mass * 1.25`. A player's own pieces never eat each other, only remerge after the timer clears.

## 6. Client responsibilities
- Canvas + `requestAnimationFrame` render loop, decoupled from the server's tick rate.
- Camera centered on the player's piece centroid; zoom scales out as total mass grows.
- Interpolate between the last two received state snapshots based on elapsed time since the last one arrived. This is required, not optional — without it, 30Hz server updates rendered at 60fps+ will visibly stutter.
- Mouse position converted to `dx/dy` relative to player center, sent as throttled input.
- Spacebar sends `split`.

## 7. Bots
- Ordinary WebSocket clients using the same join/input/split protocol as a human.
- Decision loop: from the last received state, move toward the nearest edible entity (food or a smaller player); flee if a larger player is closer than the nearest edible target.
- No pathfinding required for this POC.

## 8. Build phases
Each phase must work before starting the next.

1. Core simulation, no networking: hardcoded fake players, run the tick loop, print state to console. Verify movement/collision/split logic in isolation.
2. Add the aiohttp WebSocket server. Verify the protocol round-trips using a bare Python script client that only prints received state — no rendering yet.
3. Build the canvas client. Confirm one browser tab can move and eat food against a localhost server.
4. Open two browser tabs. Confirm players can see and eat each other.
5. Add splitting. Verify against the mass-ratio and remerge rules in section 5.
6. Add bots.
7. Move to the VM: adapter config, ufw, router port forward for the game port (independent of the existing SSH forward). Test from outside the LAN.

## 9. Explicitly deferred — do not build unless asked
- UDP / WebRTC DataChannel transport.
- Area-of-interest broadcast culling.
- DDNS / stable URL.
- TLS / `wss://`.
- Viruses, mass ejection, mass-scaled remerge timers.
- Protobuf serialization in place of the JSON payloads. WebSocket already carries binary frames, so this is a drop-in swap at the serialization layer only, no change to transport or message semantics. Defer until message shapes are stable and protocol iteration has slowed, since binary schemas add recompile friction during active field changes and remove the ability to read wire messages in the browser's network tab while debugging.
