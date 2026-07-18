# analyzer-service — offline auto-tagging (Phase C)

`run_local.py` finds objects in a colored point-cloud PLY export using
open-vocabulary text prompts and writes them into
`public/annotations/<scene>.json` as `source: "auto"` — reviewable in the
app's Objects tab (Accept / rename / delete each). This is offline tooling,
**not** a runtime dependency of the web app — run it once whenever you have
a new scan export, not on every page load.

See `splat-analyzer-plan.md` (repo root) §4 for the original spec this
implements a simplified version of.

## Setup

```bash
cd tools/analyzer-service
python -m venv .venv && source .venv/bin/activate   # optional but recommended
pip install -r requirements.txt
```

`torch` + `transformers` will download the OWL-ViT model weights
(~600 MB–1.7 GB depending on `--quality`) from Hugging Face on first run —
needs internet access once, then it's cached locally.

## Usage

```bash
python run_local.py \
  --ply MGstudio_SmallRoom_Export.ply \
  --prompt "blue sofa, tea table, arcade machine, plant, tv, lamp" \
  --quality medium
```

Sanity-check camera coverage first on a new/unfamiliar export (no
torch/transformers needed for this step):

```bash
python run_local.py --ply MGstudio_SmallRoom_Export.ply --prompt x --views-only
open preview_views/   # or your OS's equivalent — eyeball the renders
```

If the renders look sparse/empty, the point cloud's bounding-sphere heuristic
picked a bad orbit radius for this scene's shape — open an issue on this
comment or adjust `orbit_poses()`.

### Coordinate alignment — read this before trusting positions

Annotations are stored in the same local frame the app's collider mesh uses
(`splat-analyzer-plan.md` §0). This script only ever sees the raw PLY, so it
doesn't know that frame automatically. If your export needs the same
registration as `main.js`'s `CONFIG.environment.visual.offset` (check
`presets` there for your filename, or the currently active `offset` values),
pass it through:

```bash
python run_local.py --ply MGstudio_SmallRoom_Export.ply \
  --prompt "..." \
  --offset-pos 0 0 0 --offset-rot -90 0 0 --offset-scale 1
```

If you skip this, proposals still get written (identity transform) — just
open the app afterwards, toggle **Bounding boxes** (B), and check whether
the boxes actually sit inside the room / on the collider mesh. If they're
offset, re-run with corrected `--offset-*` (or add a preset in `main.js` for
this filename so both the visual layer and future runs stay in sync).

### Quality tiers

| tier   | views | model                    | conf | notes                          |
| ------ | ----- | ------------------------ | ---- | ------------------------------- |
| fast   | 8     | owlvit-base-patch32      | 0.15 | quick pass, misses small/occluded objects |
| medium | 16    | owlvit-base-patch32      | 0.12 | good default                    |
| high   | 24    | owlvit-large-patch14     | 0.10 | slower, catches more, more noise too |

Extent detection (bounding box + color) always runs against the FULL point
cloud regardless of tier — only the *rendering* pass is downsampled
(`--render-max-points`, default 400k) for speed.

### Re-running / merging

Safe to run repeatedly, including with a different `--prompt` list each
time: every run replaces ALL `source: "auto"` entries with its fresh
results, but leaves anything you've already `manual`ly tagged or `Accept`ed
(→ `verified`) in the app untouched.

## Honesty — what this is and isn't

This is **not** Grounded-DINO + SAM2 (the plan's original suggestion). It
swaps in **OWL-ViT** (via `transformers`, `pip`-installable, no extra repo
or checkpoint dance, and free-text queries like `"blue sofa"` work directly
as prompts) and skips pixel segmentation masks entirely. Object extent
instead comes from a voxel-grid connected-component flood fill against the
real point cloud, seeded at each detection's back-projected 3D point — the
same algorithm `src/annotations.js` runs client-side for manual tag-mode
clicks (`floodFillVoxels()`), so auto and manual tags are extracted the same
way and should look visually consistent in the Objects tab.

Known limitations:

- No true segmentation mask → the flood fill can over- or under-grow on
  objects that blend into their background color (e.g. a white lamp against
  a white wall). Reviewing/rejecting bad proposals in the Objects tab is
  expected, not a bug to route around.
- OWL-ViT's box localization is coarser than Grounded-DINO's; multi-word
  prompts ("tea table" vs "table") help it disambiguate similar objects.
- CPU inference works but is slow for `--quality high` — a GPU (`--device
  cuda`) is recommended there, optional for `fast`/`medium`.

### Extending

To swap in Grounded-DINO + SAM2 for tighter masks: replace `load_detector`
/ `detect` in `run_local.py` with Grounded-DINO's API (same
box-out/label-out/score-out contract expected downstream), and optionally
feed SAM2's mask into `grow_region`'s `sphere_idx` as a hard prior instead
of a fresh flood fill. The rest of the pipeline (rendering, back-projection,
clustering, merge-into-JSON) doesn't need to change.
