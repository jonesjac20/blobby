# Phase 1.5 — Feel and fidelity pass

Work banked during the Phase 1 close-out review. **None of it blocks Phase 2.** It is collected here so it is not lost and not confused with the defects that were fixed at the time.

## Read this first

You are picking up a hobby agar.io clone. The server is Python 3.13 standard library only, the client is vanilla ES modules with no build step, and tests are pytest. Start with [`GUIDEBOOK.md`](GUIDEBOOK.md) in this folder for the phase structure and the divergences from [`agario_build_plan.md`](agario_build_plan.md); this document assumes you have read the Phase 1 section.

Three things to know before you touch anything:

1. **Determinism is load-bearing.** `World` takes a seed, `World.now` is the only clock, and IDs come from the world's own RNG. The recorder and every test depend on a given seed replaying byte-identically. Do not introduce `time.monotonic()`, `random`, or `uuid.uuid4()` into simulation code.
2. **`simulation.step` is dt-invariant** and `tests/test_tick_rate.py` exists to keep it that way: the same sim time must produce the same state at 15, 30 and 60Hz. Position projection is used instead of impulses specifically for this. Any change that makes a per-tick decrement or a dt-dependent integration will fail that suite, and it is telling you the truth.
3. **Sequencing inside a tick matters.** Order is input+move, cluster forces, collisions+clamp, food, cross-player eating, kick decay, remerge, food respawn. Collisions run *before* the eat check, which is why an own-piece pair in open field can never be deep enough to eat — that subtlety already invalidated one scenario and one test.

Run `python -m pytest` (should be all green) and `python -m tools.record --serve` (opens the viewer) before and after every change.

## How to work through this

Items are grouped by area and ordered within each group by value. Each has a concrete acceptance criterion. Several are worth deferring until after Phase 3, and say so — the cluster and kick values are feel parameters, and Phase 3 is the first time anyone can actually see them. Do not tune blind.

Where a number appears below it was measured, not estimated. Re-measure before you change anything; if your number disagrees with the one here, find out why before proceeding.

---

## A. Simulation feel and correctness

### A1. `BASE_SPEED` breaks its own stated invariant for every split fragment

`server/config.py` comments that `BASE_SPEED` is kept "low enough that one tick of travel never exceeds that radius, or fast blobs jump straight over the food they are chasing". That holds only above mass ~37. `speed_for_mass` makes *lighter* pieces faster, and splitting produces lighter pieces, so it fails for exactly the case it was written about.

Solving `BASE_SPEED * (40/m) ** 0.4 / 30 = sqrt(m / pi)` puts break-even at **m ≈ 37.1**, just under `INITIAL_PLAYER_MASS = 40`. At mass 20 (one split from spawn) travel is 4.40 units per tick against a radius of 2.52 — the sampled discs still overlap, so about 15% of swept food is missed. At mass ≤ **~17.2** travel exceeds the diameter and the hops are fully disjoint: food directly on the path is skipped. A mass-10 fragment covers roughly 48% of its swath.

`_eat_food` point-samples the post-move position with no swept test, so small pieces feel arbitrarily bad at collecting.

Options: lower `BASE_SPEED`, floor the falloff below spawn mass, or make food collection a segment-vs-point test. The third is the only one that actually fixes it rather than hiding it.

**Acceptance:** a test that lays food along a piece's path and asserts every pellet is consumed, parametrized at mass 200, 40, 20 and 10. It should fail at mass 10 before your change.

### A2. `SPLIT_KICK_SPEED` is absolute while resting distance grows as `sqrt(mass)`

Total kick displacement is fixed at `120 * 0.5 / 2 = 30` units at every mass. Two halves of mass `m/2` rest `2 * sqrt(m / (2 * pi)) * (1 - OWN_PIECE_OVERLAP)` apart, which reaches 30 units at **m ≈ 1960**. Above that a split produces no visible separation at all — projection re-settles the halves the same tick. Well before that, at mass 500, the 30-unit pop is under 2.5 blob radii and reads as a twitch.

This matters more than it looks: because a predator at the 1.25x ratio moves at only 91.5% of its prey's speed (see A4), splitting to lunge is the *only* way to catch anything. This quietly removes the offensive mechanic from large players.

