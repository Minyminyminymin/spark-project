# Scan assets (not in git)

Large scan files are shared separately (zip / Drive), not committed.

Active files for the current CONFIG (`src/main.js`):

- `public/splats/MGstudio_SmallRoom.ply` — Polycam point cloud, the
  current VISUAL layer (51 MB, rendered as three.js Points)
- `public/models/MGstudio_SmallRoom.glb` — textured mesh COLLIDER
  (included in git, 4.6 MB)

Present but blocked:

- `MGstudio_SmallRoom.spz` / `MGstudio_Area1.spz` — real gaussian splats
  (883K / 1.6M splats, SH3) but in **SPZ v4** format, which Spark 2.1.0
  cannot decode? I'll work on down converting to SPZ v2 or compressed PLY (Niantic SPZ
  Converter: https://www.nianticspatial.com/spz-converter) and point
  `CONFIG.environment.visual.url` at the result — hopefully it will render
  through Spark automatically.

Optional larger scene:

- `MGstudio_Area1.ply` (242 MB point cloud, 9.4M points)
- `public/models/MGstudio_Area1_filled.glb` (hole-filled collider, 12 MB)

Without the visual file the app still runs — the HUD just reports the
visual layer as failed. Any Polycam pair works: point-cloud PLY as the
visual, mesh GLB as the collider. Real gaussian files (.spz v1–v3,
3DGS .ply) are auto-detected and rendered via Spark.
