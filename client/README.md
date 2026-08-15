# client

Browser code. Split deliberately into a reusable core, the live game client, and Phase 1 scaffolding.

## Reusable

- `render.js` — camera, snapshot interpolation and canvas drawing. Operates purely on the server → client `state` message from section 4 of the build plan, so it does not care whether that state arrived over a WebSocket or came out of a recording. `game.js` imports it as-is.
- `style.css` — design tokens, viewer chrome, and the Phase 3 full-window overlay / HUD rules under `body.game`.

`render.js` covers `followCamera` (centred on the player's piece centroid, zooming out as total mass grows) and the blend half of the interpolation section 6 of the build plan calls for. `interpolateStates` takes the blend factor as an argument; `game.js` buffers the last two live snapshots and derives that factor from elapsed time.

`drawPieces` paints in two passes: every disc largest-first so a big blob cannot hide a small one, then labels. Names sit *above the cluster* — one label per player, at the centroid's x and just above the highest disc — at a size floored at 11px from total mass, outlined for contrast. A one-piece player looks the same as before; a split player is not labelled eight times. Mass still lives inside each body and still disappears on a disc too small to hold it. A player with `protected` true (spawn invulnerability) gets a dashed gold ring after the discs, so a shoved predator reads as blocked rather than broken.

`interpolateStates` returns `next`'s set of pieces with blended positions, which is right for a live client — you always want the newest authoritative piece list — but means a caller wanting one snapshot exactly as recorded should render it directly rather than asking for a blend factor of zero. The viewer does that whenever it is paused. Player fields other than `pieces` (including `color` and `protected`) are passed through; `drawPieces` prefers `player.color` over `colorForId`. Recordings have no `protected`, which is falsy, so the viewer is unchanged.

The deliberate exceptions are `drawVelocityArrows`, `drawInputRays` and `drawMergeReady`. Split-kick velocity, `last_input` and the remerge timer are not in the wire format, so those take their data as a separate argument and the real client simply never calls them.

## Live client

- `index.html` + `game.js` — greeting menu, follow-cam, interpolation, mouse aim, spacebar split, Game Over. Served by `python -m server.main` at `http://localhost:8000`. Connecting does not spawn; Play sends `join`, Spectate never does. Spacebar sends `split` while playing; held-key auto-repeat is ignored so one press is one split. Spectate focuses the blob under a click, cycles on a click into empty world, and Escape returns to the menu. A spawn-protected life shows a "protected" chip next to mass for as long as `state` says so.

Two things to know before editing it. The arena size arrives on `welcome` and `state` as `{width, height}`, along with `tickRate` and `initialPlayerMass` — none of these are client constants, so changing `WORLD_WIDTH` / `WORLD_HEIGHT` / `TICK_RATE` / `INITIAL_PLAYER_MASS` in `server/config.py` is enough. The greeting form's `maxlength`, default color and placeholder still mirror `NAME_MAX_LEN`, `DEFAULT_COLOR` and `DEFAULT_NAME` — the menu paints before any message arrives. And a dropped socket reconnects with a doubling backoff behind the `#offline` overlay, then drops to the greeting menu: the server deletes a socket's player when it closes, so the life is gone and rejoining silently would read as a teleport back to spawn mass. Spectators carry on instead, having lost nothing. Both are recorded under Divergence in `docs/GUIDEBOOK.md`.

No part of this file is under test. See `docs/feel-pass.md` section D.

## Phase 1 only

- `viewer.html`, `viewer.js` — the verification viewer. One scripted scenario per verify box in `docs/GUIDEBOOK.md`, with a timeline you can scrub, frame-by-frame stepping, an event log and a debug HUD.
- `recording.js` — expands the recorder's delta-encoded food back into full `state` messages. No DOM access, deliberately.
- `recordings/` — generated, gitignored.

## Running it

Game client, from the project root:

```
python -m server.main
```

Then open `http://localhost:8000`.

Verification viewer:

```
python -m tools.record --serve
```

That regenerates the recordings and opens the viewer on port 8080. Without `--serve` it only writes the files. A static server is needed because `render.js` is an ES module, which browsers refuse to load over `file://`.

Space plays and pauses in the viewer, arrow keys step one frame (hold shift for ten).
