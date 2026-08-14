/**
 * Reusable rendering core.
 *
 * Everything here operates on the server -> client `state` message from section
 * 4 of the build plan:
 *
 *   { type: "state",
 *     players: [{ id, name, pieces: [{ piece_id, x, y, mass }] }],
 *     food:    [{ id, x, y }] }
 *
 * Nothing in this file knows where that state came from. The Phase 1 viewer
 * feeds it recorded frames; the Phase 3 client will feed it WebSocket messages.
 * Keep it that way: anything that depends on recordings belongs in viewer.js.
 */

export function radiusForMass(mass) {
  return Math.sqrt(Math.max(mass, 0) / Math.PI);
}

export function playerMass(player) {
  return player.pieces.reduce((total, piece) => total + piece.mass, 0);
}

export function playerCentroid(player) {
  const total = playerMass(player);
  if (!player.pieces.length) return null;
  if (total === 0) {
    return { x: player.pieces[0].x, y: player.pieces[0].y };
  }
  let x = 0;
  let y = 0;
  for (const piece of player.pieces) {
    x += piece.x * piece.mass;
    y += piece.y * piece.mass;
  }
  return { x: x / total, y: y / total };
}

/** Stable per-player colour derived from the id, so no palette needs managing. */
export function colorForId(id) {
  let hash = 0;
  for (let i = 0; i < id.length; i += 1) {
    hash = (hash * 31 + id.charCodeAt(i)) >>> 0;
  }
  const hue = hash % 360;
  return {
    hue,
    fill: `hsla(${hue}, 70%, 55%, 0.75)`,
    stroke: `hsl(${hue}, 75%, 42%)`,
    text: `hsl(${hue}, 85%, 88%)`,
  };
}

// --- camera ---------------------------------------------------------------

/** `x`/`y` is the world point at the centre of the viewport. */
export function createCamera() {
  return { x: 0, y: 0, scale: 1 };
}

/** Frame a world-space rectangle [x, y, width, height], letterboxed to fit. */
export function fitCamera(camera, rect, viewport, padding = 1.06) {
  const [x, y, width, height] = rect;
  camera.x = x + width / 2;
  camera.y = y + height / 2;
  camera.scale = Math.min(
    viewport.width / (width * padding),
    viewport.height / (height * padding)
  );
  return camera;
}

/**
 * Phase 3 camera: centre on one player's centroid and zoom out as it grows.
 * `smoothing` is the fraction of the gap closed per second (0 snaps instantly).
 */
export function followCamera(camera, state, playerId, viewport, options = {}) {
  const {
    baseSpan = 420,
    referenceMass = 40,
    smoothing = 0,
    dt = 0,
    minScale = 0.05,
    maxScale = 12,
  } = options;

  const player = state.players.find((candidate) => candidate.id === playerId);
  const centroid = player ? playerCentroid(player) : null;
  if (!centroid) return camera;

  const mass = Math.max(playerMass(player), referenceMass);
  const span = baseSpan * Math.sqrt(mass / referenceMass);
  const target = {
    x: centroid.x,
    y: centroid.y,
    scale: clamp(Math.min(viewport.width, viewport.height) / span, minScale, maxScale),
  };

  if (smoothing <= 0 || dt <= 0) {
    Object.assign(camera, target);
    return camera;
  }
  const blend = 1 - Math.exp(-smoothing * dt);
  camera.x += (target.x - camera.x) * blend;
  camera.y += (target.y - camera.y) * blend;
  camera.scale += (target.scale - camera.scale) * blend;
  return camera;
}

export function worldToScreen(camera, viewport, x, y) {
  return {
    x: (x - camera.x) * camera.scale + viewport.width / 2,
    y: (y - camera.y) * camera.scale + viewport.height / 2,
  };
}

// --- interpolation --------------------------------------------------------

/**
 * Blend two consecutive snapshots. Section 6 of the build plan calls this
 * non-optional: 30Hz state rendered at 60fps+ visibly stutters without it.
 *
 * Pieces are matched by `piece_id`. One that only exists in `next` has just
 * appeared (a split), so it pops in at its true position rather than sliding in
 * from nowhere. One that only exists in `previous` is gone (eaten or merged)
 * and is dropped immediately.
 *
 * The result therefore carries `next`'s set of pieces with blended positions.
 * A caller that needs one snapshot exactly as recorded should render it
 * directly rather than asking for alpha 0.
 */
