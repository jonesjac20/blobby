# Blobby — Build Guidebook

Living checklist for building `blobby`, an agar.io-style multiplayer POC. Follows the phases in [`agario_build_plan.md`](agario_build_plan.md), vendored into this folder so the repo carries its own source of truth onto the VM in Phase 7.

**How to use this doc.** Work top-to-bottom. Each phase must work before starting the next (section 8 of the source plan). Check items off as you go — the boxes are `- [ ]` and become `- [x]`. Every item has an ownership tag so you know at a glance whether the agent can do it from chat, whether it needs your hands, or whether it's a hand-off.

**This doc describes the repo, not just the intent.** Where the code has outgrown the source plan, that is recorded under [Divergence](#divergence-from-the-source-plan) rather than quietly folded in. If you find the code and this doc disagreeing, the doc is the bug — fix it here.

## Legend

- **[Agent]** — the agent can do this end-to-end from chat: write code, edit configs, run the test suite and the harness locally in the workspace.
- **[Human]** — requires your hands: VirtualBox GUI, router admin page, physically watching a browser tab, external-network testing, judgment calls on feel and aesthetics.
- **[Both]** — the agent stages it, you look at it and say whether it's right. Used only where the answer is a matter of judgment rather than a number.

Verification boxes are tagged the same way. An `[Agent]` verify box means there is a named automated test that proves it, and the agent can show you it passing. A `[Both]` verify box means the mechanism is tested but the *legibility* is not — someone has to watch it.

---

## Divergence from the source plan

The source plan is the authority on scope. Where this build adds to it, it is recorded here instead of being blended in silently.

- **Phase 1 soft-body cluster physics.** Source section 5 specifies the eat ratio and the split/remerge rules and nothing else; section 3 asks only for "run collisions". Cohesion, the merge pull, mass-weighted position projection and the three engulfment thresholds are all additions. They exist so a multi-piece player reads as one body rather than a pile of circles. Their values are *provisional*: every one is a feel parameter, and feel cannot be judged until Phase 3 puts it on a screen.
- **Split kick scales with parent radius.** Source section 5 says only that the new piece gets a velocity kick that decays over ~0.5s. A flat speed made large splits a twitch — resting distance grows as `sqrt(mass)` while travel stayed fixed (feel-pass A2). Displacement is now `SPLIT_KICK_RADII` times the pre-split parent piece's radius, capped at `SPLIT_KICK_MAX_ARENA_FRACTION` of the shorter arena axis so a giant cannot lunge across the map. Only the new piece is kicked; the parent stays at half mass with its kick cleared.
- **Bot brain exceeds source plan §7.** Section 7 is “nearest edible, flee if a larger player is closer.” That loop deadlocks, never catches a competent player, and suicide-splits. Phase 6 follows [`bot-logic.md`](bot-logic.md): four states (Graze / Hunt / Flee / Recover), limited vision, per-piece classification. The file is the spec; `bots/simple_bot.py` is still the Phase 6 implementation.
- **`REMERGE_SECONDS = 12`** is a pick from the source plan's "flat 10–15s" range, not a value the plan states.
- **A simulation clock.** The source plan never says where "now" comes from. See [Simulation clock](#simulation-clock).
- **Verification tooling.** `tests/`, `tools/` and the browser viewer are not in the source plan. See [Verification tooling](#verification-tooling-phase-1).
- **Phase 3 render core built early.** `client/render.js` and `client/style.css` were written during Phase 1 so the viewer had something to draw with. That breaks the "each phase before the next" rule; it is recorded here so the rule's failure is visible rather than assumed.
- **Greeting menu, color, and death.** Source section 4 has `join` / `input` / `split` and a `state` broadcast of `{id, name, pieces}`. Connecting here does not spawn. `join` carries a player-chosen `color`; the server replies with `welcome` `{id}` so a client can follow-cam without matching on name. A last-piece eat removes the player from the world and sends `game_over` `{peak_mass, survival_seconds}` on that socket only. The next `join` on the same socket is a respawn. Spectating is connecting and never sending `join`. Phase 3 owns the menu and Game Over UI; Phase 2 owns the protocol so that UI can exist. `state` players also carry `color`.
- **Phase 1 console harness lives in `server/demo.py`.** Phase 2 rewrote `server/main.py` as the aiohttp entrypoint. `python -m server.demo` is the old two-player printout.
- **Runtime vs dev requirements.** `requirements.txt` is pinned `aiohttp` only, so Phase 7's VM bootstrap does not install pytest. Tests install from `requirements-dev.txt`.
- **Spawn invulnerability.** `SPAWN_INVULN_SECONDS = 5` — not in the source plan, and a consequence of `join` picking a spawn point from the RNG. That point is clamped into the rectangle, never away from other bodies, so a join can land inside a blob heavy enough to eat it on the next tick and a player would be dead before the first frame renders. For that window the player cannot be eaten but eats normally, and a predator on top of it is shoved rather than left to interpenetrate. **Granted by a live `join` only** — `world.spawn_player` leaves `Player.spawn_time` already expired, so every Phase 1 test, scenario and the demo harness stage edible players exactly as before. Another feel parameter: 5s is a pick from the 2–5s range, to be judged on a screen in Phase 4. `state` players carry `protected` so both tabs can see the window — a dashed gold ring around the body and a HUD chip next to mass — because without that tell a shoved predator reads as broken collision. The flag is derived from `World.now` on the server; it is not a timestamp the client can influence. Random spawn makes the overlap case rare, so `BLOBBY_DEBUG_SPAWN=x,y` pins `handle_join` to a point for local feel-testing. `BLOBBY_DEBUG_MASS=280` likewise pins spawn mass so Phase 5 can exercise exponential splitting without eating first. Neither is a `join` field, which would be a cheat once this process faces the internet.
- **The broadcast is guarded against a join landing mid-tick.** `_emit` awaits each socket in turn, so a `join` handled during that loop splits the broadcast into sockets served before it and sockets served after. Three messages go wrong in that window, and each is dropped rather than sent: a `game_over` from the previous life, which would arrive after the respawn's `welcome` and end a game that just started; a `state` naming the new player *before* its `welcome`, which hands the client an id it does not yet know is its own; and the pre-join snapshot *after* `welcome`, which omits the player entirely so Phase 3's follow-cam finds nothing to follow. A playing socket simply misses that one frame — the next tick's snapshot is correct for it. Spectators are never withheld anything.
- **`peak_mass` is observed mid-tick.** `update_and_eliminate` runs after `simulation.step`, by which point a player killed this tick has no pieces left to measure. `simulation` records `Player.last_total_mass` just before eaten pieces are culled, so mass picked up on the tick that killed you — a pellet, or a fragment of the blob that then ate you — still reaches Game Over.
- **A failed tick is logged, not fatal.** The tick loop catches exceptions from `process_tick` and from the broadcast and continues. Without it, one throw cancels the task and leaves the HTTP and WebSocket endpoints answering normally over a world that has silently stopped, which reads as a network fault. A throw partway through `step` can leave the world half-mutated; for a POC a visible traceback and a resumed loop beats a frozen one.
- **`/healthz`.** Phase 10 of the deployment annex. 200 if the last successful tick is within `HEALTHZ_STALE_AFTER_SECONDS`, 503 before any tick or when the stamp is aged out. Registered before `/{name}` so the client whitelist cannot 404 it. A failed tick does not refresh the stamp. JSON logs and `/metrics` stay Phase 13.
- **An explicit `/` route, and a file whitelist.** `add_static("/", CLIENT_DIR)` maps paths to files and refuses a directory-root request, so `/` returned 403, `/index.html` 404 (no such file yet), and `/viewer.html` plus `/recordings/index.json` 200. Phase 3 serves `index.html` at `/` and only `index.html`, `game.js`, `render.js` and `style.css` under `/{name}`. The Phase 1 viewer stays a regression harness, reachable via `python -m tools.record --serve` on 8080, and is not on the game port — the same decision Phase 7 requires once this process faces the internet.
- **Food leaves the `state` broadcast.** Source plan section 4 puts a full food list in every `state`. Measured on a six-player world that was 55,503 of 57,126 bytes (97%), resent ~90% of ticks with a byte-identical 600-element array (median pellet-set churn was 0). A `food` message of `[[x, y], ...]` integer pairs is sent only when the id set changes, or to a socket whose `food_version` is behind. The payload is dumped once (`FoodStream.encoded`) and `_emit` sends that string to every behind socket; the version is recorded only after a successful send, so a raise that `_emit` swallows retries next tick, and because food is no longer in `state`, withholding a join-window frame cannot desync a client's pellets. Area-of-interest culling and protobuf stay deferred; a true per-pellet delta is a drop-in on the same message type.
- **Feel-pass A6 eat grid.** `_eat_food` buckets pellets by `FOOD_GRID_CELL` and skips hypot tests outside each piece's padded sweep AABB. Iteration stays `world.food` dict order so eats remain seed-identical. One World scan, not a per-socket index. Needed so a ≥30-bot lobby can hold 30Hz at `FOOD_COUNT = 1800`.
- **Food that spawns on a blob is eaten the same tick.** Spawn runs after the swept eat, so a pellet can appear inside a disc that did not move onto it. Left for the next tick it would render inside a body for a frame. `_refill_food` eats those at rest and respawns until the surviving pellets are uncovered.
- **The arena rectangle, tick rate, spawn mass and speed knobs ride on `welcome` and every `state`.** Nothing used to carry the rectangle, so `WORLD` in `game.js` duplicated `WORLD_WIDTH` / `WORLD_HEIGHT` from `server/config.py`. Changing the config clamped the simulation to the new bounds while the client kept drawing the old border and grid — bodies sat outside the rectangle they were supposedly inside, which reads as a physics bug rather than a constant. The size is now `{width, height}` on `welcome` (so a join can fit-cam the real arena before its first snapshot) and on `state` (so a spectator, who never sends `join`, learns it too), along with `tickRate` (interpolation interval), `initialPlayerMass` (follow-cam zoom baseline), and `baseSpeed` / `speedFalloff` / `speedFloorFraction` (client-side prediction of your own pieces). The client no longer has a copy of the numbers.
- **Drawn radius is the physics eat disc.** `render.js` `radiusForMass` used to grow far faster than `simulation.radius_for_mass` (`sqrt(mass / π)`), so at mass 1000 the picture was ~200 world units against an 18-unit eat disc — food under the rim was not edible, which read as lag once anyone grew past spawn. The disc, protection ring, labels and spectate hit-test now use the eat radius. `followCamera`'s `baseSpan` dropped from 420 to 80 so a giant is not a speck. Eat tests on the server are unchanged; the collision disc was not grown to match the old drawing.
- **Own pieces are predicted; everyone else is interpolated.** Source plan section 6 calls interpolation non-optional so 30Hz state does not stutter at 60fps. That blend is always between two already-received snapshots, so off-LAN the body you steer sat a full RTT behind the mouse. `game.js` now dead-reckons *your* pieces from the latest snapshot plus the last sent `(dx, dy)` at `speed_for_mass`, decaying the error when the next snapshot arrives, and still interpolates every other player. Kick velocity is not on the wire, so a split/merge/eat snaps instead of faking the pop. Input is sent every animation frame when the unit vector changes, not throttled to 20Hz. WebSocket stays; UDP/WebRTC is still deferred.
- **No JavaScript is under test.** Every other `[Agent]` box in this doc names a test that proves it. `client/game.js` holds the mode machine, the follow-cam handoff after `welcome`, the food splice, own-blob prediction, per-frame input and the Game Over path, and none of it is proved by anything but the Phase 3 and 4 human checklists. Two source-grep tests were briefly added and removed: asserting that `"WebSocket"` and `"0.75"` appear in the served files proved nothing about behaviour while breaking on any rewording. The gap is real and tracked in [`feel-pass.md`](feel-pass.md) D, not papered over here.
- **A dropped socket reconnects, but the life it was holding does not.** Not in the source plan, which never mentions the connection closing. `game.js` retries with a doubling backoff from 0.5s to 8s behind a "Connection lost" overlay with a Retry button, because without it a closed socket left the last snapshot on screen forever, frozen and captioned with a live-looking mass — and during Phase 4, with the server being restarted constantly, that is indistinguishable from the desync the exit criterion asks about. On reconnect the client drops to the greeting menu rather than rejoining: `websocket_handler` removed the player when the socket closed, so there is no life left to resume, and rejoining silently would read as a teleport back to spawn mass. A spectator lost nothing and carries on. Buffered snapshots are discarded on open, or interpolation would slide every blob across the gap.
- **Names are unique among live players; colors are not.** Source section 4 puts `name` on the wire and says nothing about collisions. Two blobs both labelled "jack" makes the labels useless for the one thing they exist to do, so `protocol.unique_name` appends ` (2)`, ` (3)`… to a name a live player already holds, compared case-insensitively — "Jack" and "jack" are the same name to anyone reading them. The chosen casing survives; the base is truncated so the result still fits `NAME_MAX_LEN`. Colors are deliberately left to collide: a hex picker cannot promise uniqueness without overriding the player's choice, and the name already tells the two apart. Suffixing rather than rejecting, because a rejection needs a new wire message and an error state in the menu, and it can fail a *respawn* — Game Over resends the name that life used, which someone else may have taken meanwhile — stranding the client on an overlay whose only button no longer works. Renaming always succeeds, and the client shows the result for free because labels are drawn from `state`, not from what was typed. A name frees the moment its owner dies or closes the tab.
- **Names, mass and the remerge countdown are drawn above the body, at a floored size, in their own pass.** Source section 6 asks only that players be drawn. `drawPieces` previously put the name inside the disc and skipped it below 13px of radius, which meant a freshly spawned player could not see their own name: mass 50 is a ~4-unit radius, about 10px at the follow-cam's spawn zoom. The name now sits just above the cluster — one label at the centroid's x, above the highest disc — at a size clamped to 11–16px from total mass, so splitting does not shrink the identity or stamp the name on every fragment. Total mass is stacked under it, same treatment, because a number that vanished inside a spawn-size disc could not tell you whether the blob in front of you was food or a predator. After a split, seconds until the cluster can remerge (`remerge_in`, the longest remaining wait among its pieces) sit under the mass in gold, and as a HUD chip next to mass; both hide at 0 and on a solo blob that has nothing to merge with. The value is derived from `World.now` the same way `protected` is — a remaining duration, not a timestamp, so a client cannot hurry the merge. A split player also keeps a floored mass inside each piece: eat is per-piece, so the fragment's number is what decides the fight. Outlined so the labels read against the backdrop and against other blobs, and drawn in a second pass so a small blob painted later cannot cover a bigger one's label.

---

## Setup (once, before Phase 1)

- [x] **[Human]** Confirm Python 3.13 is on PATH (`python --version`).
- [x] **[Human]** Confirm git is installed and configured with your name/email.
- [x] **[Agent]** Repo scaffold: `docs/`, `server/`, `client/`, `bots/`, `tests/`, `tools/`, `.gitignore`, `README.md`, `requirements.txt` (starts with `pytest`; `aiohttp` lands in Phase 2). `scripts/` arrives in Phase 7.
- [x] **[Human]** VirtualBox + Ubuntu VM already exist and boot. (Only needed for Phase 7.)
- [x] **[Human]** You can SSH into the VM through the existing external `2222 → VM:22` forward. (Only needed for Phase 7.)

---

## Phase 1 — Core simulation, no networking

Goal: run the tick loop in isolation and confirm movement, eating and splitting behave per section 5 of the source plan, plus the cluster behaviour this build adds on top.

### Simulation clock

The server owns time, and this is the one place that says so.

`World.now` is the simulation clock. Nothing but `simulation.step` advances it, and only ever by an interval the server measured itself, from `SIMULATION_CLOCK_SOURCE`. Every timer in the game — the remerge wait, the kick decay — is a comparison against `World.now`. No message on the wire carries or influences a timestamp, so a client cannot run its clock fast to reach a remerge or shed a split kick early, and two clients cannot disagree about when something happened.

Two consequences worth keeping:

- **Sim time is measured, not assumed.** The loop advances `World.now` by real elapsed time capped at `MAX_TICK_SECONDS`, not by a fixed `1/TICK_RATE`. A tick that runs long is reflected honestly; a hitch clamps instead of teleporting every blob across the map. Sim time falls behind real time, which is the safe direction.
- **The clock is monotonic**, so correcting the host's wall clock mid-game cannot move time backwards.

Because the clock lives on the world rather than in a module global, a scenario or a test can drive time by hand, which is what makes replay and the recorder possible.

### Fill in each server file

- [x] **[Agent]** `server/config.py`.
  - **From source plan section 5:** `TICK_RATE = 30`, `MIN_SPLIT_MASS = 35`, `MAX_PIECES = 8`, `EAT_RATIO = 1.25`, `SPLIT_KICK_DECAY_SECONDS = 0.5`. Plus `REMERGE_SECONDS = 12`, our pick from the plan's 10–15s range.
  - **Clock:** `SIMULATION_CLOCK_SOURCE` (monotonic) and `MAX_TICK_SECONDS`, per the section above.
  - **World:** `WORLD_WIDTH`, `WORLD_HEIGHT`, `FOOD_COUNT`, `FOOD_MASS`, `INITIAL_PLAYER_MASS` (above `MIN_SPLIT_MASS`, so a fresh player can split without eating first). Plus `SPAWN_INVULN_SECONDS` (addition — see Divergence), added in Phase 2 when `join` started choosing spawn points.
  - **Movement:** `BASE_SPEED`, `SPEED_FALLOFF` and `speed_for_mass(mass)` — agar.io style, speed decreasing as mass grows. `split_kick_speed(mass)` — displacement is `SPLIT_KICK_RADII` parent radii, capped at `split_kick_displacement_max()` (`SPLIT_KICK_MAX_ARENA_FRACTION` of the shorter arena axis). The decay window is still `SPLIT_KICK_DECAY_SECONDS`; only the initial magnitude scales.
  - **Cluster and collision** (additions — see Divergence): `OWN_PIECE_OVERLAP` < `EAT_OVERLAP` < `MERGE_OVERLAP`, all thresholds on engulfment depth, where 0.0 is circles just touching and 1.0 is the smaller fully inside the larger. The ordering is the design: pieces rest in contact, and eating or merging demands real penetration past that resting depth. `OWN_PIECE_OVERLAP < MERGE_OVERLAP` in particular, or the merge pull has no distance to cover and a merge becomes a snap. Plus `COHESION_SPEED`, `MERGE_PULL_SPEED`, `MERGE_RECALL`, `SEPARATION_PASSES`.
- [x] **[Agent]** `server/models.py` — dataclasses only, no behavior: `Piece(piece_id, x, y, mass, vx, vy, initial_kick_vx, initial_kick_vy, split_time)` where `split_time` gates remerge, `initial_kick_vx/vy` hold the split kick that motion integrates, and `vx/vy` are recomputed from it each tick so the wire and the debug overlays have a current velocity to read; `Player(id, name, pieces, last_input, color, spawn_time, last_total_mass)`, where the last three arrived in Phase 2: `color` rides the wire, `spawn_time` gates spawn invulnerability, and `last_total_mass` is the mid-tick high-water mark `peak_mass` reads (see Divergence for both); `Food(id, x, y)`.
- [x] **[Agent]** `server/world.py` — `World` holds `players` and `food` dicts, plus `now` (the simulation clock), a seeded `rng` and an optional `food_target`. Methods `spawn_player`, `spawn_food_to_target_count`, `remove_player`, `new_id`. IDs are uuid4-shaped but drawn from the world's seeded RNG, so a given seed replays exactly; `uuid.uuid4()` directly would not. Phase 2 made `spawn_player`'s `x`/`y` optional — omitted, they come from the world RNG and are clamped into the rectangle — and gave it a `color`.
- [x] **[Agent]** `server/simulation.py` — `step(world, dt)`, which mutates the world in place and holds no module state, so a given `(world, dt)` always produces the same result. In this order:
  1. Apply each player's `last_input` as a normalized direction scaled by `speed_for_mass(piece.mass)`, plus the decaying split kick, and integrate position. Non-finite input is dropped: NaN compares false against every threshold below, so one bad value would skip the separation bail and spread into whatever it touched.
  2. Cluster forces: draw a player's own pieces toward each other at `COHESION_SPEED`, or home merge-ready pieces on the cluster centroid at `MERGE_PULL_SPEED + MERGE_RECALL * distance` once their remerge timers have cleared, so a fragment that drifted off still returns. Skipped for any pair whose split kick is still active, so cohesion never fights the kick. Merge-ready pieces also steer at the whole body's `speed_for_mass`, so a light leftover cannot outrun the core and hover outside it.
  3. Resolve collisions by mass-weighted position projection, then clamp each piece so its **disc** stays inside the world (`center` inset by `radius`). Own pieces settle at `OWN_PIECE_OVERLAP` depth; different players' pieces are solid, *unless* one can eat the other, in which case they are left free to interpenetrate — and a spawn-protected prey is not an eat, so that pair stays solid and the predator is shoved off. **The clamp wins over separation**: a pair crushed into a corner keeps residual overlap. Bounds are inviolable, overlap is cosmetic.
  4. Player-food collision: the piece's disc covers the pellet's center at some point during this tick's travel (a swept segment test, so a light fragment that hops more than its own diameter still collects food on the path). Radius = `sqrt(mass / π)`.
  5. Cross-player eating, split fragments included as both predator and prey (the source plan's "split-piece eating"), using the `A.mass > B.mass * 1.25` rule from section 5 and additionally requiring `EAT_OVERLAP` engulfment depth, so a graze is a shove rather than a kill. A player's own pieces are never candidates — they can only remerge. A spawn-protected player is never prey in either direction of the scan, though it eats normally; protection is held by the player, so splitting during it neither forfeits nor extends it. Every player's total mass is recorded here, before eaten pieces are removed, which is the only moment a dying player's final mass exists.
  6. Decay `vx/vy` toward zero over `SPLIT_KICK_DECAY_SECONDS`. Nothing but the split kick ever writes these, so they stay meaningful on the wire.
  7. Remerge same-player pieces whose `split_time` is older than `REMERGE_SECONDS` and whose bodies have sunk to `MERGE_OVERLAP` — deeper than pieces rest at, so the merge pull has to drag them the last of the way.
  8. Respawn food up to `FOOD_COUNT`. Any pellet whose center landed inside a piece is eaten at rest and replaced, so a spawn on a blob never survives into the broadcast. Bounded by `FOOD_COUNT` extra cycles so a disc that covered the world cannot loop forever.

  Also expose `try_split(world, player)`, which aims the kick along `player.last_input` (the wire message carries no direction), splits **every** piece at or above `MIN_SPLIT_MASS` largest-first in one call, and stops at `MAX_PIECES`. Note this exceeds source plan section 5, which describes splitting a single piece.
- [x] **[Agent]** `server/demo.py` — the Phase 1 console harness (originally `server/main.py`). `asyncio` loop sleeping to a tick deadline (remainder of `1/TICK_RATE`, slipping on overrun rather than bursting), calling `simulation.step` with measured elapsed time per the clock contract above. Builds a `World` with 2 hardcoded players: **A** whose `last_input` is recomputed each tick to point at the nearest food, **B** moving in a slow circle. Both start at `DEMO_MASS`, well above spawn size, so mass growth is legible over the short watch. Every ~30 ticks (~1s), prints one summary line: `tick N | A pieces=[m1,m2] pos=(x,y) | B pieces=[m3] pos=(x,y) | food=K`, where a player holding more than one piece also gets ` at=(x,y) (x,y)` — a centroid alone cannot show a split, since two symmetric halves have the same centroid as the whole. After ~3s, call `try_split` on A once so splitting → decay → remerge is visible. Ctrl+C exits cleanly. Phase 2's `server/main.py` is the aiohttp entrypoint.

### Verification tooling (Phase 1)

Not in the source plan. It exists because half the Phase 1 checklist is about motion, and motion cannot be judged from a console.

- [x] **[Agent]** `tests/` — `test_simulation.py` covers every deterministic box below; `test_dt_invariance.py` additionally pins that the same sim time produces the same state at 15Hz / `TICK_RATE` / 60Hz, so nothing quietly becomes frame-rate dependent; `test_main.py` pins the harness helpers in `server/demo.py` and `server/loop.py`, the exact summary line format above, and the tick loop's rate, its `dt`, and its survival of a failing tick. Phase 2 added `test_protocol.py` (message parsing, join, death, `peak_mass`, all without a socket) and `test_ws.py` (round-trips against a real aiohttp app).
- [x] **[Agent]** `tools/scenarios.py` + `tools/record.py` — one scripted, seeded scenario per verify box, recording real `simulation.step` output to `client/recordings/` (generated artifacts, gitignored).
- [x] **[Agent]** `client/render.js` — snapshot renderer, camera, interpolation, plus debug overlays for velocity and merge-readiness. **Survives into Phase 3**, where `game.js` imports it as-is.
- [x] **[Agent]** `client/viewer.html`, `client/viewer.js`, `client/recording.js`, and the viewer half of `client/style.css` — Phase 1 scaffolding. Kept as a regression harness; served only by `python -m tools.record --serve`, not by the game server.

### Verify each behavior

- [x] **[Both]** Piece moves in the direction of its player's `last_input`. Mechanism: `test_piece_moves_in_direction_of_input`. Watch it for legibility — does the blob visibly go where it was pointed?
- [x] **[Agent]** Speed decreases as mass grows (bigger blob is slower). `test_speed_for_mass_decreases_as_mass_grows`, `test_heavier_piece_travels_less_per_tick`.
- [x] **[Agent]** Piece stays inside world bounds — the disc, not just the center, including when crushed into a corner. `test_piece_stays_inside_world_bounds`, `test_bounds_hold_when_blobs_are_crushed_into_a_corner`, `test_body_stays_inside_world_in_every_corner`.
- [x] **[Agent]** Food gets eaten when a piece's circle covers the food's center at some point during the tick's travel; piece mass increases; food is removed and respawns. A pellet that respawns inside a disc is eaten the same tick. `test_food_inside_radius_is_eaten`, `test_food_outside_radius_is_not_eaten`, `test_food_just_inside_radius_is_eaten`, `test_food_just_outside_radius_is_not_eaten`, `test_food_on_the_path_is_eaten_even_when_travel_exceeds_radius`, `test_eaten_food_is_respawned_to_target_count`, `test_food_that_spawns_on_a_blob_is_eaten_the_same_tick`.
- [x] **[Agent]** Player-vs-player eat rule: `A.mass > B.mass * 1.25` is required, and equal or near-equal blobs don't eat each other — in both join orders, since the eat check is a two-branch scan and the earlier-joining player is tested first. Eating is per-piece: a predator that overlaps one fragment of a split prey leaves the others alive, and those survivors remerge with only the uneaten mass. `test_eat_requires_mass_ratio_above_1_25`, `test_a_predator_that_joins_late_eats_an_earlier_prey`, `test_eating_some_pieces_of_a_split_prey_leaves_the_rest_to_remerge`.
- [x] **[Agent]** A player's own pieces never eat each other — they can only remerge. `test_own_pieces_never_eat_each_other`, which stages the pair against a wall: in open field, separation runs before the eat check and holds them too shallow to be eaten anyway, so the test would pass with the rule deleted.
- [x] **[Both]** Different players' pieces collide solidly when neither can eat the other; a predator is not blocked by its prey. Mechanism: `test_equal_players_collide_instead_of_passing_through`, `test_a_predator_is_not_blocked_by_its_prey`, `test_a_graze_does_not_eat_until_the_predator_sinks_in`. Watch it: does contact *look* solid, or mushy?
- [x] **[Agent]** `try_split` refuses to split a piece under `MIN_SPLIT_MASS`, and allows exactly `MIN_SPLIT_MASS`. `test_try_split_refuses_below_min_split_mass`, `test_try_split_allows_exactly_min_split_mass`.
- [x] **[Agent]** `try_split` refuses to split when the player already has `MAX_PIECES`, and stops partway through once it reaches the cap. Largest eligible pieces split first. `test_try_split_refuses_at_max_pieces`, `test_try_split_stops_once_max_pieces_is_reached`, `test_try_split_splits_the_largest_pieces_first`.
- [x] **[Agent]** One press splits every piece at or above `MIN_SPLIT_MASS`, so piece count is exponential across presses while total player mass is unchanged: a 280 goes 1 → 2 → 4 → 8, halving in mass each time, then stops at the cap. `test_split_is_exponential_halving`.
- [x] **[Agent]** A successful split produces two pieces of half mass, and the new one gets a velocity kick along `last_input`. `test_split_produces_two_half_mass_pieces_and_one_kick`, `test_split_kick_points_along_last_input`, `test_split_with_zero_input_produces_no_kick`.
- [x] **[Agent]** Split kick decays to zero over ~`SPLIT_KICK_DECAY_SECONDS` and nothing else ever writes velocity. `test_split_kick_decays_to_zero`, `test_split_resets_a_leftover_kick_on_the_parent`.
- [x] **[Both]** Split halves pop apart on the kick, then drift back into contact on their own. Mechanism: `test_split_halves_drift_back_into_contact`, `test_cohesion_does_not_eat_into_the_split_kick`. Watch it: the pop has to read as a lunge and the return as a drift, not a snap.
- [x] **[Agent]** Two same-player pieces remerge after `REMERGE_SECONDS` when their circles overlap, and not before, and not when far apart. `test_pieces_remerge_after_timer_when_overlapping`, `test_pieces_do_not_remerge_before_timer`, `test_pieces_do_not_remerge_when_far_apart`, `test_split_pieces_remerge_after_the_full_cycle`.
- [x] **[Both]** Once the remerge timer clears, the pair visibly sinks into each other before merging. Mechanism: `test_pieces_in_contact_do_not_merge_until_they_sink_in`, `test_merge_pull_closes_the_gap_over_several_ticks`. Watch it: the sink is the whole point, and it lasts about four tenths of a second.
- [x] **[Agent]** `food` dict length stays at `FOOD_COUNT` over time. `test_food_count_stays_at_target_over_time`.

### How to verify

Three layers, in order. The console run is a smoke test, not the verification.

**1. Automated assertions — [Agent]**

```
python -m pytest
```

Everything tagged `[Agent]` above is proved here, by the named tests. All must pass.

**2. Visual scenarios — [Both]**

```
python -m tools.record --serve
```

Records every scenario and opens the viewer. One scripted clip per verify box, each stating what you should see. Velocity arrows show the split kick, dashed rings mark pieces whose remerge timer has cleared, and each clip plays at a speed chosen to suit it. Space plays and pauses, arrow keys step a frame at a time (shift for ten). Paused, the frame is drawn exactly as recorded, so the clock, the overlays and the bodies all describe the same tick — that is what makes measuring a sink or a kick possible.

Tick "I saw this behave correctly" per scenario; the sidebar tracks the count. This is the only credible way to judge the four `[Both]` boxes, which are all sub-second events smaller than a blob.

**3. Free-running smoke test — [Both]**

```
python -m server.demo
```

Watch for ~18 seconds. This confirms nothing crashes over a sustained run:

- A's total mass climbs as it eats; `food=` holds at `FOOD_COUNT`.
- Around t = 3s A goes from one piece to two. The `at=` field then shows the halves tens of units apart at their furthest — `SPLIT_KICK_RADII` parent radii of kick (~48 at `DEMO_MASS` 200), the rest is the shove that unstacks them — closing back to resting contact over the following second. The peak lasts a single tick and prints are a second apart, so expect to see something in the forties rather than the peak itself.
- Around t = 15.5s the pair merges back into one: 12s of remerge timer from the t = 3s split, plus ~0.4s for the merge pull to drag them from resting overlap down to `MERGE_OVERLAP`.

This run deliberately keeps A and B about 600 units apart, so it says **nothing** about the eat ratio, solid collision, or either split refusal. Those are layers 1 and 2.

Ctrl+C to stop.

### Phase 1 exit criteria

- [x] **[Human]** The four `[Both]` boxes above are ticked in the viewer and nothing feels wrong. Only then move on to Phase 2.

---

## Feel and fidelity pass

Non-blocking work banked during Phase 1 review: physics values that misbehave at the extremes, viewer and recorder polish, and the remaining test gaps. Listed in [`feel-pass.md`](feel-pass.md).

None of it blocks Phase 2, and some of it is better done *after* Phase 3, when the tuning values can be judged on a screen. Do not treat it as a gate.

---

## Phase 2 — WebSocket server (no browser client yet)

Goal: put the tick loop behind an aiohttp WebSocket endpoint and confirm the protocol round-trips using a bare Python client that just prints received state.

- [x] **[Agent]** Add `aiohttp` to `requirements.txt`. Runtime deps are pinned there; pytest lives in `requirements-dev.txt`.
- [x] **[Both]** `pip install -r requirements-dev.txt` (or into a venv).
- [x] **[Agent]** Rewrite `server/main.py` as an aiohttp app: game files at `/`, WebSocket upgrade at `/ws`, tick loop as an asyncio task alongside the HTTP server. Single port (8000). Phase 3 replaced `add_static` with an explicit `/` route and a whitelist; see Divergence. The Phase 1 printout moved to `server/demo.py`.
- [x] **[Agent]** Bind `0.0.0.0`, not `127.0.0.1`, with `BLOBBY_HOST` / `BLOBBY_PORT` defaulting to `0.0.0.0:8000`. Phase 7's external test cannot pass otherwise, and the failure is indistinguishable from a bad port forward.
- [x] **[Agent]** Server → client broadcast every tick. `state` is source plan section 4 plus `color` and `protected` on each player (see Divergence). Pieces are `{piece_id, x, y, mass, remerge_in}` — remaining seconds until that fragment can remerge, derived from `World.now`, not a timestamp. Phase 3 moved food to its own message; see Divergence.
- [x] **[Agent]** Client → server messages: `join` (`name` + `color`), `input`, `split`. Connecting does not spawn. Store `last_input` per player and consume it on the next tick.
- [x] **[Agent]** `welcome` `{id}` to that socket after a successful join, so Phase 3 can follow-cam without matching on name.
- [x] **[Agent]** Drop the three messages a join landing mid-broadcast would make wrong: a previous life's `game_over`, a `state` naming the player before its `welcome`, and a `state` snapshotted before its join. See Divergence. `test_stale_game_over_is_dropped_if_the_socket_already_respawned`, `test_state_naming_the_player_is_held_until_welcome_is_sent`, `test_state_snapshotted_before_the_join_is_not_sent_after_welcome`, `test_a_spectator_receives_every_frame`, `test_the_first_state_after_welcome_contains_the_welcomed_id`.
- [x] **[Agent]** `SPAWN_INVULN_SECONDS` of invulnerability on a live `join`, because `join` is the first thing that ever chose a spawn point unattended. See Divergence. `test_a_joining_player_is_not_eaten_until_spawn_protection_expires`, plus the `test_a_spawn_protected_*` family in `test_simulation.py`.
- [x] **[Agent]** Reject `input` messages whose `dx`/`dy` are not finite, at the message boundary. The simulation drops them too, but a client sending them is a client to distrust. Malformed JSON is dropped; the connection stays up.
- [x] **[Agent]** `join` picks a spawn position from the world RNG and spawns at `INITIAL_PLAYER_MASS`. Phase 1 supplied coordinates by hand; nothing did that for a real player.
- [x] **[Agent]** Last piece eaten: remove the player from the world so the broadcast cannot emit ghosts, and send `game_over` `{peak_mass, survival_seconds}` to that socket. The socket stays open. The next `join` is a respawn (Customize changes name/color first; Respawn resends the last ones). Spectating is connecting and never sending `join`.
- [x] **[Agent]** `peak_mass` counts mass gained on the tick that killed the player, which is gone from `world` by the time the death is noticed. See Divergence. `test_peak_mass_counts_mass_gained_on_the_tick_that_kills_you`.
- [x] **[Agent]** On socket close, call `world.remove_player`. Without it every closed tab leaves a frozen blob in the world forever.
- [x] **[Agent]** Keep advancing sim time on measured elapsed time with the `MAX_TICK_SECONDS` clamp, exactly as Phase 1 does — not a fixed `1/TICK_RATE`. This is `loop.measured_dt`, pinned by `test_measured_dt_*` and, on the loop itself, by `test_tick_loop_advances_sim_time_by_measured_elapsed_not_the_tick_rate` and `test_tick_loop_clamps_a_hitch_instead_of_teleporting`. Note `tests/test_dt_invariance.py` does **not** cover this: it calls `simulation.step` directly and answers the other question, which is what the world does with a `dt` it is handed. Both still pass.
- [x] **[Agent]** Keep the tick's state-mutation section synchronous — no `await` mid-mutation (source plan section 3).
- [x] **[Agent]** A tick that raises is logged and skipped rather than cancelling the loop task. See Divergence. `test_tick_loop_survives_a_failing_tick`, `test_tick_loop_survives_a_failing_broadcast`.
- [x] **[Agent]** `tools/probe_client.py` — bare Python WebSocket client: connects, sends `join` unless `--spectate`, prints one line per received `state` / `welcome` / `game_over`, sends fake `input` occasionally.
- [x] **[Agent]** Run server + probe client and confirm the probe sees state broadcasts and its inputs are reflected in the state on the next tick.

### How to verify

```
python -m pytest
python -m server.main
```

The server logs one line on connect, one on `join`, and one on disconnect. Restart `python -m server.main` to pick up that logging if it is already running.

Then in other terminals:

```
python -m tools.probe_client --name A
python -m tools.probe_client --name B
python -m tools.probe_client --spectate
```

### Phase 2 exit criteria

- [x] **[Human]** Protocol round-trips cleanly with two probe clients connected at once. No log spam, no dropped connections on idle, and a probe that disconnects leaves no blob behind. A spectator (`--spectate`) sees those players and never appears in `players`.

---

## Phase 3 — Canvas client (one browser tab)

Goal: one browser tab can move and eat food against a localhost server.

- [x] **[Agent]** Explicit `/` route serving `index.html`, and a `PUBLIC_FILES` whitelist instead of `add_static`. `add_static("/", CLIENT_DIR)` returns 403 for `/` and would publish the viewer. See Divergence. `test_root_serves_the_menu`, `test_game_client_files_are_served`, `test_viewer_and_recordings_are_not_public`.
- [x] **[Agent]** Food leaves the `state` broadcast. A `food` message of integer `[x, y]` pairs is sent only when the pellet set changes, or to a socket that has not yet received the current version. See Divergence. `test_food_is_sent_once_then_not_resent_while_unchanged`, `test_eating_a_pellet_resends_food`, `test_a_late_joiner_receives_the_current_food_field`, `test_a_failed_food_send_is_retried_without_advancing_the_cursor`, `test_state_frame_without_food_is_under_4kb`.
- [x] **[Agent]** Food that spawns on a blob is eaten the same tick, so a pellet never renders inside a body for a frame. `test_food_that_spawns_on_a_blob_is_eaten_the_same_tick`.
- [x] **[Agent]** `client/index.html` — greeting menu over a full-window canvas: name field, color picker, **Play**, **Spectate**. Play sends `join` with the chosen name and color. Spectate never sends `join`; the tab only receives `state`.
- [x] **[Agent]** Extend the existing `client/style.css` (written in Phase 1 for the viewer) with the full-window game canvas rules and the menu / Game Over overlay. Do not recreate it; the two pages share it.
- [x] **[Agent]** `client/game.js`, importing `client/render.js`:
  - `requestAnimationFrame` render loop, decoupled from server tick rate.
  - WebSocket connection to `/ws`. Do **not** send `join` on open — wait for Play. After `welcome`, follow-cam on that id: the server guarantees the next `state` this socket receives contains it, so follow the first snapshot that has it rather than assuming the very next frame is one.
  - Mouse position → normalized `dx/dy` relative to player center; send every animation frame when the unit vector changes. Ignored while spectating or on the menu.
  - Camera centered on the followed piece centroid, zooming out as total mass grows — `followCamera` in `render.js` already does this. Own-blob follow uses the predicted centroid.
  - **Interpolation between the last two received state snapshots** (source plan section 6 flags this as non-optional) for every player except yourself. `interpolateStates` in `render.js` does the blend; `game.js` owns buffering the last two snapshots, predicting your own pieces from the latest snapshot plus last input, and deciding the blend factor from elapsed time, absorbing a late or dropped tick. `interpolateStates` spreads player fields (including `color`) and `drawPieces` prefers `player.color` over `colorForId`.
  - On `game_over`: overlay "Game Over!" with peak mass and survival time, plus **Customize** (back to the greeting menu) and **Respawn** (send `join` again with the last name and color).
  - Spectate: mouse click focuses the blob under the cursor, or cycles when the click lands on empty world — next living player, then a map-fit camera that frames the whole arena (the same view Spectate starts on), then the first living player again. Escape returns to the greeting menu.
  - Hold the most recent `food` message and splice it into the snapshot before handing it to `drawWorld`. `render.js` still expects `state.food`.
  - Reconnect with a doubling backoff behind a "Connection lost" overlay, dropping to the menu on return because the lost life cannot be resumed. See [Divergence](#divergence-from-the-source-plan).
  - Every box in this bullet is `[Agent]` by ownership but `[Human]` by proof — see [Divergence](#divergence-from-the-source-plan) on the JavaScript test gap. The `[Human]` boxes below are the only thing standing behind this file.
- [x] **[Agent]** Every live player's name is unique, so two blobs cannot both be labelled "jack". `unique_name` suffixes rather than rejecting the join; colors are deliberately still allowed to collide. See [Divergence](#divergence-from-the-source-plan). `test_a_taken_name_is_suffixed_rather_than_duplicated`, `test_name_collision_ignores_case`, `test_a_name_is_free_again_once_its_owner_is_gone`, `test_a_suffixed_name_still_fits_the_length_cap`, `test_the_suffix_itself_is_not_duplicated`, `test_two_sockets_claiming_one_name_are_told_apart_but_keep_their_color`.
- [x] **[Agent]** Names are readable at every size, including your own at spawn mass, which the original in-disc label was too small to show. Drawn above the body in a second pass. See [Divergence](#divergence-from-the-source-plan).
- [x] **[Agent]** Decide the fate of `client/viewer.*`, `client/recording.js` and `tools/record.py`: keep as a regression harness, served only by `python -m tools.record --serve` on 8080. Not on the game port.
- [x] **[Human]** Start server, open `http://localhost:8000`, set a name and color, Play, confirm you can move around and eat food. Mass shown numerically somewhere for sanity. Confirm Spectate does not spawn a blob, and that dying shows Game Over with working Customize / Respawn.

### Phase 3 exit criteria

- [x] **[Human]** Movement feels smooth (no visible 30Hz stutter — that means interpolation is working). Food gets eaten reliably.
- [x] **[Human]** The Phase 1 cluster values (`COHESION_SPEED`, `MERGE_PULL_SPEED`, the three overlap thresholds) still feel right now that they are visible for the first time. Retune here if not; this is the first honest opportunity.

---

## Phase 4 — Two browser tabs

Goal: two players can see and eat each other.

- [x] **[Human]** Open two browser tabs at `http://localhost:8000`, use different names.
- [x] **[Human]** Confirm each tab renders the other player.
- [x] **[Human]** Grow one blob well past the other and confirm it can eat the smaller one (subject to the 1.25 mass ratio). Note that a heavier blob is *slower*, so walking into fleeing prey will not catch it — corner it, or split into it. Wait out `SPAWN_INVULN_SECONDS` first: a just-joined blob cannot be eaten and will be shoved instead.
- [x] **[Human]** Confirm a fully-eaten player sees Game Over with peak mass and survival time, and can Customize or Respawn, in both tabs. Peak mass should include anything swallowed on the fatal tick.
- [ ] **[Both]** Judge the spawn invulnerability window on a screen — is 3s enough to get clear, and does a shoved predator read as blocked rather than broken? Retune `SPAWN_INVULN_SECONDS` here. See [How to verify](#how-to-verify-phase-4) for a pinned-spawn recipe; without it the overlap is luck.
- [x] **[Agent]** Reconnect on a dropped socket, behind an overlay that says so. Without it a closed socket left the last snapshot on screen forever, frozen under a live-looking mass readout — indistinguishable from the desync the exit criterion below asks about. See [Divergence](#divergence-from-the-source-plan).
- [x] **[Human]** Give both tabs the *same* name and confirm the second becomes `name (2)`, so they are still tellable apart. The same color in both tabs is fine and expected — only names are made unique.
- [x] **[Human]** Confirm you can read your own name above your own blob the moment you spawn, at spawn mass, in both tabs.
- [x] **[Human]** Restart the server with both tabs open. Each should show "Connection lost", come back on its own, and land on the greeting menu with its name and color still filled in — not resume the life it lost.
- [ ] **[Agent]** Fix any bugs surfaced (state broadcast omissions, race conditions, wrong ownership checks, etc.) as you report them.

### How to verify (Phase 4)

The two-tab eating, names, labels and reconnect boxes above are already signed off. What remains is the spawn-window judgment, which needs overlap on purpose:

```
$env:BLOBBY_DEBUG_SPAWN="600,600"; python -m server.main
```

Then two tabs at `http://localhost:8000`:

1. Tab A: Play, eat until clearly past 1.25× spawn mass (~63+), **stay on the spawn point**, wait until your gold ring and "protected" chip are gone.
2. Tab B: Play. You should land inside/on Tab A, pop apart, and both tabs should show Tab B's ring for about five seconds.
3. Can Tab B get clear before the ring dies? Does Tab A's bump read as blocked by a shield rather than as broken collision?
4. After the ring dies, if Tab A is still overlapping and heavy enough, Tab B should die.
5. While you are there, glance at feel-pass A7: if two near-ratio blobs stutter between solid and overlapping, say so; otherwise leave hysteresis deferred.

Unset the env var (or restart the server without it) when you are done — default play is still a random spawn. If 5s feels short or long, retune `SPAWN_INVULN_SECONDS` in `server/config.py`; the tests import the constant.

### Phase 4 exit criteria

- [x] **[Human]** Two-player eating works both directions when the mass ratio is met. Neither tab desyncs after a few minutes of play. A tab that froze *without* showing the reconnect overlay is a real bug worth chasing; one that showed it merely lost its socket.

---

## Phase 5 — Splitting on the client

Goal: spacebar splits, following the section 5 rules verified in Phase 1.

- [x] **[Agent]** `game.js`: spacebar sends `{"type": "split"}`.
- [x] **[Human]** Grow a blob above `MIN_SPLIT_MASS` (35), press space, confirm you see two pieces flying apart.
- [x] **[Human]** Confirm split is refused (nothing happens) when under 35 mass.
- [x] **[Human]** Confirm split is refused when already at 8 pieces.
- [x] **[Human]** Confirm the split kick visually decays over ~0.5s.
- [x] **[Human]** Press space repeatedly and confirm the whole cluster halves each time (1 → 2 → 4 → 8), not just one piece.
- [x] **[Human]** Wait ~12s and confirm pieces remerge.
- [x] **[Human]** With two tabs open: split, then eat a smaller player's piece with the split fragment. Confirm the ratio rule still applies.

### How to verify (Phase 5)

Default spawn is already `INITIAL_PLAYER_MASS = 50`, so a fresh life can split once without eating. For the exponential / remerge / fragment-eat boxes, pin a mass that can go 1 → 2 → 4 → 8:

```
$env:BLOBBY_DEBUG_MASS="280"; python -m server.main
```

Unset it (or restart without it) to judge the under-35 refusal at a depleted fragment. Space is one split per physical press; held-key auto-repeat is ignored.

### Phase 5 exit criteria

- [x] **[Human]** Splitting feels responsive and matches the rules from source plan section 5.

---

## Phase 6 — Bots

Goal: N Python bot clients playing autonomously.

- [x] **[Agent]** `bots/simple_bot.py` — WebSocket client using the same `join`/`input`/`split` protocol. CLI args for name, server URL, count. On disconnect it either reconnects or exits cleanly, per the exit criterion below.
- [x] **[Agent]** Decision loop: [`docs/bot-logic.md`](bot-logic.md). Four states (Graze / Hunt / Flee / Recover), limited vision, per-piece eat/threat classification. No pathfinding. `input_toward_nearest_food` in `server/demo.py` is the graze seed, not the product.
- [x] **[Agent]** Run 3–5 bots against a local server and confirm the tick loop still holds its rate with that many players in the world. Smoke only — not the load bar.
- [x] **[Agent]** Run `python -m bots.simple_bot --count 30` against a local server. Confirm the tick loop still holds ~30Hz with that many players in the world (tick count vs wall-clock over ~30s, not a short burst). Server log lines `tick N players=P sockets=S hz=H`. Measured 2026-08-17: 30 sockets, ~30s, hz 29.5–30.3. Deployed hosts spawn that table themselves: Compose `bots` sidecar on production, Fargate `bots` sidecar on each PR preview (`--count 30`). Do not run this by hand against those URLs unless you want extras.
- [ ] **[Human]** Play against them in a browser tab. Confirm bots don't deadlock, don't spin in place, and don't crash on player disconnect.

### How to verify (Phase 6)

```
python -m server.main
python -m bots.simple_bot --count 30
```

Open `http://localhost:8000`, Play. Watch the server log for `hz=` near 30 with `players=30`. Ctrl+C on the bot process exits 0.

### Phase 6 exit criteria

- [ ] **[Human]** World feels alive with bots present. Bots survive server restarts (the client reconnects, or its process exits cleanly).
- [ ] **[Human]** Stress test: join as a **freshly spawning** player into a lobby with **≥30 bots**. The world feels alive (bots moving, eating, hunting around you). Your own movement, eating, and the spawn-invuln window stay responsive — not hitching, not a frozen or empty-feeling map.

---

## Phase 7 — Move to the VM, expose externally

Goal: reachable from outside the LAN, independent of the existing SSH forward.

### On the VM

- [x] **[Human]** Check the VM's VirtualBox adapter mode (Settings → Network). Note whether it's **Bridged** or **NAT** — routing setup below depends on this.
- [x] **[Both]** Copy the repo to the VM (`git clone` inside the VM is easiest once the VM has internet).
- [x] **[Agent]** Write a short `scripts/vm_bootstrap.sh` — installs Python 3.13 (or 3.12), `pip`, sets up a venv, `pip install -r requirements.txt`, ufw rule.
- [x] **[Agent]** Keep the Phase 1 viewer and `client/recordings/` out of the static mount. They are development tooling and this server faces the internet. Done in Phase 3: `PUBLIC_FILES` whitelist, viewer served only by `tools/record.py --serve`.
- [x] **[Human]** Run `scripts/vm_bootstrap.sh` on the VM.
- [x] **[Human]** `sudo ufw allow 8000/tcp` on the VM (also done by the bootstrap script; verify with `sudo ufw status`).
- [x] **[Human]** Start the server on the VM. Ideally under a systemd unit or `tmux`/`screen` so it survives your SSH session.

### On VirtualBox (only if adapter is NAT)

- [x] **[Human]** VM Settings → Network → Advanced → Port Forwarding: add `host:8000 → guest:8000`.

### On the router

- [x] **[Human]** Add a **new** port forward, independent of the existing `2222 → VM:22` SSH rule. Do not modify the SSH rule.
  - Bridged adapter: `external:8000 → <VM LAN IP>:8000`.
  - NAT adapter: `external:8000 → <Windows host LAN IP>:8000`. Spectrum (and any consumer router) can only see the PC. That is the whole NAT shape: router → host:8000, VirtualBox `host:8000 → guest:8000`. Do not look for the VM's 10.0.2.x address in the Spectrum app; it is not on the LAN. Same hop chain as SSH, just port 8000 instead of 2222. Allow TCP 8000 inbound on the Windows firewall or the host hop dies there.

### External test

- [x] **[Human]** From a machine on the LAN, hit `http://<VM LAN IP>:8000` (bridged) or `http://<host LAN IP>:8000` (NAT). Confirm the client loads and connects.
- [x] **[Human]** Compare the router's reported WAN IP to what a site like whatismyip.com sees. Mismatch = your ISP has you behind carrier-grade NAT and no port forward will work.
- [x] **[Human]** If not behind CGNAT: from a device on cellular (off Wi-Fi), hit `http://<WAN IP>:8000`. Confirm it loads.
- [x] **[Both]** If behind CGNAT or the router refuses: fall back to ngrok or a Cloudflare Tunnel and re-test. This is `[Both]` because the agent can write the tunnel setup script but you have to sign in.

### Phase 7 exit criteria

- [x] **[Human]** Someone off your LAN loads the game URL and plays a round. That's the POC done.

---

## Deferred — do not build unless explicitly asked

Copied verbatim from source plan section 9 so nothing sneaks in early:

- UDP / WebRTC DataChannel transport.
- Area-of-interest broadcast culling.
- DDNS / stable URL.
- TLS / `wss://`.
- Viruses, mass ejection, mass-scaled remerge timers.
- Protobuf serialization in place of the JSON payloads. WebSocket already carries binary frames, so this is a drop-in swap at the serialization layer only, no change to transport or message semantics. Defer until message shapes are stable and protocol iteration has slowed, since binary schemas add recompile friction during active field changes and remove the ability to read wire messages in the browser's network tab while debugging.
