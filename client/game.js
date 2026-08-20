/**
 * Live game client. Drives render.js from a /ws connection.
 *
 * Connects on load and does not send join until Play. Food is held separately
 * and spliced onto a copy of the interpolated snapshot at draw time. Own pieces
 * are predicted from the latest snapshot and last input; everyone else is
 * interpolated over at most one tickRate interval (not the measured hitch gap),
 * then held — blending over a late gap left bots stuck on the previous
 * snapshot, and extrapolating past the latest one drew ghost shadows.
 * Spacebar sends split while playing; held-key auto-repeat is
 * ignored. Wheel zooms the follow-cam while playing or spectating.
 */

import {
  clampBodyPosition,
  createCamera,
  drawWorld,
  fitCamera,
  followCamera,
  interpolateStates,
  playerCentroid,
  playerMass,
  playerRemergeIn,
  radiusForMass,
  resizeCanvas,
  screenToWorld,
  speedForMass,
} from "./render.js?v=food-delta-2";

const FOLLOW_SMOOTHING = 6;
// Floor on the stop radius, in screen pixels. The real stop distance is one
// tick of travel (see maybeSendInput): 8px is smaller than that at play zoom,
// so a parked cursor used to be jumped over and the unit vector reversed.
const DEADZONE_PX = 8;
// Pointer must move this far from the park point before we leave rest. Blob
// wander from prediction / snapshot correction must not re-aim a still mouse.
const REST_RESUME_PX = 12;
const MAX_DT = 0.1;
// Cap self dead-reckon to one nominal tick. Predicting out to MAX_TICK_SECONDS
// (0.25) on a hitchy prod lobby coasted a second body ahead of the snapshot.
const PREDICT_MAX_DT_TICKS = 1.25;
// A gap this many ticks long is a hitch: drop leftover dead-reckon error.
const HITCH_TICKS = 1.75;
const PREDICT_CORRECTION = 18;
const INPUT_EPSILON = 1e-3;
const ZOOM_MIN = 0.5;
const ZOOM_MAX = 2.5;
const ZOOM_SENSITIVITY = 0.0015;
// Doubling from half a second to eight, so a server that comes straight back up
// is caught almost immediately while a tab left open overnight is not hammering
// a machine that is off.
const RECONNECT_BASE_MS = 500;
const RECONNECT_MAX_MS = 8000;
// Filled from `welcome` / `state`. These are not client constants —
// changing them in config.py used to leave this drawing a 1200 square around
// a correctly-clamped world, interpolating at 30Hz, and zooming as if spawn
// mass were still 40.
const world = { width: 0, height: 0 };
let tickSeconds = 1 / 30;
let initialPlayerMass = 50;
let baseSpeed = 200;
let speedFalloff = 0.25;
let speedFloorFraction = 0.25;

const canvas = document.getElementById("game-canvas");
const hud = document.getElementById("hud");
const massEl = document.getElementById("mass");
const bestMassEl = document.getElementById("best-mass");
const remergeEl = document.getElementById("remerge");
const remergeTimeEl = document.getElementById("remerge-time");
const protectedEl = document.getElementById("protected");
const menu = document.getElementById("menu");
const gameOver = document.getElementById("game-over");
const joinForm = document.getElementById("join-form");
const nameInput = document.getElementById("name");
const colorInput = document.getElementById("color");
const playBtn = document.getElementById("play");
const spectateBtn = document.getElementById("spectate");
const customizeBtn = document.getElementById("customize");
const respawnBtn = document.getElementById("respawn");
const peakMassEl = document.getElementById("peak-mass");
const survivalEl = document.getElementById("survival");
const offline = document.getElementById("offline");
const offlineTitle = document.getElementById("offline-title");
const offlineStatus = document.getElementById("offline-status");
const retryBtn = document.getElementById("retry");

canvas.tabIndex = 0;

/** @typedef {"menu" | "playing" | "spectating" | "gameover"} Mode */

