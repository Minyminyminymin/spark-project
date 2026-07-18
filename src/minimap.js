/**
 * minimap.js — Top-down map with a path trail.
 *
 * Pure 2D canvas overlay (no second 3D render pass — cheap and crisp).
 * World mapping: fixed top view centered on the origin. World +X → map
 * right, world +Z → map down, so "up" on the map is world -Z (the
 * direction the player faces at spawn).
 *
 * Drawn each frame:
 *   - grid (1 line / 5 m) + border
 *   - environment marker (tree: trunk dot + canopy ring)
 *   - tagged-object markers (annotations.js, low-opacity dots + labels)
 *   - the avatar's walked path as a polyline (the "trail")
 *   - avatar arrow (position + heading)
 *   - camera dot (useful in first-person flight)
 */
export function createMinimap({
  mount,
  extent = 15,       // world metres from centre to map edge
  trailMinStep = 0.15, // metres between recorded trail points
  maxTrailPoints = 3000,
} = {}) {
  const wrap = document.createElement("div");
  wrap.id = "minimap";
  wrap.innerHTML = `<span class="minimap-label">TOP VIEW</span>`;
  const canvas = document.createElement("canvas");
  wrap.appendChild(canvas);
  mount.appendChild(wrap);

  const ctx = canvas.getContext("2d");
  let cssSize = 0;

  const ro = new ResizeObserver(() => {
    cssSize = wrap.clientWidth;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = canvas.height = Math.round(cssSize * dpr);
    canvas.style.width = canvas.style.height = `${cssSize}px`;
  });
  ro.observe(wrap);

  let mapExtent = extent;
  const trail = []; // flat [x0, z0, x1, z1, ...] in world coords
  let lastX = null;
  let lastZ = null;

  const toX = (x) => canvas.width / 2 + (x / mapExtent) * (canvas.width / 2);
  const toY = (z) => canvas.height / 2 + (z / mapExtent) * (canvas.height / 2);

  function drawGrid() {
    const s = canvas.width;
    ctx.clearRect(0, 0, s, s);
    ctx.fillStyle = "rgba(14, 14, 18, 0.75)";
    ctx.fillRect(0, 0, s, s);

    ctx.strokeStyle = "rgba(255, 255, 255, 0.07)";
    ctx.lineWidth = 1;
    for (let m = -Math.floor(mapExtent / 5) * 5; m <= mapExtent; m += 5) {
      ctx.beginPath();
      ctx.moveTo(toX(m), 0);
      ctx.lineTo(toX(m), s);
      ctx.moveTo(0, toY(m));
      ctx.lineTo(s, toY(m));
      ctx.stroke();
    }
    // centre axes slightly brighter
    ctx.strokeStyle = "rgba(255, 255, 255, 0.14)";
    ctx.beginPath();
    ctx.moveTo(toX(0), 0);
    ctx.lineTo(toX(0), s);
    ctx.moveTo(0, toY(0));
    ctx.lineTo(s, toY(0));
    ctx.stroke();
  }

  function drawEnvironment(env) {
    if (!env) return;
    ctx.strokeStyle = "rgba(110, 231, 168, 0.45)";
    ctx.lineWidth = 1.5;
    if (env.rect) {
      // room / area footprint (world-space AABB)
      const s = canvas.width / (2 * mapExtent);
      ctx.strokeRect(
        toX(env.x) - (env.rect.w / 2) * s,
        toY(env.z) - (env.rect.d / 2) * s,
        env.rect.w * s,
        env.rect.d * s
      );
      return;
    }
    if (env.canopyRadius) {
      ctx.beginPath();
      ctx.arc(toX(env.x), toY(env.z), (env.canopyRadius / mapExtent) * (canvas.width / 2), 0, Math.PI * 2);
      ctx.stroke();
    }
    if (env.trunkRadius) {
      ctx.fillStyle = "rgba(110, 231, 168, 0.9)";
      ctx.beginPath();
      ctx.arc(toX(env.x), toY(env.z), Math.max(2, (env.trunkRadius / mapExtent) * (canvas.width / 2)), 0, Math.PI * 2);
      ctx.fill();
    }
  }

  function drawAnnotations(list) {
    if (!list || list.length === 0) return;
    ctx.font = "9px system-ui, sans-serif";
    ctx.textAlign = "center";
    for (const a of list) {
      const x = toX(a.x);
      const y = toY(a.z);
      ctx.fillStyle = "rgba(255, 209, 102, 0.75)";
      ctx.beginPath();
      ctx.arc(x, y, 2.5, 0, Math.PI * 2);
      ctx.fill();
      if (a.label) {
        ctx.fillStyle = "rgba(255, 255, 255, 0.55)";
        ctx.fillText(a.label, x, y - 5);
      }
    }
  }

  function drawTrail() {
    if (trail.length < 4) return;
    ctx.strokeStyle = "rgba(138, 184, 240, 0.85)";
    ctx.lineWidth = 1.5;
    ctx.lineJoin = "round";
    ctx.beginPath();
    ctx.moveTo(toX(trail[0]), toY(trail[1]));
    for (let i = 2; i < trail.length; i += 2) {
      ctx.lineTo(toX(trail[i]), toY(trail[i + 1]));
    }
    ctx.stroke();
  }

  function drawAvatar(x, z, heading) {
    const px = toX(x);
    const py = toY(z);
    const r = Math.max(5, canvas.width * 0.02);
    ctx.save();
    ctx.translate(px, py);
    // world facing vector (sin h, cos h) → canvas (x right, z down)
    ctx.rotate(Math.atan2(Math.sin(heading), Math.cos(heading)) + Math.PI);
    ctx.fillStyle = "#ffd166";
    ctx.beginPath();
    ctx.moveTo(0, -r);          // nose
    ctx.lineTo(r * 0.7, r);     // back right
    ctx.lineTo(0, r * 0.55);    // tail notch
    ctx.lineTo(-r * 0.7, r);    // back left
    ctx.closePath();
    ctx.fill();
    ctx.restore();
  }

  function drawCamera(x, z) {
    ctx.strokeStyle = "rgba(255, 255, 255, 0.8)";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.arc(toX(x), toY(z), 3.5, 0, Math.PI * 2);
    ctx.stroke();
  }

  return {
    /**
     * Call once per frame.
     * @param {object} s { avatarX, avatarZ, heading, camX, camZ, environment,
     *   annotations: [{x, z, label}] (world coords), optional }
     */
    update(s) {
      if (canvas.width === 0) return;

      // Record the trail only when the avatar actually moved.
      if (s.avatarX !== undefined) {
        if (lastX === null || Math.hypot(s.avatarX - lastX, s.avatarZ - lastZ) > trailMinStep) {
          trail.push(s.avatarX, s.avatarZ);
          lastX = s.avatarX;
          lastZ = s.avatarZ;
          if (trail.length > maxTrailPoints * 2) trail.splice(0, 2);
        }
      }

      drawGrid();
      drawEnvironment(s.environment);
      drawAnnotations(s.annotations);
      drawTrail();
      if (s.camX !== undefined) drawCamera(s.camX, s.camZ);
      if (s.avatarX !== undefined) drawAvatar(s.avatarX, s.avatarZ, s.heading ?? 0);
    },

    setExtent(v) {
      mapExtent = Math.max(2, v);
    },

    clearTrail() {
      trail.length = 0;
      lastX = null;
      lastZ = null;
    },

    dispose() {
      ro.disconnect();
      wrap.remove();
    },
  };
}
