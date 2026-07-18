/**
 * main.js — Entry point. Wires the modules together.
 *
 * Desktop-only build (WebXR removed 2026-07-16).
 * Environment: MGstudio_SmallRoom — PLY scan as the VISUAL layer,
 * textured GLB mesh as the Unity-style COLLIDER (press M to show it).
 *
 * ── For Unity people ─────────────────────────────────────────────────
 * CONFIG below is your Inspector. There is no visual scene editor here:
 *  - CONFIG.player.start = spawn Transform.position
 *  - The Controls panel adjusts the environment LIVE; "Copy CONFIG values"
 *    copies the current values to paste back into this file.
 *  - Or fly somewhere in 1st person and press P for a spawn-point snippet.
 * ─────────────────────────────────────────────────────────────────────
 */
import "./style.css";
import * as THREE from "three";
import { createScene } from "./scene.js";
import { createPlayer } from "./player.js";
import { loadSplat } from "./splat.js";
import { loadModel, loadPointCloud } from "./models.js";
import { loadAvatar } from "./avatar.js";
import { createUI } from "./ui.js";
import { createMinimap } from "./minimap.js";
import { createCollider, createPrimitiveColliders } from "./collision.js";
import { createAnnotations } from "./annotations.js";
import { createWorldState } from "./worldstate.js";
import { createCapture } from "./capture.js";

const CONFIG = {
  // Everything under EnvironmentRoot: visual (PLY/SPZ) + collider (GLB)
  // stay aligned as siblings. Tweak live in the Controls panel, copy back.
  environment: {
    // Polycam exports are y-up, real-world metres, origin at scan centre.
    // Position Y scales with the environment: hand-tuned 1.75 at scale 1.5
    // → 2.33 at scale 2. Adjust with the Pos Y slider if scale changes.
    position: [0, 2.33, 0],
    // ⚠ DEGREES, not radians! (previous radian field caused a 233° tilt
    // when 180 was entered as if degrees). The mesh is already level
    // (measured tilt: 0.84°) and the PLY offset below handles axis
    // conventions — this should normally stay [0, 0, 0].
    rotationDeg: [0, 0, 0],
    scale: 2,
    flipped: false, // adds 180° about X — for y-down gaussian PLY captures
    visual: {
      // ⚠ 2026-07-17 ROOT CAUSE FOUND for "mesh and splat sizes don't
      // match": it isn't a registration/scale bug — the gaussian splat
      // file (SuperSplat "Cleaned" export, MGstudio_SmallRoom_Cleaned.ply)
      // is a CROPPED subset of the room. Verified offline by comparing
      // axis-aligned bounding-box extents (0.5-99.5 percentile, to ignore
      // stray points) after rotating each cloud into the mesh's frame:
      //   MGstudio_SmallRoom_Cleaned.ply vs GLB mesh → x: 61%, y: 46%,
      //   z: 43% of the mesh's size — non-uniform, so no single Scale
      //   value can fix it (tried; a 0.90 "fix" was a false lead from a
      //   shallow ICP minimum and has been reverted).
      //   MGstudio_SmallRoom.ply (the RAW, uncleaned Polycam point cloud,
      //   1.97M pts, xyz+RGB, no gaussian attrs) vs the same mesh → x/y/z
      //   all within 1.4% — near-perfect size match, because this is the
      //   file the mesh was itself reconstructed from. SuperSplat's
      //   cleanup step is what shrank the "Cleaned" export.
      // So: using the raw point cloud here (not the Cleaned gaussian
      // file) is the actual fix for the size mismatch, at the cost of
      // per-vertex-color dots instead of true gaussian shading. To get
      // gaussian splats back AND have them match the mesh, re-export
      // from SuperSplat without cropping the room bounds.
      // mode:
      //  "auto"   → try Spark first, fall back to three.js Points if the
      //             file can't be shown as splats (parse error or all
      //             splats transparent, like Tree.spz was)
      //  "points" → skip Spark, load as THREE.Points directly
      // Other files here for reference:
      //  - "/splats/MGstudio_SmallRoom_Cleaned.ply" — real gaussian splats
      //    (883K, SH3) but cropped to ~45-60% of the room, see above.
      //  - *.spz here are SPZ v4 — unsupported by Spark 2.1 (guarded).
      url: "/splats/MGstudio_SmallRoom.ply",
      mode: "auto",
      pointSize: 0.012, // metres, for the Points fallback
      lod: false, // Spark LOD unreliable on desktop dev (2026-07-15 finding)
      // Registration presets, PER FILE (solved by trimmed similarity ICP —
      // rotation + translation + uniform scale; intrinsic-XYZ degrees).
      // On startup the preset matching the url's filename is copied into
      // `offset`, so switching url automatically applies the right
      // registration. Unlisted files fall back to `offset` as-is.
      presets: {
        // Polycam point cloud: z-up, 1:1 scale (residual 1.5 cm)
        "MGstudio_SmallRoom.ply": {
          position: [0, 0, 0], rotationDeg: [-90, 0, 0], scale: 1,
        },
        // SuperSplat cleaned gaussian export: y-down AND 20% shrunk by the
        // cleanup — needs scale 1.2015 (residual 4.2 cm; it's a crop, so
        // only ~half the room is covered)
        "MGstudio_SmallRoom_Cleaned.ply": {
          position: [-0.223, -0.966, -0.064],
          rotationDeg: [179.9, 1.6, -0.6],
          scale: 1.2015,
        },
      },
      // Active registration (auto-filled from presets; sliders edit this).
      offset: {
        position: [0, 0, 0],
        rotationDeg: [-90, 0, 0],
        scale: 1,
      },
    },
    mesh: {
      // Real triangle mesh (135K tris, textured) — the Unity Mesh Collider.
      // Invisible by default; M shows the TEXTURED mesh for alignment checks.
      url: "/models/MGstudio_SmallRoom.glb",
      visible: false,
    },
    // Fallback only: used when the GLB has no triangles (e.g. the old
    // point-cloud Tree.glb). With a real mesh these are ignored.
    colliders: {
      ground: { y: 0, size: 200 },
      cylinders: [],
    },
  },
  avatar: {
    // three.js example character (Mixamo "Soldier": Idle/Walk/Run clips).
    url: "https://raw.githubusercontent.com/mrdoob/three.js/r180/examples/models/gltf/Soldier.glb",
    position: [0, 0, 1], // inside the room
    scale: 1,
    facingOffset: Math.PI, // Soldier's rest pose faces -Z
  },
  player: {
    eyeHeight: 1.6,
    speed: 5, // first-person fly speed
    sprintMultiplier: 4,
    start: [0, 0, 3], // inside the room, looking toward its centre
    thirdPerson: { distance: 3, walkSpeed: 2, runSpeed: 5 },
  },
  view: "third", // starting view mode: "first" | "third"
  minimap: { extent: 8 }, // world metres from map centre to edge
};