Scale the kick with `radius_for_mass` so the pop stays proportionate.

**Acceptance:** a test asserting the halves of a mass-2000 blob end further apart than their resting distance. Defer the exact scaling constant until Phase 3.

### A3. Bounds are clamped on centers, not bodies

`_resolve_collisions` clamps `piece.x` and `piece.y` into `[0, WORLD_WIDTH]`. A piece can therefore sit with its center on the wall and a full radius outside the world — 17.8 units for a mass-1000 blob, over half its diameter hanging in the void. The Phase 1 verify box is satisfied on the coordinate but not on the body, and Phase 3 will render blobs poking out of the arena.

Clamping to `[radius, WORLD_WIDTH - radius]` is the fix. Note the interaction with A3's neighbours: a merge-ready cluster crushed into a corner has less room, and `test_own_pieces_never_eat_each_other` deliberately relies on corner crushing, so run it after.

**Acceptance:** for every piece, `radius <= x <= WORLD_WIDTH - radius`, asserted after driving blobs of several masses into all four corners. Existing bounds tests must still pass.

### A4. A predator is always slower than its prey — document the technique

Not a bug; `speed ∝ mass ** -0.4` means a predator satisfying `A.mass > 1.25 * B.mass` moves at most `(1/1.25) ** 0.4 = 91.5%` of its prey's speed, and less at higher ratios. Combined with `EAT_OVERLAP = 0.5`, which requires the prey's center to reach the predator's rim, a competently fleeing prey cannot be caught by chasing at any mass ratio. You must corner it or split into it.

This is agar.io-correct and should not be changed. It is listed here because it *reads* as a bug to anyone testing Phase 4 by walking one blob into another. The Phase 4 checklist has been amended to say so; verify that note survives and is accurate.

**Acceptance:** no code change. Confirm the Phase 4 wording matches measured behaviour.

### A5. The tick loop drifts about 6%

`server/main.py` sleeps a fixed `1/TICK_RATE` *after* the previous tick's work rather than sleeping to a deadline. Measured over 30 seconds: the last line was `tick 840`, i.e. **28.2Hz**, so a line labelled `tick 840` prints at t ≈ 29.8s.

The simulation is unaffected — `World.now` accumulates measured elapsed time, so the 3s split and the 15.5s remerge land at the correct wall-clock moments. It matters in Phase 2, where the tick loop becomes the broadcast clock and the compounding sleep makes the effective broadcast rate lower than advertised.

Sleep to a deadline: track the next tick's target time and sleep the remainder.

**Acceptance:** over a 30s run, tick count is within 1% of `30 * elapsed`. `tests/test_tick_rate.py` must still pass.

### A6. Food collision is O(pieces × FOOD_COUNT) and will bite in Phase 6

With `FOOD_COUNT = 600`, one player at 8 pieces is 4,800 `math.hypot` calls per tick, which is fine. Ten players at 8 pieces is 48,000 distance tests per tick, 1.44M/sec at 30Hz — roughly half a core on food alone, before `_resolve_collisions` (which is `SEPARATION_PASSES * O(bodies²)`, or 4 × 3,160 pairs at 80 bodies).

Phase 6 calls for 3–5 bots plus a human, so the tick budget starts overrunning exactly when bots land, and it will present as "bots feel laggy" rather than as an obvious error. A uniform grid bucketing food by cell makes this near-constant per piece.

Do this before Phase 6, not before Phase 2.

**Acceptance:** a benchmark showing per-tick time roughly flat as player count goes 1 → 10, and identical simulation output for a fixed seed (the grid must not change behaviour).

### A7. Small hardening in the simulation

