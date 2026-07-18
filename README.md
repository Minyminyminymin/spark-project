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
**Q/E** fly down/up (first person) · **V** toggle first/third person ·
**M** show collider mesh · **N** toggle scan visual · **T** toggle name tags ·
**B** toggle bounding boxes · **P** log spawn-point snippet · **Enter** chat

## Architecture

```
src/
  main.js         Entry — CONFIG (the "Inspector") + module wiring + render loop
  scene.js        Renderer, scene, camera, SparkRenderer, lights
  splat.js        Gaussian splat loading (Spark SplatMesh)
  models.js       GLB mesh loading + PLY point-cloud loading (loadPointCloud)
  collision.js    BVH raycast collider (three-mesh-bvh) + primitive fallback
  player.js       First-person fly cam + third-person character controller
  avatar.js       GLB character + animation crossfades (Idle/Walk/Run)
  ui.js           HUD, control panel (sliders), chat + Objects tabs
  minimap.js      Top-view canvas map with walked-path trail + object markers
  annotations.js  Name-tag objects in the scene: tag mode, persistence, Objects tab
  worldstate.js   Read-only query API over annotations (`/where`, window.__world)

tools/
  analyzer-service/
    run_local.py    Offline Phase-C auto-tagging CLI (see its own README.md)

Next: agent.js — AI agent receiving chat commands (hook: ui.onSubmit in
main.js), consuming worldstate.js for scene context (see
splat-analyzer-plan.md §5 — deliberately not built yet)
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
align with the Alignment sliders (all rotations are in **degrees** — radians
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

## Object annotations

`annotations.js` name-tags objects inside the scan (see
`splat-analyzer-plan.md` for the full spec). Press **T** to toggle tags,
enable "Tag mode" in the Controls panel, click a surface, then type a name
in chat — empty submit cancels. Tags are stored in EnvironmentRoot-local
coordinates (immune to Environment slider changes) in
`public/annotations/<sceneName>.json`, where `sceneName` is the collider
GLB's basename. Every edit also drafts to `localStorage` so it survives a
reload; "Export JSON" downloads the file to drop into `public/annotations/`,
and "Save to disk" does the same automatically via a `vite.config.js`
dev-server middleware (dev mode only). The Objects tab lists every tag with
live distance and rename/delete buttons; tags also render as low-opacity
dots on the minimap.

**Click detection quality:** a click doesn't just record a bare point. If a
color-carrying point cloud is loaded (the raw scan PLY — position + vertex
color; press **L** if the active visual is a Spark gaussian splat instead),
the click seeds a small neighbourhood, buckets the surrounding sphere into
5 cm voxels, then flood-fills 26-neighbour-connected voxels outward from
the seed — accepting a voxel only while its color stays close to the
region's running (adaptive) color mean, so the fill actually stops at
object edges instead of just distance. A detected floor slab under the
seed is excluded so tags don't bleed into the ground. The dominant color is
picked by per-point voting on color names, not by averaging RGB (averaging
a two-tone chair gives muddy gray; voting gives "white"). Toggle "HQ
detect" in the Controls panel to fall back to a cheaper color-ball-radius
heuristic if the flood fill is too slow/aggressive for a given scene.
Typed labels get auto-specified from the detected color ("chair" → "white
chair" if it isn't already color-specific), and the extent is stored as the
object's `aabb`. Press **B** (or the "Bounding boxes" checkbox) to see the
detected extents; name-tag labels sit right above the detected box instead
of floating a fixed height above the anchor.

**Offline auto-tagging (Phase C):** `tools/analyzer-service/run_local.py`
finds objects in a PLY export via open-vocabulary text prompts (OWL-ViT)
and writes them into the same annotations file as `source: "auto"` — see
`tools/analyzer-service/README.md`. Auto proposals show a badge and an
**Accept** button in the Objects tab (promotes to `verified`, recolors
green) alongside the existing Rename/Delete, closing the review loop the
plan's Phase C describes.

`worldstate.js` is the read-only query API a future agent (plan §5, not
built here) will consume: `window.__world.describeScene()`,
`.findObject(name)`, `.nearest(k)`, `.listObjects()`, `.playerState()`. Try
`/where <name>` in chat to test it end-to-end today.

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
- **SPZ v4 (released 2026-05) is NOT supported by Spark 2.1.0** — feeding
  one hangs the tab during decode. The loader now reads the plaintext
  v4 header and refuses with a clear HUD message instead. Down-convert
  v4 files to v2 / compressed PLY (Niantic SPZ Converter, or a
  compatibility option in the export tool) until Spark adds v4 support.
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
