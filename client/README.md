# client

Browser code. Split deliberately into a reusable core, the live game client, and Phase 1 scaffolding.

## Reusable

- `render.js` — camera, snapshot interpolation and canvas drawing. Operates purely on the server → client `state` message from section 4 of the build plan, so it does not care whether that state arrived over a WebSocket or came out of a recording. `game.js` imports it as-is.
- `style.css` — design tokens, viewer chrome, and the Phase 3 full-window overlay / HUD rules under `body.game`.

`render.js` covers `followCamera` (centred on the player's piece centroid, zooming out as total mass grows) and the blend half of the interpolation section 6 of the build plan calls for. `interpolateStates` takes the blend factor as an argument; `game.js` buffers the last two live snapshots and derives that factor from elapsed time.

`interpolateStates` returns `next`'s set of pieces with blended positions, which is right for a live client — you always want the newest authoritative piece list — but means a caller wanting one snapshot exactly as recorded should render it directly rather than asking for a blend factor of zero. The viewer does that whenever it is paused. Player fields other than `pieces` (including `color`) are passed through; `drawPieces` prefers `player.color` over `colorForId`.

The deliberate exceptions are `drawVelocityArrows`, `drawInputRays` and `drawMergeReady`. Split-kick velocity, `last_input` and the remerge timer are not in the wire format, so those take their data as a separate argument and the real client simply never calls them.

## Live client

- `index.html` + `game.js` — greeting menu, follow-cam, interpolation, mouse aim, Game Over. Served by `python -m server.main` at `http://localhost:8000`. Connecting does not spawn; Play sends `join`, Spectate never does.

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