- **`_remerge_pieces` divides by zero on a zero-mass pair.** `World.spawn_player` accepts an arbitrary mass, so two coincident zero-mass pieces get `engulfment` 1.0 from the massless branch, clear `MERGE_OVERLAP`, and crash on `total = a.mass + b.mass`. `_project_apart` guards the identical case; this does not.
- **`World.spawn_player` does not clamp the spawn point into the world.** A caller passing out-of-bounds coordinates gets a piece that teleports to the wall on the first step. Becomes relevant when Phase 2 picks spawn positions.
- **A player reduced to zero pieces stays in `world.players` forever.** Phase 2 owns the respawn-or-spectate decision; what belongs here is a test that `step` survives a player with an empty piece list, since every phase iterates it.
- **`new_id` truncates to 32 bits.** 8 hex chars means an expected first collision around 77k IDs, and a long-lived server respawning 600 food repeatedly gets there. A food collision is benign (the loop re-rolls) but a `piece_id` collision would make the `eaten` sets delete the wrong piece. Widen it.
- **The eat-ratio boundary flips collision solidity discontinuously.** At `A.mass ≈ 1.25 * B.mass`, one pellet toggles the pair between solid and fully permeable, so two players hovering near the ratio see contact stutter on and off tick to tick. A small hysteresis band would smooth it. Cosmetic; judge it in Phase 4.

**Acceptance:** a test per bullet, each failing before the fix.

---

## B. Viewer polish

All in `client/viewer.js` and `client/render.js`. None of it is urgent; the viewer works and is now accurate. Reassess the whole group at Phase 3, when the decision about the viewer's future gets made — do not polish something that is about to be deleted.

### B1. Food is drawn at a fixed screen size that does not exist in the simulation

`render.js` draws pellets at `Math.max(1.5, 2.4 * Math.min(camera.scale, 2))` pixels. Food has no radius in the simulation at all: `_eat_food` tests distance to the pellet's *center* against the piece radius. At the `food_eating` framing the dot renders about 1.9px against a blob radius of 6.3px, implying roughly 2.4 world units of body that is not there — so the one checklist item about center coverage is judged with visible slop.

**Acceptance:** pellets drawn at a size derived from world units, or visibly marked as a center point (a dot with a crosshair, say).

### B2. The HUD is rebuilt from scratch 60 times a second

`renderHud` allocates four elements per player every animation frame for text that changes at most 30 times a second and usually not at all, destroying text selection and hover state continuously. `colorForId` also re-hashes every ID string per piece per frame.

Related: `tick` calls `draw` unconditionally, so a paused viewer burns a core indefinitely. Skip the redraw when nothing has changed — paused, with no camera movement and no resize.

**Acceptance:** paused, the viewer uses negligible CPU. Player rows are not replaced when their content is unchanged.

### B3. `colorForId` gives near-identical colours to distinct players

`hash % 360` with every scenario constructing its world from the same seed in the same order means the first four players in every scene get the same hues. Measured for `input_direction`: 8, 263, 121, 275, 79, 17, 332, 337 — hues 263 and 275 are 12° apart and 332/337 are 5° apart, so two of the four pairs in the fan-out clip are indistinguishable. In `eat_ratio` the two prey are 263 and 275, i.e. the same colour.

Use golden-ratio hue spacing or an explicit palette.

**Acceptance:** eight players in one scene are all visually distinguishable.

### B4. Camera and overlay toggles persist across scenarios and break their framing

`select` resets the timeline, speed and verified checkbox, but not the follow-cam or overlay checkboxes. Every `expect` string is written for the scenario's fitted `view` rect, so leaving follow-cam on from the demo makes `input_direction` lock onto whichever of eight equal-mass blobs sorts first, with no indication that the camera rather than the simulation is the problem.

**Acceptance:** switching scenarios restores the framing each one was authored for.

### B5. Smaller viewer items

- **`world_bounds` tells the verifier to read HUD coordinates that do not exist.** The `expect` says "the HUD coordinates clamp to 0 and 1200"; the HUD shows a swatch, a name and masses. Either add x/y to the player rows or reword. Note the clamp applies to centers (A3), so a blob in a corner is drawn overhanging the world rectangle — which looks like the opposite of what the verifier is asked to confirm.
- **Only the first event of a frame is toasted.** `_detect_events` can emit "lost a piece" and "ELIMINATED" in the same frame; the elimination toast never appears in `eat_ratio` or `solid_collision`.
- **The keydown guard misses `<select>` and `<button>`.** Space with the speed dropdown focused toggles playback instead of opening the dropdown, and after clicking the timeline slider Space stops working.
- **Concurrent `select()` calls can pair one scenario's metadata with another's frames.** Two awaits with no request token: click B then A while B's 287KB file is still parsing and the slower response can land last, leaving the header describing one scenario while the cursor holds another. The verified checkbox then records against a scenario nobody watched. Guard with a monotonic request ID.
- **Verification ticks survive re-recording.** `state.verified` is keyed on scenario ID in `localStorage`, so editing the simulation and re-running the recorder leaves every checkmark in place and the footer still reading "17 / 17 verified". Because `client/recordings/` is gitignored, `git status` shows nothing either. Key the stored flag on a content hash written into `index.json` at record time.

