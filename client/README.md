# client

Browser code. Split deliberately into a reusable core and Phase 1 scaffolding.

## Reusable (Phase 3 will build on these)

- `render.js` — camera, snapshot interpolation and canvas drawing. Operates purely on the server → client `state` message from section 4 of the build plan, so it does not care whether that state arrived over a WebSocket or came out of a recording. Phase 3's `game.js` should import this unchanged.
- `style.css` — design tokens and the full-bleed canvas rules.

`render.js` also already covers two things Phase 3 needs and section 6 of the build plan calls for: `interpolateStates` (required, not optional — 30Hz state rendered at 60fps stutters without it) and `followCamera` (centred on the player's piece centroid, zooming out as total mass grows).

The deliberate exceptions are `drawVelocityArrows`, `drawInputRays` and `drawMergeReady`. Split-kick velocity, `last_input` and the remerge timer are not in the wire format, so those take their data as a separate argument and the real client simply never calls them.

## Phase 1 only

- `viewer.html`, `viewer.js` — the verification viewer. One scripted scenario per `[Both]` checklist item in `GUIDEBOOK.md`, with a timeline you can scrub, frame-by-frame stepping, an event log and a debug HUD.
- `recordings/` — generated, gitignored.

## Running it

From the project root:

```
python -m tools.record --serve
```

That regenerates the recordings and opens the viewer. Without `--serve` it only writes the files. A static server is needed because `render.js` is an ES module, which browsers refuse to load over `file://`; in Phase 2 aiohttp takes over the serving.

Space plays and pauses, arrow keys step one frame (hold shift for ten).

## Phase 3

`index.html` + `game.js` land here, importing `render.js` and driving it from a `/ws` connection instead of recorded frames. At that point `viewer.*` and `recordings/` can either stay as a regression tool or be deleted.