/** @type {Mode} */
let mode = "menu";
/** @type {WebSocket | null} */
let socket = null;
let pendingJoin = false;
let everConnected = false;
let reconnectAttempt = 0;
let reconnectTimer = null;
/** @type {string | null} */
let selfId = null;
/** @type {string | null} */
let followId = null;
let snapCamera = false;

let previousState = null;
let nextState = null;
let nextArrivedAt = 0;
let previousArrivedAt = 0;
let latestFood = [];
/** Recycled pellet objects so a food delta does not allocate 1600 points. */
const foodPool = [];
/** Deltas are ignored until a full `food` snapshot lands (same as the bots). */
let foodSnapshotReady = false;

/** @type {{ x: number, y: number } | null} */
let pointer = null;
/** @type {{ x: number, y: number } | null} */
let restPointer = null;
let lastSentDx = 0;
let lastSentDy = 0;
/** @type {number | null} */
let lastReportedDx = null;
/** @type {number | null} */
let lastReportedDy = null;
let lastTimestamp = 0;
/** @type {Map<string, { x: number, y: number }>} */
let predictError = new Map();
/** @type {Map<string, { x: number, y: number }> | null} */
let lastPredictedPieces = null;
/** @type {Set<string> | null} */
let lastPredictedIds = null;

const camera = createCamera();
let zoomFactor = 1;
/** @type {{ snapshot: object, viewport: { width: number, height: number } } | null} */
let lastDraw = null;

