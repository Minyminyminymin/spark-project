#!/usr/bin/env python3
"""
run_local.py — offline object-proposal pipeline
(splat-analyzer-plan.md, Phase C: "auto-suggest pipeline").

Usage:
  python run_local.py --ply MGstudio_SmallRoom_Export.ply \
      --prompt "blue sofa, tea table, arcade machine, plant, tv, lamp" \
      --quality medium

What it does:
  1. Loads a colored point-cloud PLY export (position + vertex RGB).
  2. Renders synthetic RGB+depth views from a ring of camera poses orbiting
     the cloud (a plain numpy point-splat rasterizer — no OpenGL/EGL context
     needed, so this runs anywhere Python does, including headless boxes).
  3. Runs open-vocabulary detection (OWL-ViT, via `transformers`) on each
     view with your --prompt list.
  4. Back-projects each detection's box center through that view's depth
     buffer to a 3D point, then clusters same-label detections across views
     (DBSCAN) into one proposal per real-world object.
  5. Grows each proposal's exact extent + dominant color against the FULL
     point cloud using a voxel-grid connected-component flood fill — the
     same algorithm annotations.js runs client-side for manual tag-mode
     clicks, so auto and manual tags are extracted the same way.
  6. Merges the results into public/annotations/<scene>.json as
     source: "auto" (manual/verified entries already in that file are left
     alone) — open the app's Objects tab to Accept, rename, or delete each.

HONESTY (see splat-analyzer-plan.md's own "Notes / honesty" section for
Phase C): this is deliberately NOT Grounded-DINO + SAM2. OWL-ViT was chosen
because it's a single `pip install transformers torch` away — no extra
GitHub repo, no separate checkpoint zoo, and free-text queries like
"blue sofa" work directly as detection prompts. It also skips real
segmentation masks: object extent comes from the color-flood-fill step
(step 5), not a pixel mask, which is why per-point color averaging across a
whole detection box would give a muddy answer — the flood fill's own vote
is what actually determines the box. This is a legitimate quality/setup
trade-off, not a mistake: swap in Grounded-DINO+SAM2 (see "Extending" in
README.md next to this file) if you have GPU budget and want tighter masks.

Coordinate frame: this script writes positions in the SAME frame as
annotations.js expects — the collider-mesh / EnvironmentRoot-local frame
(splat-analyzer-plan.md §0). Since it works from a raw PLY export, you must
tell it the same registration transform main.js applies for that file
(CONFIG.environment.visual.offset / presets in main.js) via --offset-pos /
--offset-rot / --offset-scale, or accept the default identity transform and
re-align visually afterwards (toggle Bounding boxes in the app, compare
against the collider mesh, re-run with corrected --offset-* if needed).
"""
import argparse
import colorsys
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

try:
    from plyfile import PlyData
except ImportError:
    print("Missing dependency: pip install -r requirements.txt", file=sys.stderr)
    raise

# ---------------------------------------------------------------------------
# Quality presets — trade coverage/precision for speed.
# ---------------------------------------------------------------------------

QUALITY_PRESETS = {
    "fast": dict(
        views=8, rings=1, resolution=512, model="google/owlvit-base-patch32",
        conf=0.15, dbscan_eps=0.6, dbscan_min=2,
    ),
    "medium": dict(
        views=16, rings=2, resolution=768, model="google/owlvit-base-patch32",
        conf=0.12, dbscan_eps=0.5, dbscan_min=2,
    ),
    "high": dict(
        views=24, rings=3, resolution=1024, model="google/owlvit-large-patch14",
        conf=0.10, dbscan_eps=0.4, dbscan_min=3,
    ),
}

# Voxel flood-fill extent detection — mirrors annotations.js's HQ mode
# (see src/annotations.js: SEED_RADIUS / HQ_MAX_RADIUS / HQ_COLOR_TOL / VOXEL).
SEED_RADIUS = 0.1
MAX_RADIUS = 1.8
COLOR_TOL = 55
VOXEL = 0.05
FLOOR_SLAB = 0.05

