# Spark WebXR Research Template

Research prototype foundation: Gaussian splat rendering (Spark 2.x) + Three.js + WebXR, built as a modular template for avatar, world-space UI, multiplayer, and embodied-AI-agent experiments.

## Run

```bash
npm install
npm run dev
```

Open http://localhost:5173 — click the canvas for mouse look, **WASD** to move, **Q/E** down/up, **Shift** to sprint, **Esc** to release the mouse. **M** toggles the GLB mesh, **N** toggles the splat (for quality comparison).

## Architecture

```
src/
  main.js    Entry — config + module wiring + render loop
  scene.js   Renderer, scene, camera, SparkRenderer, lights, resize
  splat.js   Gaussian splat loading (Spark SplatMesh)
  player.js  Player rig: WASD + pointer-lock mouse look
  xr.js      WebXR button + session handling (desktop pose save/restore)
  ui.js      HTML overlay HUD (status + controls help)
  models.js  GLB/GLTF mesh loading (coexists with splats in the same scene)
  avatar.js  GLB avatar loader — written, not wired in yet (step 7)

Later: network.js (multiplayer), agent.js (embodied AI agent)
```

Design rule: modules don't import each other's state — `main.js` creates everything and passes dependencies explicitly. Each module returns a small object (`{ update, dispose, ... }`).

### The player rig pattern

The camera is a child of `player.rig` (a `THREE.Group`):

- **Desktop:** mouse look rotates the camera; WASD translates the rig.
- **XR:** the headset drives the camera *relative to the rig*, so thumbstick locomotion / teleport / networking later only needs to move the rig — no camera fighting.

## Key facts about Spark (worth remembering)

- Spark is a **renderer only**. It loads existing splat files (`.ply`, `.spz`, `.splat`, `.ksplat`, `.sog`, `.zip`, `.rad`). It cannot convert OBJ/GLB into splats — those load as normal Three.js meshes and coexist in the same scene.
- **Not every .ply is a splat file.** A PLY containing `element face` data (check with `head public/splats/file.ply`) is a triangle mesh, not a 3DGS capture — Spark can't render it. `castle_of_loarre.ply` in this repo is such a mesh-PLY; the project currently uses the official Spark sample splat (butterfly.spz, loaded from sparkjs.dev) until a real capture is added. Real 3DGS PLYs have no faces and carry per-splat opacity/scale/rotation/SH properties.
- A `SparkRenderer` instance must be added to the scene (done in `scene.js`).
- `antialias: false` on the WebGLRenderer is required for performance (MSAA doesn't help splats).
- `lod: true` on `SplatMesh` builds an LOD tree in a background worker. Spark auto-scales the splat budget per platform: ~500–750K in WebXR, ~1–1.5M mobile, ~2.5M desktop. Tune with `lodSplatScale` on `SparkRenderer`.
- In WebXR, Spark automatically defers splat updates (`preUpdate: false`) to minimize latency.
- Requires `three >= 0.180` (installed: three 0.185, spark 2.1).
- Most 3DGS captures are y-down — `splat.js` flips 180° about X by default (`flipped: true`).

## Quest testing (step 6)

WebXR needs a **secure context** (https or localhost). Options:

1. **adb reverse** (simplest, treats the Quest as localhost):
   ```bash
   npm run dev
   adb reverse tcp:5173 tcp:5173
   ```
   Then open `http://localhost:5173` in the Quest browser.
2. **LAN + HTTPS:** `npm run dev -- --host` plus an HTTPS cert (e.g. `vite-plugin-mkcert`), then open `https://<your-ip>:5173` on the Quest.

## Development plan

- [x] **1.** Spark renderer + Gaussian splat on desktop
- [x] **2.** Mouse look
- [x] **3.** WASD
- [x] **4.** Load GLB (as normal Three.js mesh — `models.js`, toggle with M/N)
- [x] **5.** WebXR button
- [ ] **6.** Quest testing
- [ ] **7.** Avatar movement
- [ ] **8.** World-space UI (HTML HUD is invisible in XR)
- [ ] **9.** Networking / AI agents

## Housekeeping

Safe to delete (leftover scaffolding, no longer referenced): `SparkTest.js`, `SparkTest/`, `src/counter.js`, `src/assets/`.