**Acceptance:** one test or manual reproduction per bullet.

---

## C. Recorder and scenarios

### C1. `tools/scenarios.py` structure will not scale

Roughly 600 lines, with three specific problems:

- **Split-family duplication.** `_split_halves_and_kick`, `_kick_decay`, `_remerge` and `_split_cohesion` are the same five statements differing only in an x-offset and durations. They collapse into one parameterised builder with four rows of data.
- **Metadata lives ~200 lines from the setup it describes.** A builder is defined around line 340; its `Scenario(...)` entry is around line 550. Adding a scenario means two edits in two distant places, with nothing checking that every builder is referenced or that IDs are unique. A decorator registry — `@scenario(id=..., title=..., checklist=..., expect=..., view=..., speed=...)` directly above each builder — puts the claim next to the code meant to demonstrate it. That exact decoupling is what let `own_pieces_no_eat` carry a false claim for as long as it did.
- **`BY_ID` is built with no uniqueness check.** A duplicated ID silently shadows, writes one file for two index entries, and gives the viewer two rows loading the same recording under different titles.

**Acceptance:** adding a scenario is a single edit in one place; a test asserts every registered builder is reachable and every ID unique.

### C2. Constants are inlined into `expect` prose inconsistently

Some scenarios interpolate `SPLIT_KICK_SPEED`, `SPLIT_KICK_DECAY_SECONDS` and `FOOD_COUNT` via f-strings. Others hardcode: `world_bounds` says "1200", `remerge` says "t=13s, exactly 12s later", `exponential_split` says "8 piece cap". Change `REMERGE_SECONDS` to 10 and the `remerge` expectation tells the verifier to watch the wrong moment.

The staged coordinates have the same problem — literals like `320`/`880` and `380`/`580`/`820` silently assume a 1200×1200 world. Derive them from `WORLD_WIDTH`/`CENTER` the way `_view_around` already does.

**Acceptance:** no number in any `expect` string or staged coordinate that also exists in `server/config.py` appears as a literal. Changing a config constant and re-recording produces coherent prose.

### C3. Scenarios do not assert their own claims

The biggest gap in this group. Each scenario is a scripted setup whose correctness nobody checks; a clip that trivially passes is worse than no clip, which is exactly what happened with `own_pieces_no_eat`. Each is one assertion against the recorded frames:

- `eat_ratio` ends with three surviving players at masses `[124, 100, 226]`.
- `exponential_split` shows piece counts 1 → 2 → 4 → 8 → 8 with total mass 280 throughout.
- `own_pieces_no_eat` reaches `engulfment >= EAT_OVERLAP` in at least 90% of frames — the regression guard for the staging that was just fixed.
- `kick_decay` shows a monotonically decreasing velocity reaching zero.
- `split_refused_small` and `split_refused_max` show the refused blob unchanged *and* the control blob splitting.

Around 60 lines turns 17 eyeball checks into a regression suite.

**Acceptance:** a test module asserting the claim of every scenario, run as part of `pytest`.

### C4. Two scenarios still overstate what they show

