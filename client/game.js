/**
 * Live game client. Drives render.js from a /ws connection.
 *
 * Connects on load and does not send join until Play. Food is held separately
 * and spliced onto a copy of the interpolated snapshot at draw time. Spacebar
 * sends split while playing; held-key auto-repeat is ignored. Wheel zooms the
 * follow-cam while playing or spectating.
 */

import {
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
  worldToScreen,
} from "./render.js";

const INPUT_INTERVAL_MS = 1000 / 20;
const FOLLOW_SMOOTHING = 6;
const DEADZONE_PX = 8;
const MAX_DT = 0.1;
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

const canvas = document.getElementById("game-canvas");
const hud = document.getElementById("hud");
const massEl = document.getElementById("mass");
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
let latestFood = [];

/** @type {{ x: number, y: number } | null} */
let pointer = null;
let lastInputAt = 0;
let lastTimestamp = 0;

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
    latestFood = [];
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
      break;
    case "food":
      latestFood = (msg.food || []).map(([x, y]) => ({ x, y }));
      break;
    case "state":
      applyConfig(msg);
      previousState = nextState;
      nextState = msg;
      nextArrivedAt = performance.now();
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
  const ids = living.map((player) => player.id);
  const index = ids.indexOf(followId);
  followId = ids[(index + 1) % ids.length];
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

function maybeSendInput(snapshot, viewport, now) {
  if (mode !== "playing" || !socketIsOpen() || pointer === null || !selfId) return;
  const player = snapshot.players.find((candidate) => candidate.id === selfId);
  if (!player || !player.pieces.length) return;
  if (now - lastInputAt < INPUT_INTERVAL_MS) return;
  lastInputAt = now;

  const centroid = playerCentroid(player);
  if (!centroid) return;
  const origin = worldToScreen(camera, viewport, centroid.x, centroid.y);
  const dx = pointer.x - origin.x;
  const dy = pointer.y - origin.y;
  const dist = Math.hypot(dx, dy);
  if (dist < DEADZONE_PX) {
    sendJson({ type: "input", dx: 0, dy: 0 });
    return;
  }
  sendJson({ type: "input", dx: dx / dist, dy: dy / dist });
}

function followedPlayer(snapshot) {
  if (!followId) return null;
  const player = snapshot.players.find((candidate) => candidate.id === followId);
  if (!player || !player.pieces.length) return null;
  return player;
}

function tick(timestamp) {
  requestAnimationFrame(tick);
  const dt = lastTimestamp ? Math.min((timestamp - lastTimestamp) / 1000, MAX_DT) : 0;
  lastTimestamp = timestamp;

  const { ctx, viewport } = resizeCanvas(canvas);
  if (!nextState) {
    ctx.clearRect(0, 0, viewport.width, viewport.height);
    return;
  }

  const elapsed = (timestamp - nextArrivedAt) / 1000;
  const alpha = Math.min(Math.max(elapsed / tickSeconds, 0), 1);
  const interpolated = interpolateStates(previousState, nextState, alpha);
  const snapshot = { ...interpolated, food: latestFood };

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
    const wait = playerRemergeIn(followed);
    remergeEl.hidden = wait < 0.05;
    if (wait >= 0.05) remergeTimeEl.textContent = wait.toFixed(1);
    protectedEl.hidden = !followed.protected;
  } else if (mode === "playing") {
    hud.hidden = true;
  }

  drawWorld(ctx, snapshot, camera, viewport, { world });
  lastDraw = { snapshot, viewport };
  maybeSendInput(snapshot, viewport, timestamp);
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