COLOR_WORDS = [
    "black", "white", "gray", "grey", "red", "orange", "yellow", "green",
    "cyan", "blue", "purple", "pink", "brown", "beige", "silver", "gold",
]


# ---------------------------------------------------------------------------
# Color naming — same HSL-bucket heuristic as annotations.js's nameColor().
# ---------------------------------------------------------------------------

def name_color(r, g, b):
    """r, g, b in 0..255 -> a short color name."""
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    h_deg = h * 360
    if l < 0.13:
        return "black"
    if l > 0.9 and s < 0.25:
        return "white"
    if s < 0.14:
        return "gray"
    if 15 <= h_deg < 45 and l < 0.45 and s > 0.2:
        return "brown"
    if h_deg < 15 or h_deg >= 345:
        return "red"
    if h_deg < 45:
        return "orange"
    if h_deg < 70:
        return "yellow"
    if h_deg < 170:
        return "green"
    if h_deg < 200:
        return "cyan"
    if h_deg < 255:
        return "blue"
    if h_deg < 290:
        return "purple"
    if h_deg < 345:
        return "pink"
    return "gray"


def with_color_prefix(label, color_name):
    if not color_name:
        return label
    lower = label.lower()
    if any(w in lower for w in COLOR_WORDS):
        return label
    return f"{color_name} {label}"


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "object"


# ---------------------------------------------------------------------------
# PLY loading
# ---------------------------------------------------------------------------

def load_ply(path):
    ply = PlyData.read(str(path))
    v = ply["vertex"]
    names = v.data.dtype.names
    xyz = np.stack([v["x"], v["y"], v["z"]], axis=1).astype(np.float64)
    color_keys = None
    for candidate in (("red", "green", "blue"), ("r", "g", "b")):
        if all(k in names for k in candidate):
            color_keys = candidate
            break
    if color_keys:
        rgb = np.stack([v[k] for k in color_keys], axis=1).astype(np.float64)
        if rgb.max() <= 1.5:  # some exporters write 0..1 floats
            rgb *= 255.0
        rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    else:
        print("[warn] PLY has no red/green/blue vertex properties — "
              "color detection and prompts like \"blue sofa\" will be unreliable.")
        rgb = np.full((xyz.shape[0], 3), 160, dtype=np.uint8)
    return xyz.astype(np.float32), rgb


def subsample(xyz, rgb, max_points, seed=0):
    n = xyz.shape[0]
    if n <= max_points:
        return xyz, rgb
    idx = np.random.default_rng(seed).choice(n, size=max_points, replace=False)
    return xyz[idx], rgb[idx]


# ---------------------------------------------------------------------------
# Synthetic view rendering — plain numpy point-splat rasterizer (no GL
# context needed: robust on headless machines, over SSH, in containers).
# ---------------------------------------------------------------------------