- **`kick_decay`'s expectation is unreachable by one tick.** It says the arrow "starts at 120", but `_decay_split_kicks` runs at the end of `step`, so the first captured frame after the split reads `120 * (1 - 0.033/0.5) = 112.0`. A verifier at 0.25x watching for 120 will not find it.
- **`demo` is not the run it claims to be.** Its docstring says "the same scenario server/main.py prints", and its checklist points at the guidebook's "How to verify". Three divergences: the harness seeds from the wall clock while the recording seeds from 0; the harness steps with measured elapsed time while the recording uses a fixed 1/30; and the harness splits along A's live food-seeking input while the recording hardcodes `(1, 0)`. Ticking this box does not verify what a human sees when they run the harness.
- **`split_refused_max` has no positive control.** Eight pieces, `try_split` breaks immediately, nothing happens — and a refusal is visually indistinguishable from `try_split` being broken for all inputs. The `SPLIT REFUSED` toast is not independent evidence; it is derived from the same return value. Add a seven-piece player to the scene that splits successfully, the way `split_refused_small` already does.
- **Both threshold scenarios bracket the boundary without pinning it.** `eat_ratio` uses 1.24x and 1.26x; `split_refused_small` uses `MIN_SPLIT_MASS ± 1`. Flipping `>` to `>=` in either comparison passes both scenarios unchanged. The unit tests do cover the exact boundaries, so this is about the clips not overclaiming.

**Acceptance:** each `expect` string is true of the recorded frames, verified by C3's assertions.

### C5. Smaller recorder items

- **`_detect_events` hardcodes `1.5` as the "bigger than one pellet" threshold**, standing in for `FOOD_MASS`, and assumes one pellet per tick. A large grazer can absorb two in a tick and produce a false "ATE a piece (+2)". It does not fire in the current recordings — I checked — so this is latent, one config change away. Use `FOOD_MASS * 1.5` at minimum; better, have the simulation report kills rather than inferring them from mass deltas.
- **`debug.age` is recorded in every frame and read by nothing.** Dead payload in all 17 files.
- **The per-frame `debug` block is roughly half the file size.** `input_direction.json` is 275KB with zero food, of which the debug block is about 46%, paid even with overlays off. Consider omitting it unless a `--debug` flag is passed. Note the food delta encoding is excellent by contrast — `demo.json` is 287KB where a naive full food list would be ~9.7MB.
- **Notes land one tick late.** `rec.note()` between two `run()` calls flushes on the first capture after a step, so the demo's split logs at t=3.0333 and `split_refused_small`'s notes at t≈1.033 against an `expect` that says t=1s.
- **Each recording duplicates four metadata fields the viewer reads from `index.json` instead.** Only `speed` is read from the recording, and now only as a fallback.
- **`write_all` never prunes orphans.** Renaming a scenario leaves the old JSON in a gitignored directory where nothing will surface it.
- **Two players are distinguished by a trailing space in their name** (`"100 "`), invisible in the HUD and broken the moment anything trims names.
- **`allow_reuse_address` is set for the wrong platform's reason.** The TIME_WAIT rationale in the comment is POSIX; on Windows `SO_REUSEADDR` lets two sockets bind the same port simultaneously, so a second `--serve` can bind 8080 alongside the first with requests served by an arbitrary one. It also mutates the `socketserver.TCPServer` class attribute process-wide rather than a subclass, and `serve()` has no handler for `OSError`, so a genuine conflict is a raw traceback.
- **The static server exposes the whole repo**, `.git` included, at `http://127.0.0.1:8080/.git/config`. Localhost-bound, so a note rather than a vulnerability — but scope the handler to `client/`.

---

## D. Remaining test gaps

The suite is strong on the spec'd behaviours. The gaps are compositional and adversarial. Roughly in value order:

