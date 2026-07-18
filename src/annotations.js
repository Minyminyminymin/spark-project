/**
 * annotations.js — Name tags on objects inside the scanned scene
 * (splat-analyzer-plan.md, Phase A: the manual tagging tool).
 *
 * Coordinate rule (plan §0): every object's `position` is stored in
 * EnvironmentRoot-LOCAL coordinates — the same frame the collider mesh
 * lives in. World positions are derived every frame via
 * environmentRoot.matrixWorld, so moving/scaling the environment in the
 * Controls panel never invalidates saved annotations.
 *
 * Tag-mode click detection: when a color-carrying point cloud is loaded
 * (the raw scan PLY — position + vertex color, see models.js
 * loadPointCloud), a click doesn't just drop a bare point. It seeds a small
 * neighbourhood around the hit, averages its color, then grows outward
 * (bounded radius + color-similarity, a lightweight stand-in for a real
 * connected-component flood fill) to estimate the clicked object's AABB and
 * dominant color — entirely client-side, no ML service. That gives every
 * tag a real bounding box (toggle in the Controls panel / **B**) and lets
 * typed labels get auto-specified ("chair" → "white chair") instead of
 * staying generic.
 *
 * worldstate.js (Phase B) is the read-only query API a future agent
 * consumes over this same data — this module owns the mutable side
 * (render, tag mode, persistence, the Objects tab).
 */
import * as THREE from "three";

const SOURCE_COLOR = {
  manual: 0xffd166, // matches the avatar arrow on the minimap
  auto: 0x8ab8f0, // matches the camera dot / trail
  verified: 0x6ee7a8, // matches the environment marker
};

const FADE_NEAR = 1; // m — full opacity within this distance
const FADE_FAR = 6; // m — minimum opacity beyond this distance
const FADE_MIN_OPACITY = 0.15;
const LIST_REFRESH_INTERVAL = 0.25; // s — Objects tab live-distance throttle
const DEFAULT_RADIUS = 0.5; // m — fallback when extent detection finds nothing

// ---------- color naming (HSL bucket classification, no ML needed) ----------

const COLOR_WORDS = [
  "black", "white", "gray", "grey", "red", "orange", "yellow", "green",
  "cyan", "blue", "purple", "pink", "brown", "beige", "silver", "gold",
];

function rgbToHsl(r, g, b) {
  // r, g, b in 0..1
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  if (max === min) return { h: 0, s: 0, l };
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h;
  switch (max) {
    case r: h = (g - b) / d + (g < b ? 6 : 0); break;
    case g: h = (b - r) / d + 2; break;
    default: h = (r - g) / d + 4; break;
  }
  return { h: h * 60, s, l };
}

/** r, g, b in 0..255 → a short color name. */
function nameColor(r, g, b) {
  const { h, s, l } = rgbToHsl(r / 255, g / 255, b / 255);
  if (l < 0.13) return "black";
  if (l > 0.9 && s < 0.25) return "white";
  if (s < 0.14) return "gray";
  if (h >= 15 && h < 45 && l < 0.45 && s > 0.2) return "brown";
  if (h < 15 || h >= 345) return "red";
  if (h < 45) return "orange";
  if (h < 70) return "yellow";
  if (h < 170) return "green";
  if (h < 200) return "cyan";
  if (h < 255) return "blue";
  if (h < 290) return "purple";
  if (h < 345) return "pink";
  return "gray";
}

function withColorPrefix(label, colorName) {
  if (!colorName) return label;
  const lower = label.toLowerCase();
  if (COLOR_WORDS.some((w) => lower.includes(w))) return label; // already specific
  return `${colorName} ${label}`;
}

// ---------- point-cloud region detection ----------
//
// Two quality levels (Controls → Annotations → "HQ detect"):
//
//  "low"  — legacy heuristic: color-bounded radius growth from the seed.
//  "high" — voxel-grid CONNECTED-COMPONENT flood fill (default):
//           the candidate sphere is bucketed into 5 cm voxels, then a BFS
//           walks 26-neighbour voxels from the seed, accepting a voxel if
//           its mean color is close to the ADAPTIVE region color (running
//           mean, so shading gradients and multi-tone objects survive).
//           A detected floor slab under the seed is excluded so tags stop
//           bleeding into the ground, and the reach is larger (1.8 m)
//           because connectivity — not distance — is the boundary now.
//
// Dominant color is picked by per-point VOTING on color names (histogram),
// not by averaging RGB — averaging a multi-color object yields muddy
// "gray"; voting returns what a human would say ("white", "blue").

