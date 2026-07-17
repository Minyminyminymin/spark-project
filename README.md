# Spark Research Template (Desktop)

Walkable 3D-scan environments in the browser: a **scan visual layer**
(gaussian splats via [Spark](https://sparkjs.dev) or point clouds via
three.js) over an **invisible collision mesh** (Unity-style Mesh Collider),
with a third/first-person character, a control panel, and a top-view
minimap. Built as the foundation for embodied-AI-agent experiments.

Desktop-only: WebXR support was removed 2026-07-16 (the player-rig
structure still supports re-adding it later).

## Run

```bash
npm install
npm run dev   # http://localhost:5173
```

Large scan assets are not committed — see `public/splats/README.md`.
The app runs without them (visual layer reports "failed", everything
else works).

### Controls

Click canvas = mouse look (Esc releases) · **WASD** move · **Shift** sprint ·
**Q/E** fly down/up (1인칭) · **V** toggle first/third person ·
**M** show collider mesh · **N** toggle scan visual ·
**P** log spawn-point snippet · **Enter** chat

## Architecture

```
src/
  main.js       Entry — CONFIG (the "Inspector") + module wiring + render loop
  scene.js      Renderer, scene, camera, SparkRenderer, lights
  splat.js      Gaussian splat loading (Spark SplatMesh)
  models.js     GLB mesh loading + PLY point-cloud loading (loadPointCloud)
  collision.js  BVH raycast collider (three-mesh-bvh) + primitive fallback
  player.js     First-person fly cam + third-person character controller
  avatar.js     GLB character + animation crossfades (Idle/Walk/Run)
  ui.js         HUD, control panel (sliders), chat tab
  minimap.js    Top-view canvas map with walked-path trail

Next: agent.js — AI agent receiving chat commands (hook: ui.onSubmit in main.js)
```

`main.js` creates everything and passes dependencies explicitly; modules
never import each other's state. `window.__research` exposes live handles
(scene, camera, player, avatar…) in the browser console.

## The two-layer environment

Every scanned scene is a pair of files under `EnvironmentRoot`:

| Layer    | File                | Role                                        |
| -------- | ------------------- | ------------------------------------------- |
| visual   | `.ply` / `.spz`     | what you see (splats or colored points)     |
| collider | `.glb` (triangles!) | invisible physics: floor, walls, camera clip |

The visual loader **auto-detects** the file kind by reading the PLY
header: real gaussian PLYs (opacity/scale_0/rot_0 properties) go through
Spark; plain point clouds (e.g. Polycam exports) go through three.js
Points. Double-precision coordinates are converted to float32 (WebGL
can't upload float64 — this silently kills the render loop otherwise).

To swap in a new scene, change two urls in `CONFIG.environment` and
align with the 정렬 sliders (all rotations are in **degrees** — radians
are banned from the config after a painful 233°-tilt incident).

## Collision (Unity Mesh Collider style)

`collision.js` builds a BVH over the collider GLB (`three-mesh-bvh`) and
provides per-frame queries: ground clamp (walk on scanned floors, step
limit 0.5 m), chest-ray wall blocking, and camera-boom clipping.
Collider materials are forced double-sided (scan meshes and hole-filled
patches have inconsistent winding). If the GLB has no triangles (point
cloud exported as GLB), Unity-style primitive colliders from
`CONFIG.environment.colliders` are used instead — press **M** to see
the collider like a Unity gizmo.

Scan meshes with holes can be repaired headlessly (VTK
`vtkFillHolesFilter`, same family as MeshLab's Close Holes) — see the
dev log for the recipe used on `MGstudio_Area1`.

## Field notes / known quirks

- **Not every .ply/.spz is a gaussian splat.** Files with positions but
  empty opacity render as nothing (fully transparent). Mesh-PLYs (with
  `element face`) and point-cloud exports aren't splats either. The
  loader detects and falls back automatically.
- **Spark 2.1 static-camera quirk:** a splat that finishes loading while
  the sort worker is busy stays invisible until the view changes. The
  render loop applies an imperceptible "splat kick" for the first ~900
  frames as a workaround.
- **Spark LOD (`lod: true`) proved unreliable on desktop dev** — kept
  off; revisit if/when targeting standalone headsets.
- Polycam pairs: GLB is y-up, PLY point cloud is z-up → the visual
  offset defaults to `rotationDeg: [-90, 0, 0]` (confirmed by ICP,
  residual 2.3 cm).

## For collaborators

- Your own `world.spz` + `collider.glb` drop into `CONFIG.environment`
  directly — spz is detected as a gaussian format and rendered by Spark.
- Spark 0.1.x projects: see the official
  [0.1 → 2.0 migration guide](https://sparkjs.dev/docs/0.1-2.0-migration-guide/)
  (main requirement: three ≥ 0.180).
- The AI-brain ↔ agent bridge belongs at app level, not in Spark:
  chat input arrives at `ui.onSubmit` (main.js), and agent code can read
  and drive the world through the same handles the debug console uses.