def orbit_poses(center, radius, n_views, n_rings):
    poses = []
    per_ring = max(1, n_views // n_rings)
    heights = np.linspace(-0.1, 0.4, n_rings) * radius + center[1]
    up = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    for h in heights:
        for k in range(per_ring):
            theta = 2 * np.pi * k / per_ring
            eye = np.array([
                center[0] + radius * 1.5 * np.cos(theta),
                h,
                center[2] + radius * 1.5 * np.sin(theta),
            ], dtype=np.float32)
            target = center.astype(np.float32)
            poses.append((eye, target, up))
    return poses


def render_view(xyz, rgb, eye, target, up, fov_deg, width, height, splat_px=1):
    forward = target - eye
    forward = forward / np.linalg.norm(forward)
    right = np.cross(forward, up)
    right = right / np.linalg.norm(right)
    true_up = np.cross(right, forward)

    rel = xyz - eye
    xc = rel @ right
    yc = rel @ true_up
    zc = rel @ forward

    valid = zc > 0.05
    fx = (width / 2) / np.tan(np.radians(fov_deg) / 2)
    fy = fx
    cx, cy = width / 2, height / 2
    u = cx + fx * xc[valid] / zc[valid]
    v = cy - fy * yc[valid] / zc[valid]
    z = zc[valid]
    col = rgb[valid]

    in_bounds = (u >= 0) & (u < width) & (v >= 0) & (v < height)
    u, v, z, col = u[in_bounds], v[in_bounds], z[in_bounds], col[in_bounds]
    ui = u.astype(np.int32)
    vi = v.astype(np.int32)

    depth_buf = np.full((height, width), np.inf, dtype=np.float32)
    color_buf = np.zeros((height, width, 3), dtype=np.uint8)

    # Nearest-point-per-pixel: sort farthest-first, scatter-assign — numpy
    # fancy-index assignment keeps the LAST write per duplicate index, so
    # the nearest point (assigned last) wins without an explicit z-test loop.
    order = np.argsort(-z)
    ui, vi, z, col = ui[order], vi[order], z[order], col[order]
    depth_buf[vi, ui] = z
    color_buf[vi, ui] = col

    if splat_px > 0:
        from scipy.ndimage import grey_dilation
        size = splat_px * 2 + 1
        mask = np.isfinite(depth_buf)
        inv_depth = np.where(mask, 1.0 / np.maximum(depth_buf, 1e-6), 0.0)
        inv_depth_dil = grey_dilation(inv_depth, size=size)
        fill = (~mask) & (inv_depth_dil > 0)
        depth_buf = np.where(fill, 1.0 / np.maximum(inv_depth_dil, 1e-6), depth_buf)
        for c in range(3):
            color_buf[:, :, c] = np.where(mask, color_buf[:, :, c], grey_dilation(color_buf[:, :, c], size=size))

    extrinsics = dict(eye=eye, right=right, up=true_up, forward=forward)
    intrinsics = dict(fx=fx, fy=fy, cx=cx, cy=cy)
    return color_buf, depth_buf, extrinsics, intrinsics


def unproject(u, v, z, intr, extr):
    xc = (u - intr["cx"]) * z / intr["fx"]
    yc = -(v - intr["cy"]) * z / intr["fy"]
    point_cam = xc * extr["right"] + yc * extr["up"] + z * extr["forward"]
    return extr["eye"] + point_cam


def sample_box_depth(depth_buf, box, patch=6):
    x0, y0, x1, y1 = box
    h, w = depth_buf.shape
    cx, cy = int((x0 + x1) / 2), int((y0 + y1) / 2)
    xs = slice(max(0, cx - patch), min(w, cx + patch + 1))
    ys = slice(max(0, cy - patch), min(h, cy + patch + 1))
    vals = depth_buf[ys, xs]
    finite = vals[np.isfinite(vals)]
    if finite.size == 0:
        return None, cx, cy
    return float(np.median(finite)), cx, cy


# ---------------------------------------------------------------------------
# Open-vocabulary detection (lazy-imported: --views-only doesn't need torch)
# ---------------------------------------------------------------------------

def resolve_device(requested):
    if requested:
        return requested
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def load_detector(model_name, device):
    import torch
    from transformers import OwlViTForObjectDetection, OwlViTProcessor

    processor = OwlViTProcessor.from_pretrained(model_name)
    model = OwlViTForObjectDetection.from_pretrained(model_name).to(device).eval()
    return processor, model, torch


def detect(processor, model, torch, device, image_rgb, prompts, threshold):
    from PIL import Image

    image = Image.fromarray(image_rgb)
    inputs = processor(text=[prompts], images=image, return_tensors="pt").to(device)
    with torch.no_grad():
        outputs = model(**inputs)
    target_sizes = torch.tensor([image.size[::-1]])
    results = processor.post_process_object_detection(
        outputs=outputs, threshold=threshold, target_sizes=target_sizes
    )[0]
    boxes = results["boxes"].cpu().numpy()
    scores = results["scores"].cpu().numpy()
    label_ids = results["labels"].cpu().numpy()
    return [(boxes[i], float(scores[i]), prompts[label_ids[i]]) for i in range(len(boxes))]


# ---------------------------------------------------------------------------
# Extent detection — voxel-grid connected-component flood fill, mirroring
# annotations.js's floodFillVoxels() so auto and manual tags use the same
# algorithm. Runs against the FULL point cloud (not the render subsample).
# ---------------------------------------------------------------------------

def grow_region(xyz, rgb, center):
    d2 = np.sum((xyz - center) ** 2, axis=1)
    sphere_mask = d2 <= MAX_RADIUS * MAX_RADIUS
    sphere_idx = np.nonzero(sphere_mask)[0]
    if sphere_idx.size == 0:
        return None
    seed_idx = sphere_idx[d2[sphere_idx] <= SEED_RADIUS * SEED_RADIUS]
    if seed_idx.size < 4:
        return None

    sphere_pts = xyz[sphere_idx]
    sphere_col = rgb[sphere_idx].astype(np.float64)
    floor_y = sphere_pts[:, 1].min() + FLOOR_SLAB
    reject_floor = center[1] > floor_y + 0.06

    keys = np.floor(sphere_pts / VOXEL).astype(np.int64)
    cells = {}
    for local_i, key in enumerate(map(tuple, keys)):
        i = sphere_idx[local_i]
        if reject_floor and sphere_pts[local_i, 1] < floor_y:
            continue
        cell = cells.get(key)
        if cell is None:
            cell = {"idx": [], "sum": np.zeros(3), "n": 0}
            cells[key] = cell
        cell["idx"].append(i)
        cell["sum"] += sphere_col[local_i]
        cell["n"] += 1

    seed_keys = set()
    mean_sum = np.zeros(3)
    mean_n = 0
    for i in seed_idx:
        key = tuple(np.floor(xyz[i] / VOXEL).astype(np.int64))
        seed_keys.add(key)
        mean_sum += rgb[i].astype(np.float64)
        mean_n += 1

    accepted = []
    visited = set()
    queue = list(seed_keys)
    while queue:
        key = queue.pop()
        if key in visited:
            continue
        visited.add(key)
        cell = cells.get(key)
        if cell is None:
            continue

        if mean_n > 0 and key not in seed_keys:
            cell_mean = cell["sum"] / cell["n"]
            region_mean = mean_sum / mean_n
            if np.linalg.norm(cell_mean - region_mean) > COLOR_TOL:
                continue  # color break -> object boundary

        accepted.extend(cell["idx"])
        mean_sum += cell["sum"]
        mean_n += cell["n"]

        cx, cy, cz = key
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    if dx == dy == dz == 0:
                        continue
                    nk = (cx + dx, cy + dy, cz + dz)
                    if nk not in visited and nk in cells:
                        queue.append(nk)

    if len(accepted) < 4:
        accepted = seed_idx.tolist()

    accepted = np.array(accepted)
    pts = xyz[accepted]
    cols = rgb[accepted]
    votes = defaultdict(int)
    for c in cols:
        votes[name_color(*c)] += 1
    color_name = max(votes, key=votes.get) if votes else None
    return dict(
        aabb_min=pts.min(axis=0),
        aabb_max=pts.max(axis=0),
        color_name=color_name,
        point_count=int(accepted.size),
    )


# ---------------------------------------------------------------------------
# Registration transform — match CONFIG.environment.visual.offset in main.js
# (see this script's module docstring re: coordinate frame).
# ---------------------------------------------------------------------------

def apply_offset(points, offset_pos, offset_rot_deg, offset_scale):
    rx, ry, rz = np.radians(offset_rot_deg)
    Rx = np.array([[1, 0, 0], [0, np.cos(rx), -np.sin(rx)], [0, np.sin(rx), np.cos(rx)]])
    Ry = np.array([[np.cos(ry), 0, np.sin(ry)], [0, 1, 0], [-np.sin(ry), 0, np.cos(ry)]])
    Rz = np.array([[np.cos(rz), -np.sin(rz), 0], [np.sin(rz), np.cos(rz), 0], [0, 0, 1]])
    R = Rz @ Ry @ Rx
    return (points @ R.T) * offset_scale + np.array(offset_pos)


# ---------------------------------------------------------------------------
# Merge proposals into public/annotations/<scene>.json
# ---------------------------------------------------------------------------

def merge_and_write(out_path, scene, new_objects):
    existing = {"objects": []}
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text())
        except (json.JSONDecodeError, OSError) as err:
            print(f"[warn] couldn't parse existing {out_path} ({err}) — starting fresh")

    # Keep everything a human already reviewed; only "auto" gets replaced.
    kept = [o for o in existing.get("objects", []) if o.get("source") != "auto"]
    used_ids = {o["id"] for o in kept}
    final = list(kept)
    for obj in new_objects:
        candidate = obj["id"]
        n = 1
        while candidate in used_ids:
            n += 1
            candidate = f"{obj['id']}_{n}"
        obj["id"] = candidate
        used_ids.add(candidate)
        final.append(obj)

    payload = {
        "version": 1,
        "scene": scene,
        "frame": "environment-local",
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "objects": final,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="Offline auto-tagging (Phase C) for splat-analyzer-plan.md — "
                     "detects objects in a colored PLY export via open-vocabulary "
                     "prompts and writes proposals into public/annotations/.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--ply", required=True, help="Path to the colored point-cloud PLY export")
    p.add_argument("--prompt", required=True,
                    help='Comma-separated open-vocabulary prompts, e.g. "blue sofa, tea table, plant"')
    p.add_argument("--quality", choices=list(QUALITY_PRESETS), default="medium")
    p.add_argument("--scene", default=None,
                    help="sceneName for the annotations file — MUST match the collider GLB "
                         "basename used in main.js's CONFIG.environment.mesh.url for the app to "
                         "find it (default: --ply's filename without extension)")
    p.add_argument("--out", default=None,
                    help="Output annotations JSON path (default: public/annotations/<scene>.json, "
                         "resolved relative to the repo root two levels above this script)")
    p.add_argument("--offset-pos", nargs=3, type=float, default=[0.0, 0.0, 0.0], metavar=("X", "Y", "Z"))
    p.add_argument("--offset-rot", nargs=3, type=float, default=[0.0, 0.0, 0.0], metavar=("RX", "RY", "RZ"),
                    help="degrees — match CONFIG.environment.visual.offset.rotationDeg in main.js")
    p.add_argument("--offset-scale", type=float, default=1.0)
    p.add_argument("--confidence", type=float, default=None,
                    help="Override the quality preset's detection confidence threshold")
    p.add_argument("--fov", type=float, default=70.0)
    p.add_argument("--render-max-points", type=int, default=400_000,
                    help="Downsample for the rendering pass only — extent/color detection "
                         "(step 5) always uses the full point cloud")
    p.add_argument("--device", default=None, help="cpu | cuda (default: cuda if available)")
    p.add_argument("--views-only", action="store_true",
                    help="Render preview PNGs and exit — no torch/transformers needed. Use this "
                         "first to sanity-check camera coverage before running full detection.")
    p.add_argument("--out-views", default="preview_views", help="Directory for --views-only PNGs")
    return p.parse_args()


