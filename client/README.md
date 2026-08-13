# client

Browser code. The reusable rendering core lives here, ahead of the Phase 3 client that will drive it.

## Reusable (Phase 3 will build on these)

- `render.js` — camera, snapshot interpolation and canvas drawing. Operates purely on the server → client `state` message from section 4 of the build plan, so it does not care whether that state arrived over a WebSocket or came out of a recording. Phase 3's `game.js` should import this unchanged.
- `style.css` — design tokens and the full-bleed canvas rules.

`render.js` also already covers two things Phase 3 needs and section 6 of the build plan calls for: `interpolateStates` (required, not optional — 30Hz state rendered at 60fps stutters without it) and `followCamera` (centred on the player's piece centroid, zooming out as total mass grows).

The deliberate exceptions are `drawVelocityArrows`, `drawInputRays` and `drawMergeReady`. Split-kick velocity, `last_input` and the remerge timer are not in the wire format, so those take their data as a separate argument and the real client simply never calls them.
