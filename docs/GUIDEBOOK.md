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
- **Split is exponential.** Source section 5 says "piece becomes two pieces of half mass", singular. One press here splits every eligible piece, as agar.io does.
- **`REMERGE_SECONDS = 12`** is a pick from the source plan's "flat 10–15s" range, not a value the plan states.
- **A simulation clock.** The source plan never says where "now" comes from. See [Simulation clock](#simulation-clock).
- **Verification tooling.** `tests/`, `tools/` and the browser viewer are not in the source plan. See [Verification tooling](#verification-tooling-phase-1).
- **Phase 3 render core built early.** `client/render.js` and `client/style.css` were written during Phase 1 so the viewer had something to draw with. That breaks the "each phase before the next" rule; it is recorded here so the rule's failure is visible rather than assumed.
- **Greeting menu, color, and death.** Source section 4 has `join` / `input` / `split` and a `state` broadcast of `{id, name, pieces}`. Connecting here does not spawn. `join` carries a player-chosen `color`; the server replies with `welcome` `{id}` so a client can follow-cam without matching on name. A last-piece eat removes the player from the world and sends `game_over` `{peak_mass, survival_seconds}` on that socket only. The next `join` on the same socket is a respawn. Spectating is connecting and never sending `join`. Phase 3 owns the menu and Game Over UI; Phase 2 owns the protocol so that UI can exist. `state` players also carry `color`.
- **Phase 1 console harness lives in `server/demo.py`.** Phase 2 rewrote `server/main.py` as the aiohttp entrypoint. `python -m server.demo` is the old two-player printout.
- **Runtime vs dev requirements.** `requirements.txt` is pinned `aiohttp` only, so Phase 7's VM bootstrap does not install pytest. Tests install from `requirements-dev.txt`.
- **Spawn invulnerability.** `SPAWN_INVULN_SECONDS = 3` — not in the source plan, and a consequence of `join` picking a spawn point from the RNG. That point is clamped into the rectangle, never away from other bodies, so a join can land inside a blob heavy enough to eat it on the next tick and a player would be dead before the first frame renders. For that window the player cannot be eaten but eats normally, and a predator on top of it is shoved rather than left to interpenetrate. **Granted by a live `join` only** — `world.spawn_player` leaves `Player.spawn_time` already expired, so every Phase 1 test, scenario and the demo harness stage edible players exactly as before. Another feel parameter: 3s is a pick from the 2–5s range, to be judged on a screen in Phase 3.
- **The broadcast is guarded against a join landing mid-tick.** `_emit` awaits each socket in turn, so a `join` handled during that loop splits the broadcast into sockets served before it and sockets served after. Three messages go wrong in that window, and each is dropped rather than sent: a `game_over` from the previous life, which would arrive after the respawn's `welcome` and end a game that just started; a `state` naming the new player *before* its `welcome`, which hands the client an id it does not yet know is its own; and the pre-join snapshot *after* `welcome`, which omits the player entirely so Phase 3's follow-cam finds nothing to follow. A playing socket simply misses that one frame — the next tick's snapshot is correct for it. Spectators are never withheld anything.
- **`peak_mass` is observed mid-tick.** `update_and_eliminate` runs after `simulation.step`, by which point a player killed this tick has no pieces left to measure. `simulation` records `Player.last_total_mass` just before eaten pieces are culled, so mass picked up on the tick that killed you — a pellet, or a fragment of the blob that then ate you — still reaches Game Over.
- **A failed tick is logged, not fatal.** The tick loop catches exceptions from `process_tick` and from the broadcast and continues. Without it, one throw cancels the task and leaves the HTTP and WebSocket endpoints answering normally over a world that has silently stopped, which reads as a network fault. A throw partway through `step` can leave the world half-mutated; for a POC a visible traceback and a resumed loop beats a frozen one.

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
  - **Movement:** `BASE_SPEED`, `SPEED_FALLOFF` and `speed_for_mass(mass)` — agar.io style, speed decreasing as mass grows. `SPLIT_KICK_SPEED`, whose total displacement is `SPLIT_KICK_SPEED * SPLIT_KICK_DECAY_SECONDS / 2`.
  - **Cluster and collision** (additions — see Divergence): `OWN_PIECE_OVERLAP` < `EAT_OVERLAP` < `MERGE_OVERLAP`, all thresholds on engulfment depth, where 0.0 is circles just touching and 1.0 is the smaller fully inside the larger. The ordering is the design: pieces rest in contact, and eating or merging demands real penetration past that resting depth. `OWN_PIECE_OVERLAP < MERGE_OVERLAP` in particular, or the merge pull has no distance to cover and a merge becomes a snap. Plus `COHESION_SPEED`, `MERGE_PULL_SPEED`, `SEPARATION_PASSES`.
- [x] **[Agent]** `server/models.py` — dataclasses only, no behavior: `Piece(piece_id, x, y, mass, vx, vy, initial_kick_vx, initial_kick_vy, split_time)` where `split_time` gates remerge, `initial_kick_vx/vy` hold the split kick that motion integrates, and `vx/vy` are recomputed from it each tick so the wire and the debug overlays have a current velocity to read; `Player(id, name, pieces, last_input, color, spawn_time, last_total_mass)`, where the last three arrived in Phase 2: `color` rides the wire, `spawn_time` gates spawn invulnerability, and `last_total_mass` is the mid-tick high-water mark `peak_mass` reads (see Divergence for both); `Food(id, x, y)`.
- [x] **[Agent]** `server/world.py` — `World` holds `players` and `food` dicts, plus `now` (the simulation clock), a seeded `rng` and an optional `food_target`. Methods `spawn_player`, `spawn_food_to_target_count`, `remove_player`, `new_id`. IDs are uuid4-shaped but drawn from the world's seeded RNG, so a given seed replays exactly; `uuid.uuid4()` directly would not. Phase 2 made `spawn_player`'s `x`/`y` optional — omitted, they come from the world RNG and are clamped into the rectangle — and gave it a `color`.
- [x] **[Agent]** `server/simulation.py` — `step(world, dt)`, which mutates the world in place and holds no module state, so a given `(world, dt)` always produces the same result. In this order:
  1. Apply each player's `last_input` as a normalized direction scaled by `speed_for_mass(piece.mass)`, plus the decaying split kick, and integrate position. Non-finite input is dropped: NaN compares false against every threshold below, so one bad value would skip the separation bail and spread into whatever it touched.
  2. Cluster forces: draw a player's own pieces toward each other at `COHESION_SPEED`, or at `MERGE_PULL_SPEED` once both of a pair's remerge timers have cleared. Skipped for any pair whose split kick is still active, so cohesion never fights the kick.
  3. Resolve collisions by mass-weighted position projection, then clamp each piece so its **disc** stays inside the world (`center` inset by `radius`). Own pieces settle at `OWN_PIECE_OVERLAP` depth; different players' pieces are solid, *unless* one can eat the other, in which case they are left free to interpenetrate — and a spawn-protected prey is not an eat, so that pair stays solid and the predator is shoved off. **The clamp wins over separation**: a pair crushed into a corner keeps residual overlap. Bounds are inviolable, overlap is cosmetic.
  4. Player-food collision: the piece's disc covers the pellet's center at some point during this tick's travel (a swept segment test, so a light fragment that hops more than its own diameter still collects food on the path). Radius = `sqrt(mass / π)`.
  5. Cross-player eating, split fragments included as both predator and prey (the source plan's "split-piece eating"), using the `A.mass > B.mass * 1.25` rule from section 5 and additionally requiring `EAT_OVERLAP` engulfment depth, so a graze is a shove rather than a kill. A player's own pieces are never candidates — they can only remerge. A spawn-protected player is never prey in either direction of the scan, though it eats normally; protection is held by the player, so splitting during it neither forfeits nor extends it. Every player's total mass is recorded here, before eaten pieces are removed, which is the only moment a dying player's final mass exists.
  6. Decay `vx/vy` toward zero over `SPLIT_KICK_DECAY_SECONDS`. Nothing but the split kick ever writes these, so they stay meaningful on the wire.
  7. Remerge same-player pieces whose `split_time` is older than `REMERGE_SECONDS` and whose bodies have sunk to `MERGE_OVERLAP` — deeper than pieces rest at, so the merge pull has to drag them the last of the way.
  8. Respawn food up to `FOOD_COUNT`.

  Also expose `try_split(world, player)`, which aims the kick along `player.last_input` (the wire message carries no direction), splits **every** piece at or above `MIN_SPLIT_MASS` largest-first in one call, and stops at `MAX_PIECES`. Note this exceeds source plan section 5, which describes splitting a single piece.
- [x] **[Agent]** `server/demo.py` — the Phase 1 console harness (originally `server/main.py`). `asyncio` loop sleeping to a tick deadline (remainder of `1/TICK_RATE`, slipping on overrun rather than bursting), calling `simulation.step` with measured elapsed time per the clock contract above. Builds a `World` with 2 hardcoded players: **A** whose `last_input` is recomputed each tick to point at the nearest food, **B** moving in a slow circle. Both start at `DEMO_MASS`, well above spawn size, so mass growth is legible over the short watch. Every ~30 ticks (~1s), prints one summary line: `tick N | A pieces=[m1,m2] pos=(x,y) | B pieces=[m3] pos=(x,y) | food=K`, where a player holding more than one piece also gets ` at=(x,y) (x,y)` — a centroid alone cannot show a split, since two symmetric halves have the same centroid as the whole. After ~3s, call `try_split` on A once so splitting → decay → remerge is visible. Ctrl+C exits cleanly. Phase 2's `server/main.py` is the aiohttp entrypoint.

### Verification tooling (Phase 1)

Not in the source plan. It exists because half the Phase 1 checklist is about motion, and motion cannot be judged from a console.

- [x] **[Agent]** `tests/` — `test_simulation.py` covers every deterministic box below; `test_dt_invariance.py` additionally pins that the same sim time produces the same state at 15Hz / `TICK_RATE` / 60Hz, so nothing quietly becomes frame-rate dependent; `test_main.py` pins the harness helpers in `server/demo.py` and `server/loop.py`, the exact summary line format above, and the tick loop's rate, its `dt`, and its survival of a failing tick. Phase 2 added `test_protocol.py` (message parsing, join, death, `peak_mass`, all without a socket) and `test_ws.py` (round-trips against a real aiohttp app).
- [x] **[Agent]** `tools/scenarios.py` + `tools/record.py` — one scripted, seeded scenario per verify box, recording real `simulation.step` output to `client/recordings/` (generated artifacts, gitignored).
- [x] **[Agent]** `client/render.js` — snapshot renderer, camera, interpolation, plus debug overlays for velocity and merge-readiness. **Survives into Phase 3**, where `game.js` imports it as-is.
- [x] **[Agent]** `client/viewer.html`, `client/viewer.js`, `client/recording.js`, and the viewer half of `client/style.css` — Phase 1 scaffolding. Fate decided in Phase 3.

### Verify each behavior

- [ ] **[Both]** Piece moves in the direction of its player's `last_input`. Mechanism: `test_piece_moves_in_direction_of_input`. Watch it for legibility — does the blob visibly go where it was pointed?
- [x] **[Agent]** Speed decreases as mass grows (bigger blob is slower). `test_speed_for_mass_decreases_as_mass_grows`, `test_heavier_piece_travels_less_per_tick`.
- [x] **[Agent]** Piece stays inside world bounds — the disc, not just the center, including when crushed into a corner. `test_piece_stays_inside_world_bounds`, `test_bounds_hold_when_blobs_are_crushed_into_a_corner`, `test_body_stays_inside_world_in_every_corner`.
- [x] **[Agent]** Food gets eaten when a piece's circle covers the food's center at some point during the tick's travel; piece mass increases; food is removed and respawns. `test_food_inside_radius_is_eaten`, `test_food_outside_radius_is_not_eaten`, `test_food_just_inside_radius_is_eaten`, `test_food_just_outside_radius_is_not_eaten`, `test_food_on_the_path_is_eaten_even_when_travel_exceeds_radius`, `test_eaten_food_is_respawned_to_target_count`.
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
- Around t = 3s A goes from one piece to two. The `at=` field then shows the halves ~36 units apart at their furthest — 30 of that is the kick's own displacement, the rest is the shove that unstacks them — closing back to resting contact over the following second. The peak lasts a single tick and prints are a second apart, so expect to see something in the twenties rather than the peak itself.
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
- [ ] **[Both]** `pip install -r requirements-dev.txt` (or into a venv).
- [x] **[Agent]** Rewrite `server/main.py` as an aiohttp app: static file serving mounted at `/`, WebSocket upgrade at `/ws`, tick loop as an asyncio task alongside the HTTP server. Single port (8000). The Phase 1 printout moved to `server/demo.py`.
- [x] **[Agent]** Bind `0.0.0.0`, not `127.0.0.1`, with `BLOBBY_HOST` / `BLOBBY_PORT` defaulting to `0.0.0.0:8000`. Phase 7's external test cannot pass otherwise, and the failure is indistinguishable from a bad port forward.
- [x] **[Agent]** Server → client broadcast every tick. `state` is source plan section 4 plus `color` on each player (see Divergence). Pieces stay `{piece_id, x, y, mass}`.
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

- [ ] **[Agent]** `client/index.html` — greeting menu over a full-window canvas: name field, color picker, **Play**, **Spectate**. Play sends `join` with the chosen name and color. Spectate never sends `join`; the tab only receives `state`.
- [ ] **[Agent]** Extend the existing `client/style.css` (written in Phase 1 for the viewer) with the full-window game canvas rules and the menu / Game Over overlay. Do not recreate it; the two pages share it.
- [ ] **[Agent]** `client/game.js`, importing `client/render.js`:
  - `requestAnimationFrame` render loop, decoupled from server tick rate.
  - WebSocket connection to `/ws`. Do **not** send `join` on open — wait for Play. After `welcome`, follow-cam on that id: the server guarantees the next `state` this socket receives contains it, so follow the first snapshot that has it rather than assuming the very next frame is one.
  - Mouse position → normalized `dx/dy` relative to player center; send throttled to ~20/sec. Ignored while spectating or on the menu.
  - Camera centered on the followed piece centroid, zooming out as total mass grows — `followCamera` in `render.js` already does this.
  - **Interpolation between the last two received state snapshots** (source plan section 6 flags this as non-optional). `interpolateStates` in `render.js` does the blend; `game.js` owns buffering the last two snapshots and deciding the blend factor from elapsed time, absorbing a late or dropped tick. **`interpolateStates` currently strips unknown player fields — pass `color` through.** Drawing should prefer `player.color` over `colorForId`.
  - On `game_over`: overlay "Game Over!" with peak mass and survival time, plus **Customize** (back to the greeting menu) and **Respawn** (send `join` again with the last name and color).
  - Spectate: mouse click cycles or focuses another player's blob without spawning this client.
- [ ] **[Agent]** Decide the fate of `client/viewer.*`, `client/recording.js` and `tools/record.py`: keep as a regression harness, or delete. Do not leave it ambiguous.
- [ ] **[Human]** Start server, open `http://localhost:8000`, set a name and color, Play, confirm you can move around and eat food. Mass shown numerically somewhere for sanity. Confirm Spectate does not spawn a blob, and that dying shows Game Over with working Customize / Respawn.

### Phase 3 exit criteria

- [ ] **[Human]** Movement feels smooth (no visible 30Hz stutter — that means interpolation is working). Food gets eaten reliably.
- [ ] **[Human]** The Phase 1 cluster values (`COHESION_SPEED`, `MERGE_PULL_SPEED`, the three overlap thresholds) still feel right now that they are visible for the first time. Retune here if not; this is the first honest opportunity.

---

## Phase 4 — Two browser tabs

Goal: two players can see and eat each other.

- [ ] **[Human]** Open two browser tabs at `http://localhost:8000`, use different names.
- [ ] **[Human]** Confirm each tab renders the other player.
- [ ] **[Human]** Grow one blob well past the other and confirm it can eat the smaller one (subject to the 1.25 mass ratio). Note that a heavier blob is *slower*, so walking into fleeing prey will not catch it — corner it, or split into it. Wait out `SPAWN_INVULN_SECONDS` first: a just-joined blob cannot be eaten and will be shoved instead.
- [ ] **[Human]** Confirm a fully-eaten player sees Game Over with peak mass and survival time, and can Customize or Respawn, in both tabs. Peak mass should include anything swallowed on the fatal tick.
- [ ] **[Both]** Judge the spawn invulnerability window on a screen — is 3s enough to get clear, and does a shoved predator read as blocked rather than broken? Retune `SPAWN_INVULN_SECONDS` here.
- [ ] **[Agent]** Fix any bugs surfaced (state broadcast omissions, race conditions, wrong ownership checks, etc.) as you report them.

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
- [ ] **[Human]** Press space repeatedly and confirm the whole cluster halves each time (1 → 2 → 4 → 8), not just one piece.
- [ ] **[Human]** Wait ~12s and confirm pieces remerge.
- [ ] **[Human]** With two tabs open: split, then eat a smaller player's piece with the split fragment. Confirm the ratio rule still applies.

### Phase 5 exit criteria

- [ ] **[Human]** Splitting feels responsive and matches the rules from source plan section 5.

---

## Phase 6 — Bots

Goal: N Python bot clients playing autonomously.

- [ ] **[Agent]** `bots/simple_bot.py` — WebSocket client using the same `join`/`input`/`split` protocol. CLI args for name, server URL, count. On disconnect it either reconnects or exits cleanly, per the exit criterion below.
- [ ] **[Agent]** Decision loop: from the most recent received state, move toward the nearest edible entity (food or a smaller player); flee if a larger player is closer than the nearest edible target. No pathfinding. `input_toward_nearest_food` in `server/demo.py` is the seed of this.
- [ ] **[Agent]** Run 3–5 bots against a local server and confirm the tick loop still holds its rate with that many players in the world.
- [ ] **[Human]** Play against them in a browser tab. Confirm bots don't deadlock, don't spin in place, and don't crash on player disconnect.

### Phase 6 exit criteria

- [ ] **[Human]** World feels alive with bots present. Bots survive server restarts (the client reconnects, or its process exits cleanly).

---

## Phase 7 — Move to the VM, expose externally

Goal: reachable from outside the LAN, independent of the existing SSH forward.

### On the VM

- [ ] **[Human]** Check the VM's VirtualBox adapter mode (Settings → Network). Note whether it's **Bridged** or **NAT** — routing setup below depends on this.
- [ ] **[Both]** Copy the repo to the VM (`git clone` inside the VM is easiest once the VM has internet).
- [ ] **[Agent]** Write a short `scripts/vm_bootstrap.sh` — installs Python 3.13 (or 3.12), `pip`, sets up a venv, `pip install -r requirements.txt`, ufw rule.
- [ ] **[Agent]** Keep the Phase 1 viewer and `client/recordings/` out of the static mount. They are development tooling and this server faces the internet.
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