const SEED_RADIUS = 0.1;
const HQ_MAX_RADIUS = 1.8;
const LQ_MAX_RADIUS = 1.1;
const HQ_COLOR_TOL = 55;
const LQ_COLOR_TOL = 45;
const VOXEL = 0.05; // m
const FLOOR_SLAB = 0.05; // m above the lowest dense slab counts as "floor"

/** Per-point color-name votes → most common name. */
function voteColorName(col, scale, indices) {
  const votes = new Map();
  for (const i of indices) {
    const o = i * 3;
    const name = nameColor(col[o] * scale, col[o + 1] * scale, col[o + 2] * scale);
    votes.set(name, (votes.get(name) ?? 0) + 1);
  }
  let best = null;
  let bestCount = 0;
  for (const [name, count] of votes) {
    if (count > bestCount) {
      best = name;
      bestCount = count;
    }
  }
  return best;
}

/**
 * Estimate the clicked object's AABB + dominant color from the loaded
 * point cloud. Returns {localMin, localMax, pointCount, colorName} in the
 * point cloud's OWN local space, or null if the click missed the cloud.
 */
function detectObjectExtent(pointCloud, worldHitPoint, quality = "high") {
  const geo = pointCloud.geometry;
  const posAttr = geo?.attributes?.position;
  if (!posAttr || posAttr.isInterleavedBufferAttribute || posAttr.itemSize !== 3) return null;

  const localHit = pointCloud.worldToLocal(worldHitPoint.clone());
  const hx = localHit.x;
  const hy = localHit.y;
  const hz = localHit.z;

  const maxRadius = quality === "high" ? HQ_MAX_RADIUS : LQ_MAX_RADIUS;
  const seedR2 = SEED_RADIUS * SEED_RADIUS;
  const maxR2 = maxRadius * maxRadius;

  const pos = posAttr.array;
  const n = posAttr.count;
  const colorAttr = geo.attributes.color;
  const hasColor =
    !!colorAttr && !colorAttr.isInterleavedBufferAttribute && colorAttr.itemSize === 3 && colorAttr.count === n;
  const col = hasColor ? colorAttr.array : null;

  // Single O(n) pass: gather every point inside the candidate sphere.
  const sphereIdx = [];
  const seedIdx = [];
  let sphereMinY = Infinity;
  for (let i = 0, o = 0; i < n; i++, o += 3) {
    const dx = pos[o] - hx;
    const dy = pos[o + 1] - hy;
    const dz = pos[o + 2] - hz;
    const d2 = dx * dx + dy * dy + dz * dz;
    if (d2 > maxR2) continue;
    sphereIdx.push(i);
    if (pos[o + 1] < sphereMinY) sphereMinY = pos[o + 1];
    if (d2 <= seedR2) seedIdx.push(i);
  }
  if (seedIdx.length < 4) return null; // click missed the point cloud entirely

  // three.js PLYLoader emits 0..1 float colors for uchar r/g/b properties —
  // detect that vs. an already-0..255 source defensively.
  const scale = hasColor ? (col[seedIdx[0] * 3] <= 1.5 ? 255 : 1) : 1;

  let accepted;
  if (quality === "high") {
    accepted = floodFillVoxels({
      pos, col, scale, hasColor, sphereIdx, seedIdx, hy, sphereMinY,
    });
  } else {
    accepted = growByColorBall({ pos, col, scale, hasColor, sphereIdx, seedIdx });
  }
  if (!accepted || accepted.length < 4) accepted = seedIdx;

  let minX = Infinity, minY = Infinity, minZ = Infinity;
  let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
  for (const i of accepted) {
    const o = i * 3;
    const x = pos[o], y = pos[o + 1], z = pos[o + 2];
    if (x < minX) minX = x;
    if (x > maxX) maxX = x;
    if (y < minY) minY = y;
    if (y > maxY) maxY = y;
    if (z < minZ) minZ = z;
    if (z > maxZ) maxZ = z;
  }

  return {
    localMin: new THREE.Vector3(minX, minY, minZ),
    localMax: new THREE.Vector3(maxX, maxY, maxZ),
    pointCount: accepted.length,
    // Vote on the seed neighbourhood + accepted region, never the mean.
    colorName: hasColor ? voteColorName(col, scale, accepted) : null,
  };
}