- **Mass conservation across a compound tick.** One tick containing a cross-player eat, an own-piece remerge and a food pickup simultaneously; assert total mass equals before plus `FOOD_MASS * eaten`. The chain-eat path (a piece that eats and is itself eaten in the same tick) is the risky part — it is correct today, and nothing pins it.
- **Three-player eat ordering.** P0 eats P1's piece, grows past `1.25 * P2`, and eats P2's piece in the same tick. Assert mass lands exactly once and the result does not depend on `world.players` insertion order — or, if it does, pin the documented order.
- **Two pieces contending for one pellet.** Assert exactly one gains `FOOD_MASS` and the pellet is removed once. The `if food.id in eaten: continue` guard is currently unexecuted by any test, so a double-credit or a `KeyError` on the delete would go unnoticed.
- **`try_split` piece *selection*, not just the count.** At 3, 5, 6 and 7 pieces of splittable mass against `MAX_PIECES = 8`, assert *which* pieces split — largest first, per the docstring. The existing test checks the resulting count only.
- **`try_split` with unnormalized `last_input`.** Assert the kick magnitude is exactly `SPLIT_KICK_SPEED`, not scaled by the input's length.
- **An 8-piece all-merge-ready cluster collapses to one.** Exercises the projection skip cascading through the `while merged` loop, the least-tested control flow in `simulation.py`.
- **`World.remove_player` has no test at all**, and it is an explicit Phase 1 checklist item. Also untested: the `mass <= 0` branch of `speed_for_mass` and the massless branch of `engulfment`.
- **The food radius boundary is bracketed far too loosely.** Tests place food at `radius/2` and `radius*2`; the rule is "circle covers the center", so the threshold is exactly `radius`. An implementation using `radius/2` or `1.5*radius` passes both. Add a `radius*0.99` / `radius*1.01` pair.
- **No JavaScript is tested at all.** `recording.js` advertises in its own header that it has no DOM access "so it can be exercised outside a browser" — an affordance nothing uses. Two things are worth pinning: the delta-encoder round trip (`Recorder.capture`'s encoding and `RecordingCursor.foodAt`'s decoding share an undocumented contract that removals are applied before additions, with no test on either side), and that `render.js`'s `radiusForMass` still agrees with `simulation.radius_for_mass`. A Python test that replays the encoder's own output per frame against `world.food` covers the first without needing a JS test runner.
- **Loose tolerances worth tightening.** `CLUSTER_TOLERANCE` in `test_tick_rate.py` is justified in world units ("half a world unit") and then applied to a value in *seconds* — half a second is 15 ticks and half the entire merge-pull budget, so that invariance assertion is far weaker than it reads; the real spread is tiny, so 0.02s is free. `test_piece_moves_in_direction_of_input` checks signs only, so a diagonal being √2 too fast would pass. `test_speed_for_mass_decreases_as_mass_grows` is three-point monotonicity, which a step function satisfies. `test_every_piece_of_a_full_cluster_touches_a_neighbour` only requires one neighbour each, so two disjoint clusters of four pass. `test_split_pieces_remerge_after_the_full_cycle` allows a full second of slack, and no test anywhere pins the absolute merge time.

### Two structural notes on the test suite

- **`advance()` overshoots by up to one tick.** It loops `while world.now < target` over accumulated floats, so `advance(world, 0.5, 1/30)` runs 16 ticks, not 15 (fifteen additions of 1/30 sum to 0.49999999999999994). At least one assertion is only strictly true because of that extra tick. It passes today and `_kick_active_during_tick` is deliberately designed for it, but the dependence on float accumulation direction is unstated. `advance`'s return value is used by nothing.
- **`from conftest import ...` is fragile.** It works only because there is no `tests/__init__.py` and no pytest config file at all, so prepend import mode puts `tests/` on `sys.path`. Adding `tests/__init__.py` or switching to `importmode=importlib` breaks collection of the entire suite. Worth either adding a `pyproject.toml`/`pytest.ini` that pins the working configuration explicitly, or importing helpers through a package path.

---

## E. Housekeeping

- **`requirements.txt` is unpinned and mixes concerns.** `pytest` alone is correct for Phase 1 and nothing is missing, but Phase 7's `vm_bootstrap.sh` will `pip install -r requirements.txt` on the game server and drag pytest onto it. Pin versions, and split a `requirements-dev.txt` when `aiohttp` lands in Phase 2.
- **`test_tick_rate.py` is misnamed and disconnected from `TICK_RATE`.** It tests dt-invariance of `simulation.step`, not tick rate, and its `TICK_RATES` list hardcodes `[1/15, 1/30, 1/60]` without referencing `config.TICK_RATE` — so changing the tick rate to 60 breaks no test. Rename to something like `test_dt_invariance.py` and add one test that the configured rate is what the loop actually runs at.
- **`World.food_target` is passed by nothing outside the recorder**, while the `no_food` fixture monkeypatches `server.world.FOOD_COUNT` to get the same result. The fixture's docstring is right that patching `server.config` would not work — it just picked the harder lever. `World(food_target=0)` needs no patching.
- **Config values are bound at import time.** `server.world` and `server.simulation` import constants by value, so patching `server.config.X` has no effect; only `server.world.X` works. One fixture comment implies otherwise. Worth a note in `config.py` since it will catch someone out.
