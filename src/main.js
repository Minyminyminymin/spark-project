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
 *  - The 컨트롤 panel adjusts the environment LIVE; "CONFIG 복사" copies
 *    the current values to paste back into this file.
 *  - Or fly somewhere in 1인칭 and press P for a spawn-point snippet.
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

const CONFIG = {
  // Everything under EnvironmentRoot: visual (PLY/SPZ) + collider (GLB)
  // stay aligned as siblings. Tweak live in the 컨트롤 panel, copy back.
  environment: {
    // Polycam exports are y-up, real-world metres, origin at scan centre.
    // Floor sits at y = -1.41 × scale (measured), so position Y = 1.41 × scale
    // puts the floor at world y = 0.  (scale 1.5 → 2.11)
    position: [0, 2.11, 0],
    // ⚠ DEGREES, not radians! (previous radian field caused a 233° tilt
    // when 180 was entered as if degrees). The mesh is already level
    // (measured tilt: 0.84°) and the PLY offset below handles axis
    // conventions — this should normally stay [0, 0, 0].
    rotationDeg: [0, 0, 0],
    scale: 1.5,
    flipped: false, // adds 180° about X — for y-down gaussian PLY captures
    visual: {
      // MGstudio_SmallRoom.ply is a Polycam POINT CLOUD (xyz+RGB, no
      // gaussian attributes). mode:
      //  "auto"   → try Spark first, fall back to three.js Points if the
      //             file can't be shown as splats (parse error or all
      //             splats transparent, like Tree.spz was)
      //  "points" → skip Spark, load as THREE.Points directly
      url: "/splats/MGstudio_SmallRoom.ply",
      mode: "auto",
      pointSize: 0.012, // metres, for the Points fallback
      lod: false, // Spark LOD unreliable on desktop dev (2026-07-15 finding)
      // Local offset of the VISUAL relative to the collider mesh, in
      // human-friendly DEGREES (converted to radians internally).
      // Polycam's PLY point cloud is Z-up while its GLB is Y-up — ICP
      // registration (2026-07-16) confirmed exactly -90° about X,
      // zero translation, residual 2.3 cm. Fine-tune in the 정렬 section
      // of the 컨트롤 panel.
      offset: {
        position: [0, 0, 0],
        rotationDeg: [-90, 0, 0],
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

  // --- Visual layer: Spark splat, with automatic Points fallback ---
  let visualObject = null; // whichever object ended up in the scene
  let sparkSplat = null;   // set only when the Spark path succeeded

  // Align the visual to the collider mesh (offset config is in degrees).
  function applyVisualOffset() {
    if (!visualObject) return;
    const o = env.visual.offset;
    visualObject.position.set(...o.position);
    visualObject.rotation.set(
      THREE.MathUtils.degToRad(o.rotationDeg[0]),
      THREE.MathUtils.degToRad(o.rotationDeg[1]),
      THREE.MathUtils.degToRad(o.rotationDeg[2])
    );
  }

  function loadPointsFallback(reason) {
    if (reason) console.warn("[visual] Spark path abandoned:", reason);
    status.visual = "point cloud 로딩…";
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
   * Read only the PLY header (first network chunk) and classify the file.
   * Feeding a huge non-gaussian PLY to Spark can freeze/crash the tab, so
   * we route plain point clouds straight to the Points path.
   */
  async function detectPlyKind(url) {
    try {
      const res = await fetch(url);
      const reader = res.body.getReader();
      const { value } = await reader.read(); // first chunk ≫ header size
      reader.cancel();
      const text = new TextDecoder("latin1").decode(value);
      if (!text.startsWith("ply")) return "not-ply";
      const end = text.indexOf("end_header");
      const header = end > 0 ? text.slice(0, end) : text;
      if (/property\s+\S+\s+(opacity|scale_0|rot_0|f_dc_0)/.test(header)) return "gaussian";
      if (/element\s+face\s+[1-9]/.test(header)) return "mesh";
      return "points";
    } catch {
      return "unknown";
    }
  }

  async function startVisual() {
    if (env.visual.mode === "points") return loadPointsFallback();
    if (/\.ply(\?|$)/i.test(env.visual.url)) {
      const kind = await detectPlyKind(env.visual.url);
      if (kind === "points" || kind === "mesh") {
        return loadPointsFallback(`PLY has no gaussian attributes (kind: ${kind})`);
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

  // --- Control panel (컨트롤 tab) ---
  function applyEnv() {
    applyEnvironmentRootTransform(environmentRoot, env);
  }
  ui.buildControls([
    { type: "section", label: "환경 (EnvironmentRoot)" },
    { type: "slider", id: "env-x", label: "위치 X", min: -20, max: 20, step: 0.01,
      value: env.position[0], onChange: (v) => { env.position[0] = v; applyEnv(); } },
    { type: "slider", id: "env-y", label: "위치 Y", min: -10, max: 10, step: 0.01,
      value: env.position[1], onChange: (v) => { env.position[1] = v; applyEnv(); } },
    { type: "slider", id: "env-z", label: "위치 Z", min: -20, max: 20, step: 0.01,
      value: env.position[2], onChange: (v) => { env.position[2] = v; applyEnv(); } },
    { type: "slider", id: "env-rotx", label: "회전 X°", min: -180, max: 180, step: 0.5,
      value: env.rotationDeg[0], format: (v) => `${(+v).toFixed(1)}°`,
      onChange: (v) => { env.rotationDeg[0] = v; applyEnv(); } },
    { type: "slider", id: "env-roty", label: "회전 Y°", min: -180, max: 180, step: 0.5,
      value: env.rotationDeg[1], format: (v) => `${(+v).toFixed(1)}°`,
      onChange: (v) => { env.rotationDeg[1] = v; applyEnv(); } },
    { type: "slider", id: "env-rotz", label: "회전 Z°", min: -180, max: 180, step: 0.5,
      value: env.rotationDeg[2], format: (v) => `${(+v).toFixed(1)}°`,
      onChange: (v) => { env.rotationDeg[2] = v; applyEnv(); } },
    { type: "slider", id: "env-scale", label: "스케일", min: 0.1, max: 10, step: 0.05,
      value: env.scale, onChange: (v) => { env.scale = v; applyEnv(); } },
    { type: "section", label: "정렬 — 시각화 ↔ 메시 (도 단위)" },
    { type: "slider", id: "off-rx", label: "회전 X°", min: -180, max: 180, step: 0.5,
      value: env.visual.offset.rotationDeg[0], format: (v) => `${(+v).toFixed(1)}°`,
      onChange: (v) => { env.visual.offset.rotationDeg[0] = v; applyVisualOffset(); } },
    { type: "slider", id: "off-ry", label: "회전 Y°", min: -180, max: 180, step: 0.5,
      value: env.visual.offset.rotationDeg[1], format: (v) => `${(+v).toFixed(1)}°`,
      onChange: (v) => { env.visual.offset.rotationDeg[1] = v; applyVisualOffset(); } },
    { type: "slider", id: "off-rz", label: "회전 Z°", min: -180, max: 180, step: 0.5,
      value: env.visual.offset.rotationDeg[2], format: (v) => `${(+v).toFixed(1)}°`,
      onChange: (v) => { env.visual.offset.rotationDeg[2] = v; applyVisualOffset(); } },
    { type: "slider", id: "off-x", label: "이동 X", min: -3, max: 3, step: 0.01,
      value: env.visual.offset.position[0],
      onChange: (v) => { env.visual.offset.position[0] = v; applyVisualOffset(); } },
    { type: "slider", id: "off-y", label: "이동 Y", min: -3, max: 3, step: 0.01,
      value: env.visual.offset.position[1],
      onChange: (v) => { env.visual.offset.position[1] = v; applyVisualOffset(); } },
    { type: "slider", id: "off-z", label: "이동 Z", min: -3, max: 3, step: 0.01,
      value: env.visual.offset.position[2],
      onChange: (v) => { env.visual.offset.position[2] = v; applyVisualOffset(); } },

    { type: "button", label: "CONFIG 좌표 복사", onClick: async () => {
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
        `},`;
      try {
        await navigator.clipboard.writeText(snippet);
        ui.addMessage("시스템", "환경 CONFIG가 클립보드에 복사됐어요.");
      } catch {
        console.log("[CONFIG]\n" + snippet);
        ui.addMessage("시스템", "클립보드 실패 — 콘솔에 출력했어요.");
      }
    } },

    { type: "section", label: "레이어" },
    { type: "checkbox", id: "layer-visual", label: "스캔 시각화 (N)",
      value: true, onChange: setVisualVisible },
    { type: "checkbox", id: "layer-mesh", label: "콜라이더 메시 (M)",
      value: env.mesh.visible, onChange: setMeshVisible },

    { type: "section", label: "플레이어" },
    { type: "slider", id: "p-walk", label: "걷기", min: 0.5, max: 10, step: 0.1,
      value: CONFIG.player.thirdPerson.walkSpeed,
      onChange: (v) => player.setTuning({ walkSpeed: v }) },
    { type: "slider", id: "p-run", label: "달리기", min: 1, max: 20, step: 0.5,
      value: CONFIG.player.thirdPerson.runSpeed,
      onChange: (v) => player.setTuning({ runSpeed: v }) },
    { type: "slider", id: "p-dist", label: "카메라", min: 1, max: 12, step: 0.1,
      value: CONFIG.player.thirdPerson.distance,
      onChange: (v) => player.setTuning({ distance: v }) },
    { type: "slider", id: "p-fly", label: "비행(1인칭)", min: 1, max: 30, step: 0.5,
      value: CONFIG.player.speed,
      onChange: (v) => player.setTuning({ flySpeed: v }) },

    { type: "section", label: "미니맵" },
    { type: "slider", id: "map-extent", label: "범위 (m)", min: 5, max: 100, step: 1,
      value: CONFIG.minimap.extent, format: (v) => `${Math.round(v)}m`,
      onChange: (v) => minimap.setExtent(v) },
    { type: "button", label: "경로 지우기", onClick: () => minimap.clearTrail() },
  ]);

  // --- Chat (future agent.js hook, plan step 9) ---
  ui.onSubmit((text) => {
    ui.addMessage("나", text);
    ui.addMessage("시스템", "(에이전트 연결 전) 메시지를 받았어요.");
  });

  // --- Hotkeys ---
  window.addEventListener("keydown", (e) => {
    if (isTyping()) return;
    if (e.code === "KeyV") ui.setViewMode(player.setMode(player.mode === "third" ? "first" : "third"));
    if (e.code === "KeyM") setMeshVisible(!(model?.object.visible || colliderGizmo?.visible));
    if (e.code === "KeyN") setVisualVisible(!(visualObject?.visible ?? true));
    if (e.code === "KeyP") {
      const p = player.rig.position;
      const snippet = `start: [${p.x.toFixed(1)}, ${p.y.toFixed(1)}, ${p.z.toFixed(1)}],`;
      console.log("[spawn point] paste into CONFIG.player:", snippet);
      ui.addMessage("시스템", `스폰 좌표: ${snippet} (콘솔에도 출력됨)`);
    }
    if (e.code === "Enter") {
      player.controls.unlock();
      ui.focusInput();
      e.preventDefault();
    }
  });

  // Debug handle for the browser console (research convenience).
  window.__research = {
    THREE, scene, camera, renderer, environmentRoot, player, minimap,
    get visual() { return visualObject; },
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

    minimap.update({
      avatarX: avatar?.object.position.x,
      avatarZ: avatar?.object.position.z,
      heading: avatar ? avatar.object.rotation.y - (avatar.facingOffset ?? 0) : 0,
      camX: camWorld.x,
      camZ: camWorld.z,
      environment: roomRect ? { x: roomRect.x, z: roomRect.z, rect: roomRect } : null,
    });

    renderer.render(scene, camera);
  });
}

init();
