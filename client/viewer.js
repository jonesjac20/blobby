/**
 * Phase 1 verification viewer.
 *
 * Drives the shared renderer from recorded frames instead of a live socket, so
 * every [Both] checklist item in GUIDEBOOK.md can be watched, paused and
 * stepped through a frame at a time. The Phase 3 client will swap this file for
 * one that feeds `render.js` from a WebSocket; render.js itself stays put.
 */

import { RecordingCursor } from "./recording.js";
import {
  colorForId,
  createCamera,
  drawInputRays,
  drawMergeReady,
  drawVelocityArrows,
  drawWorld,
  fitCamera,
  followCamera,
  interpolateStates,
  playerMass,
  resizeCanvas,
} from "./render.js";

const RECORDINGS = "./recordings";
const STORAGE_KEY = "blobby.phase1.verified";

const el = {
  list: document.getElementById("scenario-list"),
  title: document.getElementById("scenario-title"),
  checklist: document.getElementById("scenario-checklist"),
  expect: document.getElementById("scenario-expect"),
  canvas: document.getElementById("stage-canvas"),
  toast: document.getElementById("event-toast"),
  play: document.getElementById("play"),
  stepBack: document.getElementById("step-back"),
  stepForward: document.getElementById("step-forward"),
  timeline: document.getElementById("timeline"),
  clock: document.getElementById("clock"),
  speed: document.getElementById("speed"),
  showDebug: document.getElementById("show-debug"),
  followCam: document.getElementById("follow-camera"),
  players: document.getElementById("players"),
  foodCount: document.getElementById("food-count"),
  eventLog: document.getElementById("event-log"),
  verified: document.getElementById("verified"),
  verifiedCount: document.getElementById("verified-count"),
  resetVerified: document.getElementById("reset-verified"),
};

const camera = createCamera();
const state = {
  index: [],
  recording: null,
  cursor: null,
  /** Fractional frame position, so playback is smooth between ticks. */
  position: 0,
  playing: false,
  speed: 1,
  verified: loadVerified(),
};

// --- data ------------------------------------------------------------------

async function boot() {
  const response = await fetch(`${RECORDINGS}/index.json`);
  if (!response.ok) {
    el.title.textContent = "No recordings found";
    el.checklist.textContent = "Run: python -m tools.record";
    return;
  }
  state.index = (await response.json()).scenarios;
  renderScenarioList();
  updateVerifiedCount();
  await select(state.index[0].id);
}

async function select(id) {
  const meta = state.index.find((entry) => entry.id === id);
  const response = await fetch(`${RECORDINGS}/${id}.json`);
  state.recording = await response.json();
  state.cursor = new RecordingCursor(state.recording);
  state.position = 0;
  state.playing = false;

  el.title.textContent = meta.title;
  el.checklist.textContent = meta.checklist;
  el.expect.textContent = meta.expect;
  el.timeline.max = String(state.recording.frames.length - 1);
  el.timeline.value = "0";
  el.speed.value = String(state.recording.speed ?? 1);
  state.speed = Number(el.speed.value);
  el.verified.checked = Boolean(state.verified[id]);

  renderScenarioList();
  renderEventLog();
  frameCamera();
  setPlaying(true);
}

// --- playback ---------------------------------------------------------------

function frameCount() {
  return state.cursor ? state.cursor.frameCount : 0;
}

function setPlaying(playing) {
  if (playing && state.position >= frameCount() - 1) state.position = 0;
  state.playing = playing;
  el.play.textContent = playing ? "Pause" : "Play";
}

function seek(index) {
  state.position = Math.min(Math.max(index, 0), Math.max(frameCount() - 1, 0));
  el.timeline.value = String(Math.floor(state.position));
}

function frameCamera() {
  const { viewport } = resizeCanvas(el.canvas);
  if (!state.recording) return;
  if (el.followCam.checked) return;
  fitCamera(camera, state.recording.view, viewport);
}

let lastTimestamp = 0;

function tick(timestamp) {
  const dt = lastTimestamp ? Math.min((timestamp - lastTimestamp) / 1000, 0.1) : 0;
  lastTimestamp = timestamp;

  if (state.cursor) {
    if (state.playing) {
      state.position += dt * state.recording.tickRate * state.speed;
      if (state.position >= frameCount() - 1) {
        state.position = frameCount() - 1;
        setPlaying(false);
      }
      el.timeline.value = String(Math.floor(state.position));
    }
    draw(dt);
  }
  requestAnimationFrame(tick);
}

// --- rendering ---------------------------------------------------------------

function draw(dt) {
  const { ctx, viewport } = resizeCanvas(el.canvas);
  const cursor = state.cursor;

  const i = Math.min(Math.floor(state.position), cursor.frameCount - 1);
  const j = Math.min(i + 1, cursor.frameCount - 1);
  const alpha = state.position - i;

  // Food never moves, so both snapshots can share the newer list. That keeps
  // this to one delta walk per rendered frame.
  const next = cursor.stateAt(j);
  const previous = { type: "state", players: cursor.frameAt(i).players, food: next.food };
  const snapshot = interpolateStates(previous, next, alpha);

  if (el.followCam.checked) {
    const biggest = [...snapshot.players]
      .filter((player) => player.pieces.length)
      .sort((a, b) => playerMass(b) - playerMass(a))[0];
    if (biggest) {
      followCamera(camera, snapshot, biggest.id, viewport, { smoothing: 6, dt });
    }
  } else {
    fitCamera(camera, state.recording.view, viewport);
  }

  drawWorld(ctx, snapshot, camera, viewport, { world: state.recording.world });

  if (el.showDebug.checked) {
    const debug = cursor.frameAt(i).debug;
    drawInputRays(ctx, snapshot, camera, viewport, debug && debug.inputs);
    drawMergeReady(ctx, snapshot, camera, viewport, debug && debug.pieces);
    drawVelocityArrows(ctx, snapshot, camera, viewport, debug && debug.pieces);
  }

  renderHud(snapshot, cursor.frameAt(i));
}