/** Legacy "low" quality: accept sphere points whose color ≈ seed color. */
function growByColorBall({ pos, col, scale, hasColor, sphereIdx, seedIdx }) {
  if (!hasColor) return sphereIdx;
  let sr = 0, sg = 0, sb = 0;
  for (const i of seedIdx) {
    const o = i * 3;
    sr += col[o] * scale;
    sg += col[o + 1] * scale;
    sb += col[o + 2] * scale;
  }
  const cr = sr / seedIdx.length, cg = sg / seedIdx.length, cb = sb / seedIdx.length;
  const accepted = [];
  for (const i of sphereIdx) {
    const o = i * 3;
    if (
      Math.hypot(col[o] * scale - cr, col[o + 1] * scale - cg, col[o + 2] * scale - cb) <= LQ_COLOR_TOL
    ) {
      accepted.push(i);
    }
  }
  return accepted;
}

/** "high" quality: voxel BFS with adaptive color model + floor rejection. */
function floodFillVoxels({ pos, col, scale, hasColor, sphereIdx, seedIdx, hy, sphereMinY }) {
  // Floor slab: the lowest points inside the sphere. Only rejected when the
  // click itself is clearly ABOVE the slab (so tagging a rug still works).
  const floorTop = sphereMinY + FLOOR_SLAB;
  const rejectFloor = hy > floorTop + 0.06;

  // Bucket the sphere into voxels.
  const cells = new Map(); // key → { idx: [], r, g, b, n }
  const keyOf = (x, y, z) =>
    `${Math.floor(x / VOXEL)},${Math.floor(y / VOXEL)},${Math.floor(z / VOXEL)}`;
  for (const i of sphereIdx) {
    const o = i * 3;
    const y = pos[o + 1];
    if (rejectFloor && y < floorTop) continue;
    const key = keyOf(pos[o], y, pos[o + 2]);
    let cell = cells.get(key);
    if (!cell) {
      cell = { idx: [], r: 0, g: 0, b: 0, n: 0 };
      cells.set(key, cell);
    }
    cell.idx.push(i);
    if (hasColor) {
      cell.r += col[o] * scale;
      cell.g += col[o + 1] * scale;
      cell.b += col[o + 2] * scale;
    }
    cell.n += 1;
  }

  // Region color model starts from the seed and adapts as voxels join.
  let mr = 0, mg = 0, mb = 0, mn = 0;
  const seedKeys = new Set();
  for (const i of seedIdx) {
    const o = i * 3;
    seedKeys.add(keyOf(pos[o], pos[o + 1], pos[o + 2]));
    if (hasColor) {
      mr += col[o] * scale;
      mg += col[o + 1] * scale;
      mb += col[o + 2] * scale;
      mn += 1;
    }
  }

  const accepted = [];
  const visited = new Set();
  const queue = [...seedKeys];
  while (queue.length > 0) {
    const key = queue.pop();
    if (visited.has(key)) continue;
    visited.add(key);
    const cell = cells.get(key);
    if (!cell) continue;

    if (hasColor && mn > 0 && !seedKeys.has(key)) {
      const vr = cell.r / cell.n, vg = cell.g / cell.n, vb = cell.b / cell.n;
      const dist = Math.hypot(vr - mr / mn, vg - mg / mn, vb - mb / mn);
      if (dist > HQ_COLOR_TOL) continue; // color break → object boundary
    }

    accepted.push(...cell.idx);
    if (hasColor) {
      mr += cell.r;
      mg += cell.g;
      mb += cell.b;
      mn += cell.n;
    }

    const [cx, cy, cz] = key.split(",").map(Number);
    for (let dx = -1; dx <= 1; dx++) {
      for (let dy = -1; dy <= 1; dy++) {
        for (let dz = -1; dz <= 1; dz++) {
          if (dx === 0 && dy === 0 && dz === 0) continue;
          const nk = `${cx + dx},${cy + dy},${cz + dz}`;
          if (!visited.has(nk) && cells.has(nk)) queue.push(nk);
        }
      }
    }
  }
  return accepted;
}

// ---------- sprite/wireframe builders ----------

