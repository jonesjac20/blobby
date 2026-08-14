# client

Browser code. Split deliberately into a reusable core and Phase 1 scaffolding.

## Reusable (Phase 3 will build on these)

- `render.js` — camera, snapshot interpolation and canvas drawing. Operates purely on the server → client `state` message from section 4 of the build plan, so it does not care whether that state arrived over a WebSocket or came out of a recording. Phase 3's `game.js` should import this unchanged.
- `style.css` — design tokens and the full-bleed canvas rules.

`render.js` also already covers `followCamera` (centred on the player's piece centroid, zooming out as total mass grows) and the blend half of the interpolation section 6 of the build plan calls for. Note what `interpolateStates` is *not*: it takes the blend factor as an argument, so the hard part of live interpolation — buffering the last two snapshots and deriving that factor from elapsed time, while absorbing a late or dropped tick — is Phase 3 work that has no counterpart here. A recording hands the viewer a complete, evenly spaced, seekable frame array, which is the one input a live client never gets.

`interpolateStates` returns `next`'s set of pieces with blended positions, which is right for a live client — you always want the newest authoritative piece list — but means a caller wanting one snapshot exactly as recorded should render it directly rather than asking for a blend factor of zero. The viewer does that whenever it is paused.

The deliberate exceptions are `drawVelocityArrows`, `drawInputRays` and `drawMergeReady`. Split-kick velocity, `last_input` and the remerge timer are not in the wire format, so those take their data as a separate argument and the real client simply never calls them.

## Phase 1 only

- `viewer.html`, `viewer.js` — the verification viewer. One scripted scenario per verify box in `docs/GUIDEBOOK.md`, with a timeline you can scrub, frame-by-frame stepping, an event log and a debug HUD.
- `recording.js` — expands the recorder's delta-encoded food back into full `state` messages. No DOM access, deliberately.
- `recordings/` — generated, gitignored.

## Running it

From the project root:

```
python -m tools.record --serve
```

That regenerates the recordings and opens the viewer. Without `--serve` it only writes the files. A static server is needed because `render.js` is an ES module, which browsers refuse to load over `file://`; in Phase 2 aiohttp takes over the serving.

Space plays and pauses, arrow keys step one frame (hold shift for ten).

## Phase 3

`index.html` + `game.js` land here, importing `render.js` and driving it from a `/ws` connection instead of recorded frames. `style.css` gets extended rather than replaced, since both pages share it. Deciding whether `viewer.*` and `recordings/` stay as a regression tool or get deleted is an explicit Phase 3 checklist item — see `docs/GUIDEBOOK.md`.