function renderHud(snapshot, frame) {
  el.clock.textContent = `${frame.t.toFixed(2)}s`;
  el.foodCount.textContent = `food ${snapshot.food.length}`;

  el.players.replaceChildren(
    ...snapshot.players.map((player) => {
      const row = document.createElement("div");
      row.className = player.pieces.length ? "player-row" : "player-row is-out";

      const swatch = document.createElement("span");
      swatch.className = "swatch";
      swatch.style.background = colorForId(player.id).stroke;

      const name = document.createElement("span");
      name.className = "name";
      name.textContent = player.name;

      const masses = document.createElement("span");
      masses.className = "masses";
      masses.textContent = player.pieces.length
        ? `${playerMass(player).toFixed(0)} = [${player.pieces
            .map((piece) => piece.mass.toFixed(0))
            .join(",")}]`
        : "eaten";

      row.append(swatch, name, masses);
      return row;
    })
  );

  const current = (frame.events || [])[0];
  el.toast.hidden = !current;
  if (current) el.toast.textContent = current;

  highlightCurrentEvent(frame.t);
}

function renderEventLog() {
  const entries = state.cursor.events();

  if (!entries.length) {
    const empty = document.createElement("li");
    empty.className = "empty";
    empty.textContent = "No discrete events - watch the blobs.";
    el.eventLog.replaceChildren(empty);
    return;
  }

  el.eventLog.replaceChildren(
    ...entries.map(({ index, t, event }) => {
      const item = document.createElement("li");
      item.dataset.t = String(t);

      const button = document.createElement("button");
      button.type = "button";
      button.innerHTML = `<span class="at">${t.toFixed(2)}s</span> ${escapeHtml(event)}`;
      button.addEventListener("click", () => {
        setPlaying(false);
        seek(index);
      });

      item.append(button);
      return item;
    })
  );
}

function highlightCurrentEvent(now) {
  for (const item of el.eventLog.children) {
    if (!item.dataset.t) continue;
    const at = Number(item.dataset.t);
    item.classList.toggle("is-current", Math.abs(at - now) < 0.4);
  }
}

function renderScenarioList() {
  el.list.replaceChildren(
    ...state.index.map((meta) => {
      const item = document.createElement("li");
      if (meta.tags && meta.tags.includes("demo")) item.className = "demo-entry";

      const button = document.createElement("button");
      button.type = "button";
      button.textContent = meta.title;
      if (state.verified[meta.id]) button.classList.add("is-verified");
      if (state.recording && state.recording.id === meta.id) {
        button.setAttribute("aria-current", "true");
      }
      button.addEventListener("click", () => select(meta.id));

      item.append(button);
      return item;
    })
  );
}

// --- verified checkboxes ------------------------------------------------------

function loadVerified() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
  } catch {
    return {};
  }
}

function saveVerified() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state.verified));
  updateVerifiedCount();
  renderScenarioList();
}

function updateVerifiedCount() {
  const done = state.index.filter((meta) => state.verified[meta.id]).length;
  el.verifiedCount.textContent = `${done} / ${state.index.length} verified`;
}

// --- wiring ---------------------------------------------------------------

el.play.addEventListener("click", () => setPlaying(!state.playing));
el.stepBack.addEventListener("click", () => {
  setPlaying(false);
  seek(Math.floor(state.position) - 1);
});
el.stepForward.addEventListener("click", () => {
  setPlaying(false);
  seek(Math.floor(state.position) + 1);
});
el.timeline.addEventListener("input", () => {
  setPlaying(false);
  seek(Number(el.timeline.value));
});
el.speed.addEventListener("change", () => {
  state.speed = Number(el.speed.value);
});
el.followCam.addEventListener("change", frameCamera);
el.verified.addEventListener("change", () => {
  state.verified[state.recording.id] = el.verified.checked;
  saveVerified();
});
el.resetVerified.addEventListener("click", () => {
  state.verified = {};
  el.verified.checked = false;
  saveVerified();
});

window.addEventListener("resize", frameCamera);

document.addEventListener("keydown", (event) => {
  if (event.target instanceof HTMLInputElement && event.target.type !== "checkbox") return;
  if (event.code === "Space") {
    event.preventDefault();
    setPlaying(!state.playing);
  } else if (event.code === "ArrowLeft") {
    setPlaying(false);
    seek(Math.floor(state.position) - (event.shiftKey ? 10 : 1));
  } else if (event.code === "ArrowRight") {
    setPlaying(false);
    seek(Math.floor(state.position) + (event.shiftKey ? 10 : 1));
  }
});

function escapeHtml(value) {
  return value.replace(
    /[&<>"']/g,
    (character) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]
  );
}

requestAnimationFrame(tick);
boot();