const isTyping = () => {
  const el = document.activeElement;
  return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);
};

function applyEnvironmentRootTransform(root, env) {
  root.position.set(...env.position);
  const d = THREE.MathUtils.degToRad;
  const [rx, ry, rz] = env.rotationDeg;
  root.rotation.set(d(rx) + (env.flipped ? Math.PI : 0), d(ry), d(rz));
  root.scale.setScalar(env.scale);
}

function init() {
  const ui = createUI({ title: "Spark WebXR Research" });
  const minimap = createMinimap({
    mount: ui.minimapMount,
    extent: CONFIG.minimap.extent,
  });

  const { renderer, scene, camera } = createScene({
    container: document.querySelector("#app"),
  });

  const player = createPlayer({
    camera,
    domElement: renderer.domElement,
    ...CONFIG.player,
  });
  player.rig.position.set(...CONFIG.player.start);
  scene.add(player.rig);

  player.controls.addEventListener("lock", () => ui.setHelpVisible(false));
  player.controls.addEventListener("unlock", () => ui.setHelpVisible(true));

  // Combined status line for the async loads.
  const status = { visual: "loading…", model: "loading…", avatar: "loading…" };
  function refreshStatus() {
    ui.setStatus(
      `Visual: ${status.visual} · Collider: ${status.model} · Avatar: ${status.avatar}`
    );
  }
  refreshStatus();

  // --- Environment root: visual + collider siblings ---
  const environmentRoot = new THREE.Group();
  environmentRoot.name = "EnvironmentRoot";
  applyEnvironmentRootTransform(environmentRoot, CONFIG.environment);
  scene.add(environmentRoot);

  const env = CONFIG.environment;

  // Apply the per-file registration preset for the active visual url
  // (deep-copied so slider edits don't mutate the preset itself).
  {
    const base = env.visual.url.split("/").pop();
    const preset = env.visual.presets?.[base];
    if (preset) {
      env.visual.offset = {
        position: [...preset.position],
        rotationDeg: [...preset.rotationDeg],
        scale: preset.scale ?? 1,
      };
    }
  }

  // --- Annotations: name tags on scene objects (splat-analyzer-plan.md) ---
  // sceneName = collider GLB basename without extension (plan §1).
  const sceneName = env.mesh.url.split("/").pop().replace(/\.[^/.]+$/, "");
  const annotations = createAnnotations({
    scene,
    environmentRoot,
    camera,
    domElement: renderer.domElement,
    sceneName,
    ui,
    // Lazily-resolved: visualObject/rawPlyObject are reassigned by the
    // async loaders below, declared further down but already `let`-bound
    // by the time tag mode can actually be used (this closure only runs
    // on a later click, never during this synchronous init() body).
    getPointCloud: () =>
      (visualObject?.isPoints ? visualObject : rawPlyObject?.isPoints ? rawPlyObject : null),
  });
  annotations.load();

  // --- Visual layer: Spark splat, with automatic Points fallback ---
  let visualObject = null; // whichever object ended up in the scene
  let sparkSplat = null;   // set only when the Spark path succeeded
  let rawPlyObject = null; // raw PLY loaded as plain THREE.Points (debug, L key)
  let rawPlyLoading = false;

  // Align the visual to the collider mesh (offset config is in degrees).
  // Applies to whichever visual is currently active (Spark splat or the
  // raw PLY point-cloud debug layer) plus the raw PLY layer if loaded,
  // since both come from the same source file and share one registration.
  function applyVisualOffset() {
    const o = env.visual.offset;
    const s = o.scale ?? 1;
    for (const obj of [visualObject, rawPlyObject]) {
      if (!obj) continue;
      obj.position.set(...o.position);
      obj.rotation.set(
        THREE.MathUtils.degToRad(o.rotationDeg[0]),
        THREE.MathUtils.degToRad(o.rotationDeg[1]),
        THREE.MathUtils.degToRad(o.rotationDeg[2])
      );
      obj.scale.setScalar(s);
    }
  }

  function loadPointsFallback(reason) {
    if (reason) console.warn("[visual] Spark path abandoned:", reason);
    status.visual = "loading point cloud…";
    refreshStatus();
    loadPointCloud({ url: env.visual.url, pointSize: env.visual.pointSize })
      .then(({ object, count }) => {
        environmentRoot.add(object);
        visualObject = object;
        applyVisualOffset();
        status.visual = `${count.toLocaleString()} points`;
        refreshStatus();
      })
      .catch((err) => {
        console.error("Point cloud loading failed:", err);
        status.visual = "failed (see console)";
        refreshStatus();
      });
  }

  /**
   * Debug layer: load the visual PLY as a plain THREE.Points cloud —
   * bypassing Spark's gaussian rendering entirely (no opacity/SH shading,
   * just raw xyz dots). Useful because Spark's shading can make subtle
   * visual↔mesh misalignment hard to judge by eye; raw points give a
   * cleaner geometric silhouette to compare against the mesh (M).
   * Lazy: only fetched the first time it's toggled on (L key or the
   * Layers checkbox), since the PLY here is 200MB+.
   */
  function loadRawPly() {
    if (rawPlyObject || rawPlyLoading) return;
    rawPlyLoading = true;
    status.visual = `${status.visual} · raw PLY loading…`;
    refreshStatus();
    loadPointCloud({ url: env.visual.url, pointSize: env.visual.pointSize })
      .then(({ object, count }) => {
        environmentRoot.add(object);
        rawPlyObject = object;
        rawPlyLoading = false;
        applyVisualOffset();
        ui.setControl("layer-ply", true);
        ui.addMessage("System", `Raw PLY loaded: ${count.toLocaleString()} points.`);
        refreshStatus();
      })
      .catch((err) => {
        console.error("Raw PLY loading failed:", err);
        rawPlyLoading = false;
        ui.addMessage("System", "Raw PLY load failed (see console).");
        ui.setControl("layer-ply", false);
      });
  }

  /** First network chunk of a file (≫ any header size). */
  async function readFirstChunk(url) {
    try {
      const res = await fetch(url);
      const reader = res.body.getReader();
      const { value } = await reader.read();
      reader.cancel();
      return value ?? null;
    } catch {
      return null;
    }
  }

  /**
   * Classify a PLY by its header. Feeding a huge non-gaussian PLY to
   * Spark can freeze/crash the tab, so plain point clouds are routed
   * straight to the Points path.
   */
  function classifyPly(bytes) {
    const text = new TextDecoder("latin1").decode(bytes);
    if (!text.startsWith("ply")) return "not-ply";
    const end = text.indexOf("end_header");
    const header = end > 0 ? text.slice(0, end) : text;
    if (/property\s+\S+\s+(opacity|scale_0|rot_0|f_dc_0)/.test(header)) return "gaussian";
    if (/element\s+face\s+[1-9]/.test(header)) return "mesh";
    return "points";
  }

  async function startVisual() {
    if (env.visual.mode === "points") return loadPointsFallback();

    if (/\.ply(\?|$)/i.test(env.visual.url)) {
      const head = await readFirstChunk(env.visual.url);
      if (head) {
        const kind = classifyPly(head);
        if (kind === "points" || kind === "mesh") {
          return loadPointsFallback(`PLY has no gaussian attributes (kind: ${kind})`);
        }
      }
    }

    if (/\.spz(\?|$)/i.test(env.visual.url)) {
      const head = await readFirstChunk(env.visual.url);
      // SPZ v4 (2026-05) has a PLAINTEXT header: "NGSP" magic + uint32
      // version. Spark 2.1.0 hangs on v4 — fail loudly instead of
      // freezing the tab. (Legacy v1–v3 spz are fully gzip-wrapped, so
      // their first bytes are 1f 8b, not "NGSP".)
      if (head && head.length >= 8 &&
          head[0] === 0x4e && head[1] === 0x47 && head[2] === 0x53 && head[3] === 0x50) {
        const version = head[4] | (head[5] << 8) | (head[6] << 16) | (head[7] << 24);
        if (version >= 4) {
          status.visual = `SPZ v${version} — unsupported by Spark 2.1 (convert to v2)`;
          refreshStatus();
          console.error(
            `[visual] ${env.visual.url} is SPZ v${version}; Spark 2.1.0 only ` +
            `decodes v1–v3. Down-convert (Niantic SPZ Converter) or re-export.`
          );
          return;
        }
      }
    }

    loadSparkVisual();
  }

  function loadSparkVisual() {
    const splat = loadSplat({
      url: env.visual.url,
      lod: env.visual.lod,
      flipped: false, // flip is handled on EnvironmentRoot
      position: [0, 0, 0],
      scale: 1,
      onProgress: (e) => {
        if (e.lengthComputable) {
          status.visual = `${Math.round((100 * e.loaded) / e.total)}%`;
          refreshStatus();
        }
      },
    });
    environmentRoot.add(splat);

    splat.initialized
      .then(() => {
        // Hollow exports (like the old Tree.spz) decode with opacity 0 on
        // every splat — detect that and switch to the Points fallback.
        let seen = 0;
        let visible = 0;
        splat.forEachSplat((i, c, s, q, opacity) => {
          if (seen < 5000) {
            seen += 1;
            if (opacity > 0) visible += 1;
          }
        });
        if (splat.numSplats === 0 || visible === 0) {
          environmentRoot.remove(splat);
          splat.dispose?.();
          loadPointsFallback("splats decode as fully transparent");
        } else {
          visualObject = splat;
          sparkSplat = splat;
          applyVisualOffset();
          status.visual = `${splat.numSplats.toLocaleString()} splats`;
          refreshStatus();
        }
      })
      .catch((err) => {
        environmentRoot.remove(splat);
        loadPointsFallback(err?.message ?? "parse error");
      });
  }

  startVisual();

  // --- Collider layer: textured GLB mesh (invisible; M shows it) ---
  let model = null;
  let colliderGizmo = null; // primitive colliders group (fallback only)
  let roomRect = null; // measured footprint for the minimap marker
  loadModel({
    url: env.mesh.url,
    position: [0, 0, 0],
    rotation: [0, 0, 0],
    scale: 1,
    visible: true,
  })
    .then((loaded) => {
      model = loaded;
      model.object.visible = env.mesh.visible;
      environmentRoot.add(model.object);
      environmentRoot.updateMatrixWorld(true);

      // Minimap marker: measured world-space footprint of the mesh.
      const box = new THREE.Box3().setFromObject(model.object);
      const size = new THREE.Vector3();
      const center = new THREE.Vector3();
      box.getSize(size);
      box.getCenter(center);
      roomRect = { x: center.x, z: center.z, w: size.x, d: size.z };

      let collider = createCollider(model.object);
      if (collider.meshCount > 0) {
        status.model = `mesh (${(size.x).toFixed(1)}×${(size.z).toFixed(1)}m)`;
      } else {
        colliderGizmo = createPrimitiveColliders(env.colliders);
        scene.add(colliderGizmo);
        colliderGizmo.updateMatrixWorld(true);
        collider = createCollider(colliderGizmo);
        status.model = "point cloud → primitive collider";
      }
      player.setCollider(collider);
      annotations.setCollider(collider);
      refreshStatus();
    })
    .catch((err) => {
      console.error("Collider GLB loading failed:", err);
      status.model = "failed (see console)";
      refreshStatus();
    });

  // --- Avatar (third-person character) ---
  let avatar = null;
  loadAvatar(CONFIG.avatar)
    .then((loaded) => {
      avatar = loaded;
      scene.add(avatar.object);
      avatar.setAnimation("idle");
      player.setAvatar(avatar);
      status.avatar = "loaded";
      refreshStatus();
      ui.setViewMode(player.setMode(CONFIG.view));
    })
    .catch((err) => {
      console.error("Avatar loading failed:", err);
      status.avatar = "failed (see console)";
      refreshStatus();
    });

  // --- View mode UI ---
  ui.setViewMode(player.mode);
  ui.onViewChange((mode) => ui.setViewMode(player.setMode(mode)));
  player.onModeChange((mode) => ui.setViewMode(mode));

  // --- Layer visibility (panel checkboxes ↔ M/N hotkeys) ---
  function setVisualVisible(visible) {
    if (visualObject) {
      visualObject.visible = visible;
      if (visualObject === sparkSplat) visualObject.opacity = visible ? 1 : 0;
    }
    ui.setControl("layer-visual", visible);
  }
  function setMeshVisible(visible) {
    if (model) model.object.visible = visible;
    if (colliderGizmo) colliderGizmo.visible = visible;
    ui.setControl("layer-mesh", visible);
  }
  function setRawPlyVisible(visible) {
    // If the main visual already IS the raw point cloud (env.visual.url
    // has no gaussian attributes, so it fell back to Points already —
    // true by default now that visual.url points at the raw scan), don't
    // fetch a redundant second copy of a 50MB+ file — just alias to N.
    if (visualObject && visualObject !== sparkSplat) {
      setVisualVisible(visible);
      ui.setControl("layer-ply", visible);
      return;
    }
    if (visible && !rawPlyObject) {
      loadRawPly(); // async; will show once loaded
    } else if (rawPlyObject) {
      rawPlyObject.visible = visible;
    }
    ui.setControl("layer-ply", visible);
  }
  function setTagsVisible(visible) {
    annotations.setVisible(visible);
    ui.setControl("layer-tags", visible);
  }
  function setBBoxVisible(visible) {
    annotations.setBBoxVisible(visible);
    ui.setControl("layer-bbox", visible);
  }

  // --- Control panel (Controls tab) ---
  function applyEnv() {
    applyEnvironmentRootTransform(environmentRoot, env);
  }
  ui.buildControls([
    // Environment moves the WHOLE scene: splat visual + collider mesh move
    // together, and collision follows automatically (raycasts read live
    // matrixWorld). This is the section to use for placing/scaling.
    { type: "section", label: "Environment — splat + collider together" },
    { type: "slider", id: "env-x", label: "Pos X", min: -20, max: 20, step: 0.01,
      value: env.position[0], onChange: (v) => { env.position[0] = v; applyEnv(); } },
    { type: "slider", id: "env-y", label: "Pos Y", min: -10, max: 10, step: 0.01,
      value: env.position[1], onChange: (v) => { env.position[1] = v; applyEnv(); } },
    { type: "slider", id: "env-z", label: "Pos Z", min: -20, max: 20, step: 0.01,
      value: env.position[2], onChange: (v) => { env.position[2] = v; applyEnv(); } },
    { type: "slider", id: "env-rotx", label: "Rot X°", min: -180, max: 180, step: 0.5,
      value: env.rotationDeg[0], format: (v) => `${(+v).toFixed(1)}°`,
      onChange: (v) => { env.rotationDeg[0] = v; applyEnv(); } },
    { type: "slider", id: "env-roty", label: "Rot Y°", min: -180, max: 180, step: 0.5,
      value: env.rotationDeg[1], format: (v) => `${(+v).toFixed(1)}°`,
      onChange: (v) => { env.rotationDeg[1] = v; applyEnv(); } },
    { type: "slider", id: "env-rotz", label: "Rot Z°", min: -180, max: 180, step: 0.5,
      value: env.rotationDeg[2], format: (v) => `${(+v).toFixed(1)}°`,
      onChange: (v) => { env.rotationDeg[2] = v; applyEnv(); } },
    { type: "slider", id: "env-scale", label: "Scale", min: 0.1, max: 10, step: 0.05,
      value: env.scale, onChange: (v) => { env.scale = v; applyEnv(); } },
    // Registration nudges ONLY the splat relative to the collider — use it
    // solely to fix visual↔collision mismatch (pre-solved by ICP above).
    { type: "section", label: "Registration — splat only (advanced)" },
    { type: "slider", id: "off-rx", label: "Rot X°", min: -180, max: 180, step: 0.5,
      value: env.visual.offset.rotationDeg[0], format: (v) => `${(+v).toFixed(1)}°`,
      onChange: (v) => { env.visual.offset.rotationDeg[0] = v; applyVisualOffset(); } },
    { type: "slider", id: "off-ry", label: "Rot Y°", min: -180, max: 180, step: 0.5,
      value: env.visual.offset.rotationDeg[1], format: (v) => `${(+v).toFixed(1)}°`,
      onChange: (v) => { env.visual.offset.rotationDeg[1] = v; applyVisualOffset(); } },
    { type: "slider", id: "off-rz", label: "Rot Z°", min: -180, max: 180, step: 0.5,
      value: env.visual.offset.rotationDeg[2], format: (v) => `${(+v).toFixed(1)}°`,
      onChange: (v) => { env.visual.offset.rotationDeg[2] = v; applyVisualOffset(); } },
    { type: "slider", id: "off-x", label: "Offset X", min: -3, max: 3, step: 0.01,
      value: env.visual.offset.position[0],
      onChange: (v) => { env.visual.offset.position[0] = v; applyVisualOffset(); } },
    { type: "slider", id: "off-y", label: "Offset Y", min: -3, max: 3, step: 0.01,
      value: env.visual.offset.position[1],
      onChange: (v) => { env.visual.offset.position[1] = v; applyVisualOffset(); } },
    { type: "slider", id: "off-z", label: "Offset Z", min: -3, max: 3, step: 0.01,
      value: env.visual.offset.position[2],
      onChange: (v) => { env.visual.offset.position[2] = v; applyVisualOffset(); } },
    // Relative scale of the splat vs. the mesh collider. Added 2026-07-17:
    // the "Cleaned" PLY export isn't quite 1:1 metric with the GLB — a
    // scale sweep found a real minimum around 0.9. At this room's ~3-4m
    // half-extent, leaving this at 1.0 alone produces 30-40cm of
    // mismatch at the walls even when position/rotation look right near
    // the center, which is likely why alignment still looked off.
    { type: "slider", id: "off-scale", label: "Scale", min: 0.5, max: 1.5, step: 0.005,
      value: env.visual.offset.scale ?? 1,
      onChange: (v) => { env.visual.offset.scale = v; applyVisualOffset(); } },

    { type: "button", label: "Copy CONFIG values", onClick: async () => {
      const o = env.visual.offset;
      const snippet =
        `// environment\n` +
        `position: [${env.position.map((n) => +n.toFixed(2)).join(", ")}],\n` +
        `rotationDeg: [${env.rotationDeg.map((n) => +n.toFixed(1)).join(", ")}],\n` +
        `scale: ${+env.scale.toFixed(2)},\n` +
        `// visual.offset\n` +
        `offset: {\n` +
        `  position: [${o.position.map((n) => +n.toFixed(3)).join(", ")}],\n` +
        `  rotationDeg: [${o.rotationDeg.map((n) => +n.toFixed(1)).join(", ")}],\n` +
        `  scale: ${(+(o.scale ?? 1)).toFixed(3)},\n` +
        `},`;
      try {
        await navigator.clipboard.writeText(snippet);
        ui.addMessage("System", "Environment CONFIG copied to clipboard.");
      } catch {
        console.log("[CONFIG]\n" + snippet);
        ui.addMessage("System", "Clipboard failed — printed to console instead.");
      }
    } },

    { type: "section", label: "Layers" },
    { type: "checkbox", id: "layer-visual", label: "Scan visual (N)",
      value: true, onChange: setVisualVisible },
    { type: "checkbox", id: "layer-mesh", label: "Collider mesh (M)",
      value: env.mesh.visible, onChange: setMeshVisible },
    { type: "checkbox", id: "layer-ply", label: "Raw PLY points (L)",
      value: false, onChange: setRawPlyVisible },
    { type: "checkbox", id: "layer-tags", label: "Name tags (T)",
      value: true, onChange: setTagsVisible },
    { type: "checkbox", id: "layer-bbox", label: "Bounding boxes (B)",
      value: false, onChange: setBBoxVisible },

    { type: "section", label: "Annotations" },
    { type: "checkbox", id: "tag-mode", label: "Tag mode",
      value: false, onChange: (v) => {
        if (v) player.controls.unlock(); // clicks raycast instead of locking
        annotations.setTagMode(v);
      } },
    { type: "checkbox", id: "tag-hq", label: "HQ detect (flood fill)",
      value: true,
      onChange: (v) => annotations.setDetectQuality(v ? "high" : "low") },
    { type: "button", label: "Export JSON", onClick: () => annotations.exportJson() },
    { type: "button", label: "Save to disk (dev)", onClick: () => annotations.saveToDisk() },

    { type: "section", label: "Camera capture (AI eyes)" },
    { type: "checkbox", id: "cap-auto", label: "Auto capture (C)",
      value: false, onChange: (v) => {
        capture.setEnabled(v);
        ui.addMessage("System", v
          ? "Auto capture ON — a frame is stored every 2 s while the camera moves."
          : `Auto capture OFF — ${capture.count()} frame(s) in the buffer.`);
      } },
    { type: "slider", id: "cap-interval", label: "Interval", min: 0.5, max: 10, step: 0.5,
      value: 2, format: (v) => `${(+v).toFixed(1)}s`,
      onChange: (v) => capture.setInterval(v) },
    { type: "button", label: "Capture now", onClick: () => {
      // Grab on the next rendered frame so the canvas is fresh.
      requestAnimationFrame(() => {
        const f = capture.captureNow();
        ui.addMessage("System", f
          ? `Captured frame #${f.id} (${capture.count()} buffered).`
          : "Capture failed — canvas not ready.");
      });
    } },
    { type: "button", label: "View captures (G)", onClick: () => {
      ui.showCaptureGallery(capture.getFrames());
    } },
    { type: "button", label: "Export frames JSON", onClick: () => {
      if (capture.count() === 0) return ui.addMessage("System", "No frames to export.");
      capture.exportJson("MGstudio_SmallRoom");
      ui.addMessage("System", `Exported ${capture.count()} frame(s).`);
    } },
    { type: "button", label: "Clear frames", onClick: () => {
      capture.clear();
      ui.addMessage("System", "Capture buffer cleared.");
    } },

    { type: "section", label: "Player" },
    { type: "slider", id: "p-walk", label: "Walk", min: 0.5, max: 10, step: 0.1,
      value: CONFIG.player.thirdPerson.walkSpeed,
      onChange: (v) => player.setTuning({ walkSpeed: v }) },
    { type: "slider", id: "p-run", label: "Run", min: 1, max: 20, step: 0.5,
      value: CONFIG.player.thirdPerson.runSpeed,
      onChange: (v) => player.setTuning({ runSpeed: v }) },
    { type: "slider", id: "p-dist", label: "Camera", min: 1, max: 12, step: 0.1,
      value: CONFIG.player.thirdPerson.distance,
      onChange: (v) => player.setTuning({ distance: v }) },
    { type: "slider", id: "p-fly", label: "Fly (1st)", min: 1, max: 30, step: 0.5,
      value: CONFIG.player.speed,
      onChange: (v) => player.setTuning({ flySpeed: v }) },

    { type: "section", label: "Minimap" },
    { type: "slider", id: "map-extent", label: "Extent", min: 5, max: 100, step: 1,
      value: CONFIG.minimap.extent, format: (v) => `${Math.round(v)}m`,
      onChange: (v) => minimap.setExtent(v) },
    { type: "button", label: "Clear trail", onClick: () => minimap.clearTrail() },
  ]);

  // --- World-state API (splat-analyzer-plan.md Phase B — the AI bridge) ---
  const worldState = createWorldState({
    annotations,
    player,
    camera,
    getAvatar: () => avatar,
  });
  window.__world = worldState; // debug, per plan §3

  // --- Camera capture (the "eyes" feed for the future vision AI) ---
  // Every 2 s (while the camera actually moves) the rendered frame is
  // stored with its full pose. The agent, once connected, consumes this
  // via capture.onFrame(f => …) or capture.getFrames() — see capture.js.
  const capture = createCapture({
    renderer,
    camera,
    scene,
    interval: 2,
    // Frames are ALWAYS first-person (the avatar's eyes) — in third-person
    // mode the scene is re-rendered from the avatar's head for the capture
    // (body hidden), then the on-screen view is restored. In first person
    // the main camera already is the eye, so no re-render is needed.
    getEgoView: () => {
      if (player.mode === "first" || !avatar) return null;
      const a = avatar.object.position;
      return {
        position: [a.x, a.y + CONFIG.player.eyeHeight, a.z],
        heading: avatar.object.rotation.y - (avatar.facingOffset ?? 0),
        hide: [avatar.object],
      };
    },
    getPlayerState: () => ({
      mode: player.mode,
      playerPosition: player.rig.position.toArray().map((n) => +n.toFixed(2)),
      avatarPosition: avatar ? avatar.object.position.toArray().map((n) => +n.toFixed(2)) : null,
      heading: avatar ? +(avatar.object.rotation.y - (avatar.facingOffset ?? 0)).toFixed(3) : 0,
    }),
  });
  window.__capture = capture; // debug / future agent hookup

  // --- Chat (future agent.js hook, plan step 9) ---
  ui.onSubmit((text) => {
    // Tag mode (annotations.js) hijacks the next chat submission as a
    // label for the point just clicked — empty submit cancels it.
    if (annotations.isAwaitingLabel()) {
      annotations.submitLabel(text);
      return;
    }
    if (!text) return;
    ui.addMessage("Me", text);
    if (text.startsWith("/where")) {
      const query = text.slice(6).trim();
      const obj = query ? worldState.findObject(query) : null;
      ui.addMessage(
        "System",
        obj
          ? `${obj.label} (${obj.id}): ${obj.distanceFromPlayer.toFixed(1)}m away at ` +
            `(${obj.position[0].toFixed(1)}, ${obj.position[1].toFixed(1)}, ${obj.position[2].toFixed(1)}).`
          : query
            ? `No object matching "${query}".`
            : "Usage: /where <name>"
      );
      return;
    }
    ui.addMessage("System", "(agent not connected yet) Message received.");
  });

  // --- Hotkeys ---
  window.addEventListener("keydown", (e) => {
    if (isTyping()) return;
    if (e.code === "KeyV") ui.setViewMode(player.setMode(player.mode === "third" ? "first" : "third"));
    if (e.code === "KeyM") setMeshVisible(!(model?.object.visible || colliderGizmo?.visible));
    if (e.code === "KeyN") setVisualVisible(!(visualObject?.visible ?? true));
    if (e.code === "KeyL") setRawPlyVisible(!(rawPlyObject?.visible ?? false));
    if (e.code === "KeyT") setTagsVisible(!annotations.isVisible());
    if (e.code === "KeyB") setBBoxVisible(!annotations.isBBoxVisible());
    if (e.code === "KeyP") {
      const p = player.rig.position;
      const snippet = `start: [${p.x.toFixed(1)}, ${p.y.toFixed(1)}, ${p.z.toFixed(1)}],`;
      console.log("[spawn point] paste into CONFIG.player:", snippet);
      ui.addMessage("System", `Spawn point: ${snippet} (also in console)`);
    }
    if (e.code === "KeyG") {
      ui.showCaptureGallery(capture.getFrames());
    }
    if (e.code === "KeyC") {
      const next = !capture.isEnabled();
      capture.setEnabled(next);
      ui.setControl("cap-auto", next);
      ui.addMessage("System", next
        ? "Auto capture ON — a frame is stored every 2 s while the camera moves."
        : `Auto capture OFF — ${capture.count()} frame(s) in the buffer.`);
    }
    if (e.code === "Enter") {
      player.controls.unlock();
      ui.focusInput();
      e.preventDefault();
    }
  });

  // Debug handle for the browser console (research convenience).
  window.__research = {
    THREE, scene, camera, renderer, environmentRoot, player, minimap, annotations, worldState, capture,
    get visual() { return visualObject; },
    get rawPly() { return rawPlyObject; },
    get model() { return model; },
    get avatar() { return avatar; },
  };

  // "Splat kick" — Spark 2.1 quirk: freshly loaded splats can stay
  // invisible with a static camera. Only relevant on the Spark path.
  let kickFrames = 900;

  // --- Render loop ---
  const clock = new THREE.Clock();
  const camWorld = new THREE.Vector3();
  renderer.setAnimationLoop(() => {
    const delta = Math.min(clock.getDelta(), 0.05); // clamp tab-switch spikes
    player.update(delta);
    avatar?.update(delta);

    if (kickFrames > 0 && sparkSplat?.isInitialized) {
      if (kickFrames % 30 === 0) sparkSplat.position.x += 1e-4;
      kickFrames -= 1;
    }

    camera.getWorldPosition(camWorld);
    ui.setPosition(camWorld.x, camWorld.y, camWorld.z);

    annotations.update(delta, camWorld);

    minimap.update({
      avatarX: avatar?.object.position.x,
      avatarZ: avatar?.object.position.z,
      heading: avatar ? avatar.object.rotation.y - (avatar.facingOffset ?? 0) : 0,
      camX: camWorld.x,
      camZ: camWorld.z,
      environment: roomRect ? { x: roomRect.x, z: roomRect.z, rect: roomRect } : null,
      annotations: annotations.isVisible()
        ? annotations.getObjects().map((o) => ({ x: o.worldPosition.x, z: o.worldPosition.z, label: o.label }))
        : [],
    });

    renderer.render(scene, camera);

    // Must run AFTER render — the WebGL canvas is only readable in the
    // same tick (preserveDrawingBuffer is false).
    capture.afterRender(delta);
  });
}

init();
