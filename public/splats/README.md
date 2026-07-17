# Scan assets (not in git)

Large scan files are shared separately (zip / Drive), not committed.

Expected files for the current CONFIG (`src/main.js`):

- `public/splats/MGstudio_SmallRoom.ply` — Polycam point cloud (visual layer, 53 MB)
- `public/models/MGstudio_SmallRoom.glb` — textured mesh (collider — included in git)

Optional larger scene:

- `public/splats/MGstudio_Area1.ply` (253 MB, 9.4M points)
- `public/models/MGstudio_Area1_filled.glb` (hole-filled collider, 12 MB)

Without the PLY the app still runs — the visual layer just reports
"failed" in the HUD. Any Polycam export pair works: point-cloud PLY as
the visual, mesh GLB as the collider. Real gaussian splat files
(.spz / 3DGS .ply) are detected automatically and rendered via Spark.