function dotTexture() {
  const size = 64;
  const c = document.createElement("canvas");
  c.width = c.height = size;
  const ctx = c.getContext("2d");
  const r = size / 2;
  const grad = ctx.createRadialGradient(r, r, 0, r, r, r);
  grad.addColorStop(0, "rgba(255,255,255,1)");
  grad.addColorStop(0.55, "rgba(255,255,255,0.85)");
  grad.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = grad;
  ctx.beginPath();
  ctx.arc(r, r, r, 0, Math.PI * 2);
  ctx.fill();
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

const measureCtx = document.createElement("canvas").getContext("2d");

function labelTexture(text) {
  const fontSize = 30;
  const padX = 14;
  const padY = 8;
  const font = `600 ${fontSize}px system-ui, sans-serif`;
  measureCtx.font = font;
  const textWidth = measureCtx.measureText(text).width;

  const c = document.createElement("canvas");
  c.width = Math.ceil(textWidth + padX * 2);
  c.height = fontSize + padY * 2;
  const ctx = c.getContext("2d");
  ctx.font = font;
  ctx.textBaseline = "middle";
  ctx.fillStyle = "rgba(14, 14, 18, 0.75)";
  roundRect(ctx, 0, 0, c.width, c.height, 10);
  ctx.fill();
  ctx.fillStyle = "#f4f4f8";
  ctx.fillText(text, padX, c.height / 2 + 1);

  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return { texture: tex, aspect: c.width / c.height };
}

/**
 * @param {object} deps
 * @param {THREE.Scene} deps.scene
 * @param {THREE.Group} deps.environmentRoot  parent for the annotations group
 * @param {THREE.Camera} deps.camera          for tag-mode click raycasting
 * @param {HTMLElement} deps.domElement        the render canvas
 * @param {string} deps.sceneName              collider GLB basename (no ext)
 * @param {ReturnType<import("./ui.js").createUI>} deps.ui
 * @param {() => (THREE.Points|null)} [deps.getPointCloud]
 *   Lazily-resolved active point cloud (color+position), used for tag-mode
 *   extent/color detection. Reads main.js's live visual/rawPly bindings.
 */
export function createAnnotations({ scene, environmentRoot, camera, domElement, sceneName, ui, getPointCloud }) {
  const draftKey = `spark-annotations:${sceneName}`;
  const anchorMap = dotTexture();

  const group = new THREE.Group(); // anchors + labels — toggled by "Name tags" / T
  group.name = "Annotations";
  environmentRoot.add(group);

  const bboxGroup = new THREE.Group(); // detected/known extents — toggled by "Bounding boxes" / B
  bboxGroup.name = "AnnotationBoundingBoxes";
  bboxGroup.visible = false;
  environmentRoot.add(bboxGroup);

  const objects = new Map(); // id -> record
  const lastCameraPos = new THREE.Vector3();
  let collider = null; // set once the environment GLB collider is ready
  let tagMode = false;
  let detectQuality = "high"; // "high" (voxel flood fill) | "low" (legacy)
  let pendingTag = null; // { position, aabb, colorName, radius } (env-local) awaiting a name
  let listRefreshTimer = 0;

  // ---------- object CRUD ----------

  function nextId(label) {
    const base =
      label
        .toLowerCase()
        .trim()
        .replace(/[^a-z0-9]+/g, "_")
        .replace(/^_+|_+$/g, "") || "object";
    let n = 1;
    while (objects.has(`${base}_${n}`)) n += 1;
    return `${base}_${n}`;
  }

  function buildVisual(record) {
    const anchor = new THREE.Sprite(
      new THREE.SpriteMaterial({
        map: anchorMap,
        color: SOURCE_COLOR[record.source] ?? SOURCE_COLOR.manual,
        depthTest: false,
        transparent: true,
      })
    );
    anchor.scale.setScalar(0.2);
    anchor.position.set(...record.position);
    anchor.renderOrder = 10;

    const { texture, aspect } = labelTexture(record.label);
    const labelSprite = new THREE.Sprite(
      new THREE.SpriteMaterial({ map: texture, depthTest: false, transparent: true })
    );
    const labelHeight = 0.18;
    labelSprite.scale.set(labelHeight * aspect, labelHeight, 1);
    // Hug the object: centred over the detected AABB, just above its top —
    // or, without an AABB, right above the anchor point.
    if (record.aabb) {
      labelSprite.position.set(
        (record.aabb.min[0] + record.aabb.max[0]) / 2,
        record.aabb.max[1] + 0.03,
        (record.aabb.min[2] + record.aabb.max[2]) / 2
      );
    } else {
      labelSprite.position.set(record.position[0], record.position[1] + 0.08, record.position[2]);
    }
    labelSprite.renderOrder = 11;

    group.add(anchor, labelSprite);
    record.anchor = anchor;
    record.label3d = labelSprite;
  }

  function buildBBox(record) {
    if (!record.aabb) return;
    const min = new THREE.Vector3(...record.aabb.min);
    const max = new THREE.Vector3(...record.aabb.max);
    const size = new THREE.Vector3().subVectors(max, min);
    const center = new THREE.Vector3().addVectors(min, max).multiplyScalar(0.5);
    const geom = new THREE.BoxGeometry(
      Math.max(size.x, 0.02),
      Math.max(size.y, 0.02),
      Math.max(size.z, 0.02)
    );
    const edges = new THREE.EdgesGeometry(geom);
    geom.dispose();
    const mat = new THREE.LineBasicMaterial({
      color: SOURCE_COLOR[record.source] ?? SOURCE_COLOR.manual,
      transparent: true,
      opacity: 0.85,
    });
    const box = new THREE.LineSegments(edges, mat);
    box.position.copy(center);
    bboxGroup.add(box);
    record.bboxMesh = box;
  }

  function disposeVisual(record) {
    for (const spr of [record.anchor, record.label3d]) {
      if (!spr) continue;
      group.remove(spr);
      spr.material.map?.dispose();
      spr.material.dispose();
    }
    if (record.bboxMesh) {
      bboxGroup.remove(record.bboxMesh);
      record.bboxMesh.geometry.dispose();
      record.bboxMesh.material.dispose();
      record.bboxMesh = null;
    }
  }

  function updateLabelTexture(record) {
    record.label3d.material.map?.dispose();
    const { texture, aspect } = labelTexture(record.label);
    record.label3d.material.map = texture;
    const labelHeight = 0.18;
    record.label3d.scale.set(labelHeight * aspect, labelHeight, 1);
  }

  /** Recolor the anchor/bbox after `record.source` changes (e.g. accept). */
  function updateSourceColor(record) {
    const color = SOURCE_COLOR[record.source] ?? SOURCE_COLOR.manual;
    record.anchor.material.color.setHex(color);
    if (record.bboxMesh) record.bboxMesh.material.color.setHex(color);
  }

  /** Insert an object record (from a file/draft load, or a new tag). */
  function insert(raw) {
    const record = {
      id: raw.id,
      label: raw.label,
      aliases: raw.aliases ?? [],
      position: [...raw.position],
      radius: raw.radius ?? DEFAULT_RADIUS,
      aabb: raw.aabb ? { min: [...raw.aabb.min], max: [...raw.aabb.max] } : undefined,
      confidence: raw.confidence ?? 1.0,
      source: raw.source ?? "manual",
      notes: raw.notes ?? "",
      worldPosition: new THREE.Vector3(),
    };
    buildVisual(record);
    buildBBox(record);
    objects.set(record.id, record);
    return record;
  }

  function addFromTag(label, positionLocal, extra = {}) {
    const record = insert({
      id: nextId(label),
      label,
      position: positionLocal,
      radius: extra.radius ?? DEFAULT_RADIUS,
      aabb: extra.aabb ?? undefined,
      confidence: 1.0,
      source: "manual",
    });
    saveDraft();
    refreshObjectsList();
    return record;
  }

  function remove(id) {
    const record = objects.get(id);
    if (!record) return;
    disposeVisual(record);
    objects.delete(id);
  }

  function rename(id, label) {
    const record = objects.get(id);
    if (!record || !label) return;
    record.label = label;
    updateLabelTexture(record);
  }

  // ---------- tag mode / click-to-place ----------

  function raycastEvent(e) {
    const rect = domElement.getBoundingClientRect();
    const ndc = new THREE.Vector2(
      ((e.clientX - rect.left) / rect.width) * 2 - 1,
      -((e.clientY - rect.top) / rect.height) * 2 + 1
    );
    camera.updateMatrixWorld();
    return collider?.raycastFromCamera?.(ndc, camera) ?? null;
  }

  // Capture-phase listener on `document` runs BEFORE player.js's own
  // (bubble-phase, on domElement) click-to-pointer-lock listener, so this
  // can veto it with stopImmediatePropagation — the plan requires tag-mode
  // clicks to raycast instead of locking the pointer, without touching
  // player.js (not in the plan's file list).
  function onDocumentClickCapture(e) {
    if (!tagMode || e.target !== domElement) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    if (pendingTag) {
      ui.addMessage("System", "Tag mode: finish naming the current point first (or submit empty to cancel).");
      return;
    }
    if (!collider) {
      ui.addMessage("System", "Tag mode: collider not loaded yet.");
      return;
    }
    const hit = raycastEvent(e);
    if (!hit) {
      ui.addMessage("System", "Tag mode: no surface hit — click on the scanned scene.");
      return;
    }
    const local = environmentRoot.worldToLocal(hit.point.clone());
    pendingTag = { position: [local.x, local.y, local.z], aabb: null, colorName: null, radius: DEFAULT_RADIUS };

    const pointCloud = getPointCloud?.();
    if (!pointCloud) {
      ui.setInputPlaceholder("Name this point… (Enter to save, empty to cancel)");
      ui.focusInput();
      ui.addMessage(
        "System",
        "Tag mode: type a name for this point (no point-cloud color data loaded — press L to load the " +
          "raw scan for automatic color/extent detection)."
      );
      return;
    }

    // Defer the (synchronous, O(point count)) detection pass one frame so
    // this message actually paints before the main thread blocks on it.
    ui.addMessage("System", "Tag mode: analyzing the scan around this point…");
    requestAnimationFrame(() => {
      const detection = detectObjectExtent(pointCloud, hit.point, detectQuality);
      if (detection) {
        const box = new THREE.Box3(detection.localMin, detection.localMax);
        box.applyMatrix4(pointCloud.matrix); // point-cloud-local → environmentRoot-local
        pendingTag.aabb = {
          min: [box.min.x, box.min.y, box.min.z],
          max: [box.max.x, box.max.y, box.max.z],
        };
        pendingTag.colorName = detection.colorName;
        const size = new THREE.Vector3();
        box.getSize(size);
        pendingTag.radius = Math.max(0.15, size.length() / 2);
        ui.addMessage(
          "System",
          `Tag mode: detected${detection.colorName ? ` a ${detection.colorName}` : ""} region ` +
            `(~${detection.pointCount} pts, ${size.x.toFixed(2)}×${size.y.toFixed(2)}×${size.z.toFixed(2)}m). ` +
            "Type a name."
        );
      } else {
        ui.addMessage("System", "Tag mode: no clear object edge detected — type a name for this point.");
      }
      ui.setInputPlaceholder("Name this point… (Enter to save, empty to cancel)");
      ui.focusInput();
    });
  }
  document.addEventListener("click", onDocumentClickCapture, true);

  function isAwaitingLabel() {
    return pendingTag !== null;
  }

  /** Feed the next chat submission here while isAwaitingLabel() is true. */
  function submitLabel(text) {
    if (!pendingTag) return;
    const typed = text.trim();
    ui.setInputPlaceholder(null);
    if (!typed) {
      pendingTag = null;
      ui.addMessage("System", "Tag mode: cancelled.");
      return;
    }
    const label = withColorPrefix(typed, pendingTag.colorName);
    const record = addFromTag(label, pendingTag.position, {
      aabb: pendingTag.aabb,
      radius: pendingTag.radius,
    });
    pendingTag = null;
    ui.addMessage("System", `Tagged "${record.label}" (${record.id}).`);
  }

  function setTagMode(enabled) {
    tagMode = enabled;
    if (!enabled && pendingTag) {
      pendingTag = null;
      ui.setInputPlaceholder(null);
    }
    ui.addMessage("System", enabled ? "Tag mode ON — click the scene to place a tag." : "Tag mode OFF.");
  }

  // ---------- persistence ----------

  function serialize() {
    return {
      version: 1,
      scene: sceneName,
      frame: "environment-local",
      updatedAt: new Date().toISOString(),
      objects: [...objects.values()].map((o) => ({
        id: o.id,
        label: o.label,
        aliases: o.aliases,
        position: o.position,
        radius: o.radius,
        ...(o.aabb ? { aabb: o.aabb } : {}),
        confidence: o.confidence,
        source: o.source,
        notes: o.notes,
      })),
    };
  }

  function saveDraft() {
    try {
      localStorage.setItem(draftKey, JSON.stringify(serialize()));
    } catch (err) {
      console.warn("[annotations] localStorage save failed", err);
    }
  }

  /** Load draft (localStorage) if present, else the checked-in JSON file. */
  async function load() {
    let data = null;
    try {
      const raw = localStorage.getItem(draftKey);
      if (raw) data = JSON.parse(raw);
    } catch (err) {
      console.warn("[annotations] bad localStorage draft, ignoring", err);
    }
    if (!data) {
      try {
        const res = await fetch(`/annotations/${sceneName}.json`);
        if (res.ok) data = await res.json();
      } catch {
        // offline, or file doesn't exist yet — start with an empty set.
      }
    }
    for (const raw of data?.objects ?? []) insert(raw);
    refreshObjectsList();
  }

  function exportJson() {
    const blob = new Blob([JSON.stringify(serialize(), null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${sceneName}.json`;
    a.click();
    URL.revokeObjectURL(url);
    ui.addMessage("System", `Exported ${objects.size} object(s). Drop the file into public/annotations/.`);
  }

  /** Dev-only nicety: POST to the Vite middleware in vite.config.js. */
  async function saveToDisk() {
    try {
      const res = await fetch(`/__annotations/${sceneName}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(serialize(), null, 2),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      ui.addMessage("System", `Saved public/annotations/${sceneName}.json.`);
    } catch (err) {
      ui.addMessage(
        "System",
        "Save to disk failed (only works with `npm run dev`) — use Export JSON instead."
      );
      console.warn("[annotations] saveToDisk failed", err);
    }
  }

  // ---------- Objects tab ----------

  function refreshObjectsList() {
    const entries = [...objects.values()]
      .map((o) => ({
        id: o.id,
        label: o.label,
        distance: o.worldPosition.distanceTo(lastCameraPos),
        source: o.source,
      }))
      .sort((a, b) => a.label.localeCompare(b.label));
    ui.renderObjects(entries);
  }

  ui.onObjectAction((action, id) => {
    if (action === "delete") {
      remove(id);
      saveDraft();
      refreshObjectsList();
    } else if (action === "rename") {
      const record = objects.get(id);
      if (!record) return;
      const next = window.prompt("Rename object", record.label);
      if (next === null) return;
      const label = next.trim();
      if (!label || label === record.label) return;
      rename(id, label);
      saveDraft();
      refreshObjectsList();
    } else if (action === "accept") {
      // Phase C review flow: promote an auto-detected proposal to verified
      // (recolors green — SOURCE_COLOR.verified) once a human has checked it.
      const record = objects.get(id);
      if (!record || record.source !== "auto") return;
      record.source = "verified";
      updateSourceColor(record);
      saveDraft();
      refreshObjectsList();
    }
  });

  // ---------- per-frame update ----------

  function update(delta, cameraWorldPos) {
    lastCameraPos.copy(cameraWorldPos);
    for (const o of objects.values()) {
      o.worldPosition.set(...o.position).applyMatrix4(environmentRoot.matrixWorld);
      const dist = o.worldPosition.distanceTo(cameraWorldPos);
      let t = (dist - FADE_NEAR) / (FADE_FAR - FADE_NEAR);
      t = Math.min(1, Math.max(0, t));
      const opacity = 1 - t * (1 - FADE_MIN_OPACITY);
      o.anchor.material.opacity = opacity;
      o.label3d.material.opacity = opacity;
      if (o.bboxMesh) o.bboxMesh.material.opacity = opacity * 0.9;
    }
    listRefreshTimer += delta;
    if (listRefreshTimer >= LIST_REFRESH_INTERVAL) {
      listRefreshTimer = 0;
      refreshObjectsList();
    }
  }

  return {
    load,
    update,
    setCollider(c) {
      collider = c;
    },
    setVisible(visible) {
      group.visible = visible;
    },
    isVisible() {
      return group.visible;
    },
    setBBoxVisible(visible) {
      bboxGroup.visible = visible;
    },
    isBBoxVisible() {
      return bboxGroup.visible;
    },
    setTagMode,
    setDetectQuality(q) {
      detectQuality = q === "low" ? "low" : "high";
    },
    isAwaitingLabel,
    submitLabel,
    exportJson,
    saveToDisk,
    /** Live records (with cached worldPosition) — read-only for worldstate.js. */
    getObjects() {
      return [...objects.values()];
    },
    dispose() {
      document.removeEventListener("click", onDocumentClickCapture, true);
      for (const o of objects.values()) disposeVisual(o);
      environmentRoot.remove(group);
      environmentRoot.remove(bboxGroup);
      anchorMap.dispose();
    },
  };
}