function wsUrl() {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${location.host}/ws`;
}

function socketIsOpen() {
  return socket !== null && socket.readyState === WebSocket.OPEN;
}

function sendJson(payload) {
  if (!socketIsOpen()) return;
  socket.send(JSON.stringify(payload));
}

function applyWorld(msg) {
  const rect = msg.world;
  if (!rect || typeof rect !== "object") return;
  const { width, height } = rect;
  if (typeof width !== "number" || typeof height !== "number") return;
  if (!Number.isFinite(width) || !Number.isFinite(height)) return;
  if (width <= 0 || height <= 0) return;
  world.width = width;
  world.height = height;
}

function applyConfig(msg) {
  applyWorld(msg);
  const { tickRate, initialPlayerMass: spawnMass } = msg;
  if (typeof tickRate === "number" && Number.isFinite(tickRate) && tickRate > 0) {
    tickSeconds = 1 / tickRate;
  }
  if (typeof spawnMass === "number" && Number.isFinite(spawnMass) && spawnMass > 0) {
    initialPlayerMass = spawnMass;
  }
  const { baseSpeed: speed, speedFalloff: falloff, speedFloorFraction: floor } = msg;
  if (typeof speed === "number" && Number.isFinite(speed) && speed > 0) {
    baseSpeed = speed;
  }
  if (typeof falloff === "number" && Number.isFinite(falloff) && falloff >= 0) {
    speedFalloff = falloff;
  }
  if (typeof floor === "number" && Number.isFinite(floor) && floor >= 0) {
    speedFloorFraction = floor;
  }
}

function resetPrediction() {
  lastSentDx = 0;
  lastSentDy = 0;
  lastReportedDx = null;
  lastReportedDy = null;
  restPointer = null;
  predictError = new Map();
  lastPredictedPieces = null;
  lastPredictedIds = null;
  lastDraw = null;
}

function worldRect() {
  return [0, 0, world.width, world.height];
}

function syncJoinButtons() {
  const open = socketIsOpen();
  playBtn.disabled = !open;
  respawnBtn.disabled = !open;
}

function blurOverlayButtons() {
  const active = document.activeElement;
  if (!(active instanceof HTMLElement)) return;
  if (menu.contains(active) || gameOver.contains(active)) active.blur();
}

function focusCanvas() {
  blurOverlayButtons();
  canvas.focus();
}

function showOffline() {
  offlineTitle.textContent = everConnected ? "Connection lost" : "Connecting";
  offlineStatus.textContent = everConnected
    ? `Reconnecting… (attempt ${reconnectAttempt})`
    : "Reaching the server…";
  offline.hidden = false;
  blurOverlayButtons();
}

function scheduleReconnect() {
  if (reconnectTimer !== null) return;
  const delay = Math.min(
    RECONNECT_BASE_MS * 2 ** (reconnectAttempt - 1),
    RECONNECT_MAX_MS
  );
  reconnectTimer = window.setTimeout(() => {
    reconnectTimer = null;
    connect();
  }, delay);
}

function connect() {
  socket = new WebSocket(wsUrl());
  socket.addEventListener("open", () => {
    everConnected = true;
    reconnectAttempt = 0;
    offline.hidden = true;
    // The previous socket's snapshots describe a world this one has not been
    // told about yet, and its player is gone. Interpolating across the gap
    // would slide every blob from where it was to where it now is.
    previousState = null;
    nextState = null;
    nextArrivedAt = 0;
    previousArrivedAt = 0;
    latestFood = [];
    foodSnapshotReady = false;
    resetPrediction();
    syncJoinButtons();
    // The server removes a socket's player when it closes, so the life that was
    // in progress cannot be resumed — only replaced. Dropping to the menu says
    // so, where silently rejoining would look like a teleport back to spawn
    // mass. A spectator lost nothing and simply carries on.
    if (mode === "playing" || mode === "gameover") {
      showMenu();
    } else if (pendingJoin) {
      pendingJoin = false;
      sendJoin();
    }
  });
  socket.addEventListener("message", onMessage);
  socket.addEventListener("close", () => {
    socket = null;
    pendingJoin = false;
    reconnectAttempt += 1;
    syncJoinButtons();
    showOffline();
    scheduleReconnect();
  });
}

function applyFullFood(pairs) {
  const n = pairs.length;
  while (latestFood.length < n) {
    const recycled = foodPool.pop();
    latestFood.push(recycled || { x: 0, y: 0 });
  }
  while (latestFood.length > n) {
    foodPool.push(latestFood.pop());
  }
  for (let i = 0; i < n; i++) {
    latestFood[i].x = pairs[i][0];
    latestFood[i].y = pairs[i][1];
  }
}

function applyFoodDelta(add, remove) {
  for (const pair of remove || []) {
    const x = pair[0];
    const y = pair[1];
    const index = latestFood.findIndex((point) => point.x === x && point.y === y);
    if (index >= 0) foodPool.push(latestFood.splice(index, 1)[0]);
  }
  for (const pair of add || []) {
    const point = foodPool.pop() || { x: 0, y: 0 };
    point.x = pair[0];
    point.y = pair[1];
    latestFood.push(point);
  }
}

function onMessage(event) {
  let msg;
  try {
    msg = JSON.parse(event.data);
  } catch {
    return;
  }
  if (!msg || typeof msg !== "object") return;

  switch (msg.type) {
    case "welcome":
      applyConfig(msg);
      selfId = msg.id;
      followId = null;
      snapCamera = true;
      resetPrediction();
      break;
    case "food":
      if (Array.isArray(msg.food)) {
        applyFullFood(msg.food);
        foodSnapshotReady = true;
      } else if (foodSnapshotReady) {
        // Pre-food_delta servers reused type `food` for add/remove.
        applyFoodDelta(msg.add, msg.remove);
      }
      break;
    case "food_delta":
      if (foodSnapshotReady) applyFoodDelta(msg.add, msg.remove);
      break;
    case "state":
      applyConfig(msg);
      {
        const arrived = performance.now();
        const gap =
          nextArrivedAt > 0 ? (arrived - nextArrivedAt) / 1000 : tickSeconds;
        if (gap > tickSeconds * HITCH_TICKS) {
          // The snapshot already includes the hitch-sized dt. Leftover
          // dead-reckon error would draw a second copy of your blob offset
          // from the interpolated one; circling the mouse then orbits both
          // around the cursor.
          predictError = new Map();
          lastPredictedPieces = null;
          lastPredictedIds = null;
        } else {
          reconcilePrediction(msg);
        }
        previousState = nextState;
        previousArrivedAt = nextArrivedAt > 0 ? nextArrivedAt : arrived;
        nextState = msg;
        nextArrivedAt = arrived;
      }
      if (mode === "playing" && selfId) {
        const mine = msg.players.find((player) => player.id === selfId);
        if (mine && mine.pieces.length) {
          if (followId !== selfId) snapCamera = true;
          followId = selfId;
        }
      }
      break;
    case "game_over":
      if (mode !== "playing") break;
      mode = "gameover";
      selfId = null;
      followId = null;
      resetPrediction();
      hud.hidden = true;
      peakMassEl.textContent = String(Math.round(msg.peak_mass));
      survivalEl.textContent = Number(msg.survival_seconds).toFixed(1);
      gameOver.hidden = false;
      blurOverlayButtons();
      break;
    default:
      break;
  }
}

function sendJoin() {
  if (!socketIsOpen()) {
    pendingJoin = true;
    return;
  }
  sendJson({
    type: "join",
    name: nameInput.value,
    color: colorInput.value,
  });
  enterPlaying();
}

function enterPlaying() {
  mode = "playing";
  followId = null;
  zoomFactor = 1;
  resetPrediction();
  menu.hidden = true;
  gameOver.hidden = true;
  hud.hidden = true;
  focusCanvas();
}

function showMenu() {
  mode = "menu";
  pendingJoin = false;
  selfId = null;
  followId = null;
  resetPrediction();
  hud.hidden = true;
  gameOver.hidden = true;
  menu.hidden = false;
  blurOverlayButtons();
}

function enterSpectate() {
  pendingJoin = false;
  mode = "spectating";
  selfId = null;
  followId = null;
  zoomFactor = 1;
  hud.hidden = true;
  menu.hidden = true;
  gameOver.hidden = true;
  focusCanvas();
}

function livingPlayers(snapshot) {
  return snapshot.players.filter((player) => player.pieces.length);
}

function cycleFollow(snapshot) {
  const living = livingPlayers(snapshot);
  if (!living.length) {
    followId = null;
    return;
  }
  // Living players plus a trailing null slot for the map-fit camera — the
  // same framing Spectate starts on. A vanished follow id is not in the
  // ring, so indexOf is -1 and the next slot is the first living player.
  const ring = [...living.map((player) => player.id), null];
  const index = ring.indexOf(followId);
  followId = ring[(index + 1) % ring.length];
  snapCamera = true;
}

function topmostPlayerAt(snapshot, viewport, sx, sy) {
  const world = screenToWorld(camera, viewport, sx, sy);
  const discs = [];
  for (const player of snapshot.players) {
    for (const piece of player.pieces) discs.push({ player, piece });
  }
  // drawPieces paints largest first, smallest last — hit the topmost disc.
  discs.sort((a, b) => a.piece.mass - b.piece.mass);
  for (const { player, piece } of discs) {
    const radius = radiusForMass(piece.mass);
    if (Math.hypot(world.x - piece.x, world.y - piece.y) <= radius) {
      return player.id;
    }
  }
  return null;
}

function pointerOnCanvas(event) {
  const rect = canvas.getBoundingClientRect();
  return { x: event.clientX - rect.left, y: event.clientY - rect.top };
}

function maybeSendInput(snapshot, viewport) {
  if (mode !== "playing" || !socketIsOpen() || pointer === null || !selfId) return;
  const player = snapshot.players.find((candidate) => candidate.id === selfId);
  if (!player || !player.pieces.length) return;

  const centroid = playerCentroid(player);
  if (!centroid) return;

  // Aim in world space. The server treats (dx, dy) as a direction at full
  // speed_for_mass, never a throttle, so a parked cursor is only reachable by
  // sending (0, 0) before a tick of travel would carry the body through it.
  const cursor = screenToWorld(camera, viewport, pointer.x, pointer.y);
  const wx = cursor.x - centroid.x;
  const wy = cursor.y - centroid.y;
  const worldDist = Math.hypot(wx, wy);
  const speed = speedForMass(playerMass(player), speedKnobs());
  const scale = camera.scale > 0 ? camera.scale : 1;
  const stopWorld = Math.max(speed * tickSeconds, DEADZONE_PX / scale);

  const atRest = lastSentDx === 0 && lastSentDy === 0;
  const pointerShift = restPointer
    ? Math.hypot(pointer.x - restPointer.x, pointer.y - restPointer.y)
    : Infinity;

  let dx = 0;
  let dy = 0;
  if (atRest && pointerShift < REST_RESUME_PX) {
    // Pointer has not moved. Snapshot correction and prediction error can
    // shove the drawn centroid around a still cursor; that must not flip the
    // unit vector, or the blob orbits the mouse — worse over the net, where
    // the body coasts on the last input for half an RTT after we stop.
  } else if (worldDist >= stopWorld) {
    dx = wx / worldDist;
    dy = wy / worldDist;
    restPointer = null;
  } else {
    restPointer = { x: pointer.x, y: pointer.y };
  }

  lastSentDx = dx;
  lastSentDy = dy;
  if (
    lastReportedDx !== null &&
    Math.hypot(dx - lastReportedDx, dy - lastReportedDy) < INPUT_EPSILON
  ) {
    return;
  }
  lastReportedDx = dx;
  lastReportedDy = dy;
  sendJson({ type: "input", dx, dy });
}

function speedKnobs() {
  return { baseSpeed, initialPlayerMass, speedFalloff, speedFloorFraction };
}

function pieceIds(player) {
  return new Set(player.pieces.map((piece) => piece.piece_id));
}

function setsEqual(a, b) {
  if (a === b) return true;
  if (!a || !b || a.size !== b.size) return false;
  for (const value of a) {
    if (!b.has(value)) return false;
  }
  return true;
}

function reconcilePrediction(msg) {
  if (mode !== "playing" || !selfId || !lastPredictedPieces) {
    predictError = new Map();
    return;
  }
  const mine = msg.players.find((player) => player.id === selfId);
  if (!mine || !mine.pieces.length) {
    predictError = new Map();
    lastPredictedPieces = null;
    lastPredictedIds = null;
    return;
  }
  const ids = pieceIds(mine);
  if (!setsEqual(ids, lastPredictedIds)) {
    // Split, merge, or eat: kick velocity is not on the wire, so snap.
    predictError = new Map();
    lastPredictedIds = ids;
    return;
  }
  const snapDist = baseSpeed * tickSeconds * PREDICT_MAX_DT_TICKS;
  const nextError = new Map();
  for (const piece of mine.pieces) {
    const drawn = lastPredictedPieces.get(piece.piece_id);
    if (!drawn) continue;
    const dx = drawn.x - piece.x;
    const dy = drawn.y - piece.y;
    if (Math.hypot(dx, dy) > snapDist) {
      // Too large to be RTT — leftover hitch prediction. Snap rather than
      // holding an offset disc next to the snapshot body.
      predictError = new Map();
      lastPredictedIds = ids;
      return;
    }
    nextError.set(piece.piece_id, { x: dx, y: dy });
  }
  predictError = nextError;
  lastPredictedIds = ids;
}

function predictSelf(interpolated, now, frameDt) {
  if (mode !== "playing" || !selfId || !nextState) {
    lastPredictedPieces = null;
    lastPredictedIds = null;
    return interpolated;
  }
  const source = nextState.players.find((player) => player.id === selfId);
  if (!source || !source.pieces.length) {
    lastPredictedPieces = null;
    lastPredictedIds = null;
    return interpolated;
  }

  const predictHorizon = tickSeconds * PREDICT_MAX_DT_TICKS;
  const predictDt = Math.min(Math.max((now - nextArrivedAt) / 1000, 0), predictHorizon);
  const decay = frameDt > 0 ? Math.exp(-PREDICT_CORRECTION * frameDt) : 1;
  const knobs = speedKnobs();
  // One cluster velocity, not per-piece. Different masses otherwise drift
  // apart along the aim vector and read as two halves orbiting the cursor.
  const clusterSpeed = speedForMass(playerMass(source), knobs);
  const drawn = new Map();
  const cursor =
    pointer && lastDraw
      ? screenToWorld(camera, lastDraw.viewport, pointer.x, pointer.y)
      : null;

  const travelX = lastSentDx * clusterSpeed * predictDt;
  const travelY = lastSentDy * clusterSpeed * predictDt;
  const planned = source.pieces.map((piece) => {
    let err = predictError.get(piece.piece_id) || { x: 0, y: 0 };
    err = { x: err.x * decay, y: err.y * decay };
    predictError.set(piece.piece_id, err);
    return {
      piece,
      err,
      travelX,
      travelY,
    };
  });

  // Dead-reckoning is full speed along last input, and over the net predictDt
  // can be several ticks. Clamp the cluster so its centroid cannot coast
  // through the cursor — that overshoot is what flipped the aim vector.
  if (cursor && (lastSentDx !== 0 || lastSentDy !== 0)) {
    let fromX = 0;
    let fromY = 0;
    let toX = 0;
    let toY = 0;
    let total = 0;
    for (const row of planned) {
      const mass = row.piece.mass;
      total += mass;
      const sx = row.piece.x + row.err.x;
      const sy = row.piece.y + row.err.y;
      fromX += sx * mass;
      fromY += sy * mass;
      toX += (sx + row.travelX) * mass;
      toY += (sy + row.travelY) * mass;
    }
    if (total > 0) {
      fromX /= total;
      fromY /= total;
      toX /= total;
      toY /= total;
      const movX = toX - fromX;
      const movY = toY - fromY;
      const toCx = cursor.x - fromX;
      const toCy = cursor.y - fromY;
      const movLen = Math.hypot(movX, movY);
      const aimLen = Math.hypot(toCx, toCy);
      if (
        movLen > aimLen &&
        movLen > 1e-9 &&
        movX * toCx + movY * toCy > 0
      ) {
        const scale = aimLen / movLen;
        for (const row of planned) {
          row.travelX *= scale;
          row.travelY *= scale;
        }
      }
    }
  }

  const pieces = planned.map((row) => {
    const rawX = row.piece.x + row.travelX + row.err.x;
    const rawY = row.piece.y + row.travelY + row.err.y;
    const clamped = clampBodyPosition(rawX, rawY, row.piece.mass, world);
    drawn.set(row.piece.piece_id, clamped);
    return { ...row.piece, x: clamped.x, y: clamped.y };
  });

  lastPredictedPieces = drawn;
  lastPredictedIds = new Set(drawn.keys());

  const predicted = { ...source, pieces };
  // Always strip then overlay. Concat-if-missing left the interpolated self
  // in place whenever ids failed to match, which is two discs of you.
  const players = interpolated.players
    .filter((player) => player.id !== selfId)
    .concat(predicted);
  return { ...interpolated, players };
}

function followedPlayer(snapshot) {
  if (!followId) return null;
  const player = snapshot.players.find((candidate) => candidate.id === followId);
  if (!player || !player.pieces.length) return null;
  return player;
}

function tick() {
  requestAnimationFrame(tick);
  // Same clock as nextArrivedAt. Mixing rAF timestamps with performance.now()
  // made alpha hitch even when snapshots were on time.
  const now = performance.now();
  const dt = lastTimestamp ? Math.min((now - lastTimestamp) / 1000, MAX_DT) : 0;
  lastTimestamp = now;

  const { ctx, viewport } = resizeCanvas(canvas);
  if (!nextState) {
    ctx.clearRect(0, 0, viewport.width, viewport.height);
    return;
  }

  const elapsed = Math.max((now - nextArrivedAt) / 1000, 0);
  // Blend over one nominal tick, never over a hitch-sized arrival gap.
  // Using the measured gap after a late snapshot kept alpha near 0 for
  // hundreds of ms (bots frozen on the previous frame), then extrapolated
  // past the new one — the shadow trail on a hitchy prod lobby.
  const alpha = Math.min(elapsed / tickSeconds, 1);
  const interpolated = interpolateStates(previousState, nextState, alpha);
  const withFood = { ...interpolated, food: latestFood };
  const aimSource = lastDraw ? lastDraw.snapshot : withFood;
  const aimViewport = lastDraw ? lastDraw.viewport : viewport;
  maybeSendInput(aimSource, aimViewport);
  const snapshot = predictSelf(withFood, now, dt);

  if (mode === "spectating" && followId && !followedPlayer(snapshot)) {
    cycleFollow(snapshot);
  }

  const followed = followedPlayer(snapshot);
  const canFollow =
    followed &&
    ((mode === "playing" && followId === selfId) || mode === "spectating");

  if (canFollow) {
    const options = snapCamera
      ? { smoothing: 0, dt: 0, referenceMass: initialPlayerMass, zoomFactor }
      : { smoothing: FOLLOW_SMOOTHING, dt, referenceMass: initialPlayerMass, zoomFactor };
    followCamera(camera, snapshot, followId, viewport, options);
    snapCamera = false;
  } else {
    fitCamera(camera, worldRect(), viewport);
  }

  if (mode === "playing" && followed && followId === selfId) {
    hud.hidden = false;
    massEl.textContent = String(Math.round(playerMass(followed)));
    const best = Number(followed.peak_mass);
    bestMassEl.textContent = String(
      Math.round(Number.isFinite(best) ? Math.max(best, playerMass(followed)) : playerMass(followed)),
    );
    const wait = playerRemergeIn(followed);
    remergeEl.hidden = wait < 0.05;
    if (wait >= 0.05) remergeTimeEl.textContent = wait.toFixed(1);
    protectedEl.hidden = !followed.protected;
  } else if (mode === "playing") {
    hud.hidden = true;
  }

  drawWorld(ctx, snapshot, camera, viewport, { world });
  lastDraw = { snapshot, viewport };
}

joinForm.addEventListener("submit", (event) => {
  event.preventDefault();
  sendJoin();
});
playBtn.addEventListener("click", () => sendJoin());
spectateBtn.addEventListener("click", () => enterSpectate());
customizeBtn.addEventListener("click", () => showMenu());
respawnBtn.addEventListener("click", () => sendJoin());

retryBtn.addEventListener("click", () => {
  // Skipping the rest of the backoff, not stacking a second socket on top of an
  // attempt already in flight.
  if (socket !== null) return;
  if (reconnectTimer !== null) {
    window.clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  offlineStatus.textContent = "Reaching the server…";
  connect();
});

canvas.addEventListener("pointermove", (event) => {
  pointer = pointerOnCanvas(event);
});

window.addEventListener(
  "wheel",
  (event) => {
    if (mode !== "playing" && mode !== "spectating") return;
    event.preventDefault();
    const pixels = event.deltaMode === 1 ? event.deltaY * 16 : event.deltaY;
    zoomFactor *= Math.exp(-pixels * ZOOM_SENSITIVITY);
    zoomFactor = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, zoomFactor));
  },
  { passive: false }
);

canvas.addEventListener("click", (event) => {
  if (mode !== "spectating" || !lastDraw) return;
  const point = pointerOnCanvas(event);
  const hit = topmostPlayerAt(lastDraw.snapshot, lastDraw.viewport, point.x, point.y);
  if (hit) {
    followId = hit;
    snapCamera = true;
    return;
  }
  cycleFollow(lastDraw.snapshot);
});

window.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    if (mode !== "spectating") return;
    event.preventDefault();
    showMenu();
    return;
  }
  if (event.code !== "Space") return;
  if (mode !== "playing") return;
  // OS auto-repeat would dump 1 → 2 → 4 → 8 in a few hundred milliseconds.
  // One physical press is one split; mash Space to go further.
  if (event.repeat) return;
  event.preventDefault();
  sendJson({ type: "split" });
});

syncJoinButtons();
connect();
requestAnimationFrame(tick);