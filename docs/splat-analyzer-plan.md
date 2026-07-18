# Splat Analyzer — Implementation Plan

Goal: attach **name tags to objects inside the scanned scene** (the splat /
point-cloud visual), so that a future AI agent can query *what objects
exist and where they are* and act on that ("go to the sofa", "what's next
to the arcade machine?").

This document is the spec for an implementing AI. Read
`README.md` + `src/main.js` first — respect the existing architecture.

---

## 0. Constraints (do not violate)

- Vanilla ES modules + three.js r185 + Vite. No frameworks, no TypeScript.
- Follow the existing module pattern: `createX(deps) → { update, dispose, … }`,
  wired ONLY in `main.js`. Modules never import each other's state.
- All rotations in config/UI are **degrees** (see `rotationDeg` precedent).
- Do not modify Spark internals or `collision.js` query semantics.
- UI goes through `ui.js` (`buildControls` schema, tab pattern, hotkeys in
  `main.js` guarded by `isTyping()`).
- **Coordinate rule:** annotation positions are stored in
  **EnvironmentRoot-local coordinates** (same frame as the collider mesh,
  AFTER the visual registration offset is applied — i.e. the frame the
  collider mesh lives in). World positions are always derived at runtime
  via `environmentRoot.matrixWorld`, so moving/scaling the environment in
  the Controls panel never invalidates saved annotations.

## 1. Data model — `public/annotations/<sceneName>.json`

`sceneName` = collider GLB basename without extension
(e.g. `MGstudio_SmallRoom`).

```json
{
  "version": 1,
  "scene": "MGstudio_SmallRoom",
  "frame": "environment-local",
  "updatedAt": "2026-07-17T12:00:00Z",
  "objects": [
    {
      "id": "sofa_1",
      "label": "sofa",
      "aliases": ["couch", "소파"],
      "position": [1.2, 0.4, -2.1],
      "radius": 0.9,
      "aabb": { "min": [0.4, 0.0, -2.9], "max": [2.0, 0.8, -1.3] },
      "confidence": 1.0,
      "source": "manual",
      "notes": ""
    }
  ]
}
```

Rules: `id` = `label` + numeric suffix, unique. `radius` required; `aabb`
optional. `source` ∈ `manual | auto | verified`. Multiple instances of the
same label are separate objects (`sofa_1`, `sofa_2`).

## 2. Phase A — Manual tagging tool (MVP, build this first)

New module `src/annotations.js`:

- `createAnnotations({ scene, environmentRoot, camera })`
- **Load** `public/annotations/<scene>.json` on startup (404 → empty set).
- **Render**: for each object, a small anchor dot (THREE.Sprite) + a
  billboard text label (canvas-texture sprite, label text, readable at
  ~1–6 m, fades with distance). Parent everything under ONE
  `THREE.Group` added to `environmentRoot` (so local coords come free).
- **Toggle**: hotkey `T` + a `Layers → Name tags` checkbox (sync both,
  follow the M/N/L precedent in `main.js`).
- **Tag mode**: checkbox `Tag mode` in a new panel section. While ON:
  - Canvas click does NOT pointer-lock; instead raycast
    `camera → click point` against the **collider meshes**
    (reuse `collision.js` — add a `raycastFromCamera(ndc, camera)` helper
    there if needed) and place the anchor at the hit point (store in
    env-local coords: `environmentRoot.worldToLocal(hitPoint)`).
  - Then focus the chat input in "name entry" state: next submitted text
    becomes the label (empty submit cancels). Show feedback via
    `ui.addMessage("System", …)`.
- **Objects tab**: third tab in the right panel ("Objects") listing all
  annotations: label, distance from player (live), buttons: rename,
  delete, "go to" is NOT needed yet.
- **Persistence** (dev-friendly, two layers):
  1. Every change → `localStorage` draft (survives reload).
  2. `Export JSON` button → downloads the file; the user drops it into
     `public/annotations/`. (Optional nicety: a tiny Vite dev-server
     middleware `POST /__annotations/<scene>` that writes the file
     directly — implement in `vite.config.js`; guard to dev mode only.)
- **Minimap**: extend `minimap.js` `update()` to accept
  `annotations: [{x, z, label}]` (world coords) and draw small dots +
  labels at low opacity.

Acceptance: walk around, T shows tags, tag mode lets me click a sofa,
type "sofa", see a labeled anchor that persists across reloads, appears
on the minimap, and export produces valid JSON.

## 3. Phase B — World-state API (the AI bridge)

New module `src/worldstate.js` — **this is the contract the future agent
consumes.** Keep it pure/query-only.

```js
createWorldState({ annotations, environmentRoot, player, avatar })
```

Methods (all return plain JSON-safe data, positions in WORLD coords):

- `listObjects()` → `[{ id, label, aliases, position, radius, distanceFromPlayer }]`
- `findObject(query)` → best match by label/alias (case-insensitive,
  substring ok) or null
- `nearest(k = 3)` → k closest objects to the player
- `playerState()` → `{ position, heading, mode }`
- `describeScene()` → compact single-string summary for LLM context, e.g.
  `"Objects: sofa_1 (2.1m NE), arcade_1 (4.0m N), plant_1 (1.2m E). Player at (0.0, 0.0, 3.0) facing N."`
  (bearing = compass-style from world -Z; keep it under ~500 chars)

Wire-up: `window.__world = worldState` (debug), and pass it to the chat
handler in `main.js` — when a chat message starts with `/where <name>`,
answer from `findObject` directly (no AI needed yet). This proves the
bridge end-to-end.

Acceptance: `/where sofa` in chat answers with distance + direction;
`window.__world.describeScene()` returns a sane summary.

## 4. Phase C — Auto-suggest pipeline (optional, after A+B work)

Semi-automatic labeling. Do NOT attempt semantics on raw gaussians; use
multi-view 2D detection + geometric back-projection:

1. **Capture (in-app)**: "Capture views" button → move the camera through
   ~12–20 poses (orbit at 2 heights around the room centre, inside
   collider bounds) → for each pose save: RGB canvas screenshot (JPEG),
   camera intrinsics (fov/aspect) + extrinsics (matrixWorld), and a
   **depth image rendered from the collider mesh** at the same pose
   (MeshDepthMaterial override pass, packed RGBA → float). Bundle into a
   zip or POST per-frame.
2. **Service (`tools/analyzer-service/`, Python FastAPI)**:
   - `POST /analyze` accepts frames.
   - Open-vocabulary detection + segmentation per frame:
     Grounded-DINO + SAM2 (or YOLO-World as a lighter fallback) with a
     configurable prompt list (`sofa, chair, table, plant, tv, arcade
     machine, mirror, door, window, …`).
   - For each mask: sample the depth image inside the mask (median),
     back-project centroid ray → 3D world point → transform to env-local
     using the pose data.
   - Cluster per-label points across frames (DBSCAN, eps ≈ 0.5 m);
     cluster centre → proposal `{ label, position, radius, confidence,
     source: "auto" }`.
   - Return proposals JSON.
3. **Review UI**: proposals appear in the Objects tab flagged `auto`
   (different color); accept (→ `verified`), rename, or reject each.

Notes / honesty: depth-from-mesh fails inside mesh holes — fall back to
skipping masks whose depth variance is huge. GPU for SAM2 recommended;
service is offline tooling, NOT a runtime dependency of the web app.

## 5. Phase D — Agent hook (do not build; leave the seam)

`ui.onSubmit` in `main.js` currently echoes. The future `agent.js`
receives `{ text, worldState }`, calls an LLM with
`worldState.describeScene()` in context, and can return actions like
`{ type: "goto", objectId }` — navigation can then use annotation world
positions + collider ground clamp. Out of scope for this plan; just keep
`worldstate.js` free of DOM/UI dependencies so it can be reused.

## 6. File summary

| File | Change |
| --- | --- |
| `src/annotations.js` | NEW — anchors, labels, tag mode, persistence |
| `src/worldstate.js` | NEW — query API for agents |
| `src/minimap.js` | extend `update()` with annotation markers |
| `src/collision.js` | add `raycastFromCamera(ndc, camera)` helper |
| `src/ui.js` | third tab "Objects", list rendering helpers |
| `src/main.js` | wiring: hotkey T, tag-mode state, chat `/where`, `__world` |
| `vite.config.js` | (optional) dev-only annotation save middleware |
| `tools/analyzer-service/` | (Phase C) FastAPI + Grounded-SAM pipeline |
| `public/annotations/MGstudio_SmallRoom.json` | seed with 2–3 manual tags |

## 7. Milestones & order

1. **A1**: annotations.js load/render/toggle (T) with a hand-written JSON.
2. **A2**: tag mode (click → raycast → name via chat) + localStorage +
   export + Objects tab + minimap markers.
3. **B**: worldstate.js + `/where` chat command + `window.__world`.
4. **C** (optional): capture → service → proposals → review flow.

Each milestone must leave the app fully working (`npx vite build` passes,
manual smoke test: walk, toggle layers, chat).