export function interpolateStates(previous, next, alpha) {
  if (!previous || previous === next) return next;
  // alpha 0 is `previous` by definition. Returning `next` here would jump a
  // whole tick ahead every time a fresh snapshot resets alpha.
  if (alpha <= 0) return previous;

  const before = new Map();
  for (const player of previous.players) {
    for (const piece of player.pieces) before.set(piece.piece_id, piece);
  }

  const players = next.players.map((player) => ({
    id: player.id,
    name: player.name,
    pieces: player.pieces.map((piece) => {
      const old = before.get(piece.piece_id);
      if (!old) return piece;
      return {
        piece_id: piece.piece_id,
        x: old.x + (piece.x - old.x) * alpha,
        y: old.y + (piece.y - old.y) * alpha,
        mass: old.mass + (piece.mass - old.mass) * alpha,
      };
    }),
  }));

  return { type: "state", players, food: next.food };
}

// --- drawing --------------------------------------------------------------

/**
 * Size the backing store to the element's CSS box times the device pixel ratio,
 * so circles and text stay crisp on high-DPI displays.
 */
export function resizeCanvas(canvas) {
  const ratio = window.devicePixelRatio || 1;
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { ctx, viewport: { width, height } };
}

export function drawWorld(ctx, state, camera, viewport, options = {}) {
  const { world = null, grid = 100, labels = true } = options;

  ctx.clearRect(0, 0, viewport.width, viewport.height);

  if (world) drawBackdrop(ctx, camera, viewport, world, grid);
  drawFood(ctx, state, camera, viewport);
  drawPieces(ctx, state, camera, viewport, labels);
}

function drawBackdrop(ctx, camera, viewport, world, grid) {
  const origin = worldToScreen(camera, viewport, 0, 0);
  const far = worldToScreen(camera, viewport, world.width, world.height);

  ctx.fillStyle = "#0f1420";
  ctx.fillRect(origin.x, origin.y, far.x - origin.x, far.y - origin.y);

  if (grid > 0 && grid * camera.scale > 8) {
    ctx.strokeStyle = "rgba(255, 255, 255, 0.045)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let x = 0; x <= world.width; x += grid) {
      const sx = worldToScreen(camera, viewport, x, 0).x;
      ctx.moveTo(sx, origin.y);
      ctx.lineTo(sx, far.y);
    }
    for (let y = 0; y <= world.height; y += grid) {
      const sy = worldToScreen(camera, viewport, 0, y).y;
      ctx.moveTo(origin.x, sy);
      ctx.lineTo(far.x, sy);
    }
    ctx.stroke();
  }

  ctx.strokeStyle = "rgba(120, 170, 255, 0.55)";
  ctx.lineWidth = 2;
  ctx.strokeRect(origin.x, origin.y, far.x - origin.x, far.y - origin.y);
}

function drawFood(ctx, state, camera, viewport) {
  const radius = Math.max(1.5, 2.4 * Math.min(camera.scale, 2));
  ctx.fillStyle = "#8fe38f";
  for (const food of state.food) {
    const point = worldToScreen(camera, viewport, food.x, food.y);
    if (point.x < -8 || point.y < -8 || point.x > viewport.width + 8 || point.y > viewport.height + 8) {
      continue;
    }
    ctx.beginPath();
    ctx.arc(point.x, point.y, radius, 0, Math.PI * 2);
    ctx.fill();
  }
}

function drawPieces(ctx, state, camera, viewport, labels) {
  // Smallest last, so a big blob never completely hides a small one.
  const drawOrder = [];
  for (const player of state.players) {
    for (const piece of player.pieces) drawOrder.push({ player, piece });
  }
  drawOrder.sort((a, b) => b.piece.mass - a.piece.mass);

  ctx.textAlign = "center";
  ctx.textBaseline = "middle";

  for (const { player, piece } of drawOrder) {
    const point = worldToScreen(camera, viewport, piece.x, piece.y);
    const radius = radiusForMass(piece.mass) * camera.scale;
    const color = colorForId(player.id);

    ctx.beginPath();
    ctx.arc(point.x, point.y, Math.max(radius, 2), 0, Math.PI * 2);
    ctx.fillStyle = color.fill;
    ctx.fill();
    ctx.lineWidth = Math.max(1, Math.min(3, radius * 0.12));
    ctx.strokeStyle = color.stroke;
    ctx.stroke();

    if (!labels || radius < 13) continue;
    ctx.fillStyle = color.text;
    ctx.font = `600 ${Math.min(15, Math.max(9, radius * 0.42))}px ui-monospace, monospace`;
    ctx.fillText(player.name, point.x, point.y - radius * 0.18);
    ctx.font = `${Math.min(13, Math.max(8, radius * 0.34))}px ui-monospace, monospace`;
    ctx.fillText(piece.mass.toFixed(0), point.x, point.y + radius * 0.32);
  }
}