def main():
    args = parse_args()
    preset = QUALITY_PRESETS[args.quality]
    prompts = [p.strip() for p in args.prompt.split(",") if p.strip()]
    if not prompts:
        print("error: --prompt produced no usable terms", file=sys.stderr)
        sys.exit(1)

    ply_path = Path(args.ply)
    scene = args.scene or ply_path.stem

    print(f"[1/5] Loading {ply_path} …")
    xyz, rgb = load_ply(ply_path)
    print(f"  {len(xyz):,} points")

    center = xyz.mean(axis=0)
    radius = float(np.percentile(np.linalg.norm(xyz - center, axis=1), 90))
    poses = orbit_poses(center, radius, preset["views"], preset["rings"])
    render_xyz, render_rgb = subsample(xyz, rgb, args.render_max_points)

    if args.views_only:
        from PIL import Image
        out_dir = Path(args.out_views)
        out_dir.mkdir(parents=True, exist_ok=True)
        for i, (eye, target, up) in enumerate(poses):
            color, _depth, _extr, _intr = render_view(
                render_xyz, render_rgb, eye, target, up, args.fov, preset["resolution"], preset["resolution"]
            )
            Image.fromarray(color).save(out_dir / f"view_{i:02d}.png")
        print(f"Saved {len(poses)} preview renders to {out_dir}/ — check coverage, then re-run without --views-only.")
        return

    device = resolve_device(args.device)
    print(f"[2/5] Loading detector ({preset['model']}) on {device} — first run downloads weights…")
    processor, model, torch = load_detector(preset["model"], device)

    print(f"[3/5] Rendering {len(poses)} views and detecting: {', '.join(prompts)}")
    confidence = args.confidence if args.confidence is not None else preset["conf"]
    raw_hits = defaultdict(list)
    for i, (eye, target, up) in enumerate(poses):
        color, depth, extr, intr = render_view(
            render_xyz, render_rgb, eye, target, up, args.fov, preset["resolution"], preset["resolution"]
        )
        dets = detect(processor, model, torch, device, color, prompts, confidence)
        hit_count = 0
        for box, score, label in dets:
            z, cx, cy = sample_box_depth(depth, box)
            if z is None:
                continue
            point3d = unproject(cx, cy, z, intr, extr)
            raw_hits[label].append((point3d, score))
            hit_count += 1
        print(f"  view {i + 1}/{len(poses)}: {hit_count} detection(s)")

    print("[4/5] Clustering across views + growing extents against the full point cloud…")
    try:
        from sklearn.cluster import DBSCAN
    except ImportError:
        print("Missing dependency: pip install -r requirements.txt", file=sys.stderr)
        raise

    objects = []
    for label, items in raw_hits.items():
        if not items:
            continue
        pts = np.array([p for p, _s in items])
        clustering = DBSCAN(eps=preset["dbscan_eps"], min_samples=preset["dbscan_min"]).fit(pts)
        labels_arr = clustering.labels_
        for cid in sorted(set(labels_arr) - {-1}):
            mask = labels_arr == cid
            centroid = pts[mask].mean(axis=0)
            conf = float(np.mean([items[i][1] for i in range(len(items)) if mask[i]]))

            region = grow_region(xyz, rgb, centroid)
            if region is None:
                aabb = None
                radius_est = 0.4
                color_name = None
            else:
                aabb_min = apply_offset(region["aabb_min"][None, :], args.offset_pos, args.offset_rot, args.offset_scale)[0]
                aabb_max = apply_offset(region["aabb_max"][None, :], args.offset_pos, args.offset_rot, args.offset_scale)[0]
                aabb = {"min": aabb_min.tolist(), "max": aabb_max.tolist()}
                radius_est = float(np.linalg.norm(region["aabb_max"] - region["aabb_min"]) / 2)
                color_name = region["color_name"]

            centroid_local = apply_offset(centroid[None, :], args.offset_pos, args.offset_rot, args.offset_scale)[0]
            final_label = with_color_prefix(label, color_name)
            base_id = f"{slugify(final_label)}_auto"

            objects.append({
                "id": base_id,
                "label": final_label,
                "aliases": [],
                "position": centroid_local.tolist(),
                "radius": round(max(radius_est, 0.15), 3),
                **({"aabb": aabb} if aabb else {}),
                "confidence": round(conf, 3),
                "source": "auto",
                "notes": f"run_local.py: prompt \"{label}\", {int(mask.sum())} view hit(s), "
                         f"quality={args.quality}",
            })

    print(f"  {len(objects)} proposal(s) after clustering")

    if args.out:
        out_path = Path(args.out)
    else:
        repo_root = Path(__file__).resolve().parents[2]
        out_path = repo_root / "public" / "annotations" / f"{scene}.json"

    print(f"[5/5] Merging into {out_path} (existing manual/verified tags are kept)…")
    merge_and_write(out_path, scene, objects)
    print("Done. Open the app, Objects tab: auto proposals are badge-marked — Accept, rename, or delete each.")


if __name__ == "__main__":
    main()
