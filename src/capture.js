/**
 * capture.js — Periodic first/third-person camera captures.
 *
 * Every `interval` seconds (default 2) the rendered frame is downscaled
 * and stored as a JPEG data-URL together with the full camera pose and
 * player state. This is the "eyes" feed for a future vision AI: when the
 * agent is connected it can either subscribe live (`onFrame`) or pull
 * the recent history (`getFrames()`) and send it to a vision model to
 * recognize the environment.
 *
 * Design notes:
 *  - The WebGL canvas is created with preserveDrawingBuffer: false, so
 *    frames MUST be grabbed synchronously right after renderer.render()
 *    — hence `afterRender(delta)` is called from main.js's render loop.
 *  - By default a frame is only taken when the camera actually moved or
 *    rotated since the last capture (no point stacking identical images
 *    while the player stands still).
 *  - Ring buffer (default 30 frames ≈ last minute) keeps memory bounded:
 *    640px JPEG ≈ 40–80 KB per frame.
 */
import * as THREE from "three";

export function createCapture({
  renderer,
  camera,
  scene = null,        // required for ego-view re-render
  getPlayerState = () => null, // () => { mode, playerPosition, avatarPosition, heading }
  // First-person "eyes" for the AI: when this returns a view, the frame is
  // rendered from the avatar's eye position/heading (hiding the avatar
  // body), regardless of the on-screen third-person camera. Return null to
  // capture the main camera as-is (e.g. already in first person).
  // Shape: { position: [x,y,z], heading: radians, hide: [Object3D…] }
  getEgoView = () => null,
  interval = 2,        // seconds between captures
  maxFrames = 30,      // ring buffer length
  width = 640,         // capture width in px (height keeps aspect)
  onlyWhenMoved = true,
  minMove = 0.05,      // metres
  minTurnDeg = 2,      // degrees
} = {}) {
  const frames = [];
  const listeners = new Set();
  let enabled = false;
  let sinceLast = Infinity; // capture immediately on enable
  let nextId = 1;

  const work = document.createElement("canvas");
  const ctx = work.getContext("2d");

  const pos = new THREE.Vector3();
  const quat = new THREE.Quaternion();
  const lastPos = new THREE.Vector3(Infinity, Infinity, Infinity);
  const lastQuat = new THREE.Quaternion();

  function poseChanged() {
    camera.getWorldPosition(pos);
    camera.getWorldQuaternion(quat);
    const moved = pos.distanceTo(lastPos) > minMove;
    const turned =
      THREE.MathUtils.radToDeg(2 * Math.acos(Math.min(1, Math.abs(quat.dot(lastQuat))))) > minTurnDeg;
    return moved || turned;
  }

  // Ego camera used for first-person re-renders (fov matches the main cam).
  const egoCam = new THREE.PerspectiveCamera(60, 1, 0.05, 1000);

  /** Grab one frame NOW (call only right after renderer.render). */
  function captureNow(force = false) {
    if (!force && onlyWhenMoved && !poseChanged()) return null;

    const src = renderer.domElement;
    if (!src.width || !src.height) return null;

    // First-person view: re-render the scene from the avatar's eyes onto
    // the same canvas, grab it, then restore the on-screen view below.
    const ego = scene ? getEgoView() : null;
    let captureCam = camera;
    let hidden = [];
    if (ego) {
      egoCam.fov = camera.fov;
      egoCam.aspect = src.width / src.height;
      egoCam.updateProjectionMatrix();
      egoCam.position.set(ego.position[0], ego.position[1], ego.position[2]);
      egoCam.rotation.set(0, ego.heading, 0); // heading 0 = facing -Z
      hidden = (ego.hide ?? []).filter((o) => o && o.visible);
      for (const o of hidden) o.visible = false;
      renderer.render(scene, egoCam);
      captureCam = egoCam;
    }

    const h = Math.max(1, Math.round((width * src.height) / src.width));
    work.width = width;
    work.height = h;
    ctx.drawImage(src, 0, 0, width, h);

    if (ego) {
      for (const o of hidden) o.visible = true;
      renderer.render(scene, camera); // put the real view back on screen
    }

    captureCam.getWorldPosition(pos);
    captureCam.getWorldQuaternion(quat);
    // Movement gating always tracks the MAIN camera (it follows the player
    // in both modes), regardless of which camera produced the image.
    camera.getWorldPosition(lastPos);
    camera.getWorldQuaternion(lastQuat);

    const frame = {
      id: nextId++,
      timestamp: Date.now(),
      view: ego ? "ego" : "camera", // ego = first-person from the avatar's eyes
      image: work.toDataURL("image/jpeg", 0.75),
      camera: {
        position: pos.toArray().map((n) => +n.toFixed(3)),
        quaternion: quat.toArray().map((n) => +n.toFixed(5)), // [x, y, z, w]
        fov: captureCam.fov,
        aspect: +(src.width / src.height).toFixed(4),
      },
      player: getPlayerState(),
    };
    frames.push(frame);
    while (frames.length > maxFrames) frames.shift();
    for (const fn of listeners) {
      try { fn(frame); } catch (err) { console.error("[capture] listener failed:", err); }
    }
    return frame;
  }

  return {
    /** Call from the render loop, AFTER renderer.render(). */
    afterRender(delta) {
      if (!enabled) return;
      sinceLast += delta;
      if (sinceLast < interval) return;
      if (captureNow()) sinceLast = 0;
      // pose unchanged → keep waiting; retry next frame at zero cost
    },

    setEnabled(v) {
      enabled = !!v;
      if (enabled) sinceLast = Infinity; // first frame right away
    },
    isEnabled: () => enabled,
    setInterval(v) {
      interval = Math.max(0.25, v);
    },

    captureNow: () => captureNow(true),
    getFrames: () => [...frames],
    latest: () => frames[frames.length - 1] ?? null,
    count: () => frames.length,
    clear() {
      frames.length = 0;
    },

    /** Future AI seam: subscribe to every new frame. Returns unsubscribe. */
    onFrame(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },

    /** Download the buffer (images + poses) as one JSON file. */
    exportJson(sceneName = "scene") {
      const payload = {
        version: 1,
        scene: sceneName,
        exportedAt: new Date().toISOString(),
        frameCount: frames.length,
        frames,
      };
      const blob = new Blob([JSON.stringify(payload)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `${sceneName}-captures-${Date.now()}.json`;
      a.click();
      URL.revokeObjectURL(a.href);
    },

    dispose() {
      listeners.clear();
      frames.length = 0;
    },
  };
}