/**
 * Phase 1 debug overlay: draw each piece's split-kick velocity as an arrow.
 *
 * `vx`/`vy` are deliberately absent from the section 4 wire format, so this
 * takes them separately and the Phase 3 client simply never calls it.
 */
export function drawVelocityArrows(ctx, state, camera, viewport, debugPieces) {
  if (!debugPieces) return;
  ctx.lineWidth = 2;
  ctx.strokeStyle = "#ffd166";
  ctx.fillStyle = "#ffd166";

  for (const player of state.players) {
    for (const piece of player.pieces) {
      const debug = debugPieces[piece.piece_id];
      if (!debug) continue;
      const speed = Math.hypot(debug.vx, debug.vy);
      if (speed < 0.01) continue;

      const from = worldToScreen(camera, viewport, piece.x, piece.y);
      // Scale by speed but keep short arrows visible at any zoom level.
      const length = Math.max(18, speed * camera.scale * 0.9);
      const ux = debug.vx / speed;
      const uy = debug.vy / speed;
      const to = { x: from.x + ux * length, y: from.y + uy * length };

      ctx.beginPath();
      ctx.moveTo(from.x, from.y);
      ctx.lineTo(to.x, to.y);
      ctx.stroke();

      ctx.beginPath();
      ctx.moveTo(to.x, to.y);
      ctx.lineTo(to.x - ux * 9 - uy * 5, to.y - uy * 9 + ux * 5);
      ctx.lineTo(to.x - ux * 9 + uy * 5, to.y - uy * 9 - ux * 5);
      ctx.closePath();
      ctx.fill();

      ctx.font = "11px ui-monospace, monospace";
      ctx.textAlign = "left";
      ctx.fillText(speed.toFixed(1), to.x + 6, to.y - 6);
      ctx.textAlign = "center";
    }
  }
}

/**
 * Phase 1 debug overlay: ring the pieces whose remerge timer has cleared.
 *
 * Without this the merge pull is indistinguishable from ordinary cohesion, since
 * both look like two blobs drifting together. The ring appears on the exact tick
 * the timer clears, so the pull has a visible start to measure from.
 */
export function drawMergeReady(ctx, state, camera, viewport, debugPieces) {
  if (!debugPieces) return;
  ctx.save();
  ctx.setLineDash([5, 4]);
  ctx.lineWidth = 2;
  ctx.strokeStyle = "rgba(130, 255, 200, 0.9)";

  for (const player of state.players) {
    for (const piece of player.pieces) {
      const debug = debugPieces[piece.piece_id];
      if (!debug || !debug.merge_ready) continue;

      const point = worldToScreen(camera, viewport, piece.x, piece.y);
      const radius = radiusForMass(piece.mass) * camera.scale;
      ctx.beginPath();
      ctx.arc(point.x, point.y, Math.max(radius, 2) + 4, 0, Math.PI * 2);
      ctx.stroke();
    }
  }
  ctx.restore();
}

/** Phase 1 debug overlay: draw each player's `last_input` as a dashed ray. */
export function drawInputRays(ctx, state, camera, viewport, inputs) {
  if (!inputs) return;
  ctx.save();
  ctx.setLineDash([5, 4]);
  ctx.lineWidth = 1.5;
  ctx.strokeStyle = "rgba(255, 255, 255, 0.5)";

  for (const player of state.players) {
    const input = inputs[player.id];
    if (!input) continue;
    const [dx, dy] = input;
    if (Math.hypot(dx, dy) < 0.001) continue;
    const centroid = playerCentroid(player);
    if (!centroid) continue;

    const from = worldToScreen(camera, viewport, centroid.x, centroid.y);
    ctx.beginPath();
    ctx.moveTo(from.x, from.y);
    ctx.lineTo(from.x + dx * 46, from.y + dy * 46);
    ctx.stroke();
  }
  ctx.restore();
}

function clamp(value, low, high) {
  return Math.min(Math.max(value, low), high);
}
