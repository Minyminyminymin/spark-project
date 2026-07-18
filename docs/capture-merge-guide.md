# Capture Feature — Merge Guide

For the bridging teammate: you've already merged everything **up to**
the camera-capture system. This guide contains the remaining pieces —
every block is copy-paste-ready and marked with an anchor showing
exactly where it goes in your merged tree.

**Scope of this delta** (nothing else changed):

| File | Change type |
| --- | --- |
| `src/capture.js` | ✚ NEW file — copy it wholesale from this repo |
| `src/main.js` | 6 insertions (import, wiring, panel section, 2 hotkeys, loop call) |
| `src/ui.js` | 1 method + 1 help-text line |
| `src/style.css` | 1 appended style block |

No new npm dependencies. Feature summary: every 2 s (while the camera
moves) a first-person frame is rendered from the avatar's eyes and
stored with its pose; `G` opens a gallery; "Export frames JSON"
produces the file described in `docs/ai-bridge.md`.

---

## 1. `src/capture.js` — new file

Copy the whole file from this repo (self-contained, ~190 lines, imports
only `three`). Key API, for orientation:

```js
createCapture({ renderer, camera, scene, getEgoView, getPlayerState, interval })
  → { afterRender, setEnabled, isEnabled, setInterval, captureNow,
      getFrames, latest, count, clear, onFrame, exportJson, dispose }
```

---

## 2. `src/main.js` — six insertions

### 2.1 Import

**WHERE:** with the other module imports at the top, after
`import { createWorldState } from "./worldstate.js";`

```js
import { createCapture } from "./capture.js";
```

### 2.2 Create the capture system

**WHERE:** immediately after the world-state block that ends with
`window.__world = worldState;`

```js
  // --- Camera capture (the "eyes" feed for the future vision AI) ---
  // Every 2 s (while the camera actually moves) the rendered frame is
  // stored with its full pose. The agent, once connected, consumes this
  // via capture.onFrame(f => …) or capture.getFrames() — see capture.js.
  const capture = createCapture({
    renderer,
    camera,
    scene,
    interval: 2,
    // Frames are ALWAYS first-person (the avatar's eyes) — in third-person
    // mode the scene is re-rendered from the avatar's head for the capture
    // (body hidden), then the on-screen view is restored. In first person
    // the main camera already is the eye, so no re-render is needed.
    getEgoView: () => {
      if (player.mode === "first" || !avatar) return null;
      const a = avatar.object.position;
      return {
        position: [a.x, a.y + CONFIG.player.eyeHeight, a.z],
        heading: avatar.object.rotation.y - (avatar.facingOffset ?? 0),
        hide: [avatar.object],
      };
    },
    getPlayerState: () => ({
      mode: player.mode,
      playerPosition: player.rig.position.toArray().map((n) => +n.toFixed(2)),
      avatarPosition: avatar ? avatar.object.position.toArray().map((n) => +n.toFixed(2)) : null,
      heading: avatar ? +(avatar.object.rotation.y - (avatar.facingOffset ?? 0)).toFixed(3) : 0,
    }),
  });
  window.__capture = capture; // debug / future agent hookup
```

### 2.3 Control-panel section

**WHERE:** inside the `ui.buildControls([...])` array, immediately
**before** `{ type: "section", label: "Player" },`

```js
    { type: "section", label: "Camera capture (AI eyes)" },
    { type: "checkbox", id: "cap-auto", label: "Auto capture (C)",
      value: false, onChange: (v) => {
        capture.setEnabled(v);
        ui.addMessage("System", v
          ? "Auto capture ON — a frame is stored every 2 s while the camera moves."
          : `Auto capture OFF — ${capture.count()} frame(s) in the buffer.`);
      } },
    { type: "slider", id: "cap-interval", label: "Interval", min: 0.5, max: 10, step: 0.5,
      value: 2, format: (v) => `${(+v).toFixed(1)}s`,
      onChange: (v) => capture.setInterval(v) },
    { type: "button", label: "Capture now", onClick: () => {
      // Grab on the next rendered frame so the canvas is fresh.
      requestAnimationFrame(() => {
        const f = capture.captureNow();
        ui.addMessage("System", f
          ? `Captured frame #${f.id} (${capture.count()} buffered).`
          : "Capture failed — canvas not ready.");
      });
    } },
    { type: "button", label: "View captures (G)", onClick: () => {
      ui.showCaptureGallery(capture.getFrames());
    } },
    { type: "button", label: "Export frames JSON", onClick: () => {
      if (capture.count() === 0) return ui.addMessage("System", "No frames to export.");
      capture.exportJson("MGstudio_SmallRoom");
      ui.addMessage("System", `Exported ${capture.count()} frame(s).`);
    } },
    { type: "button", label: "Clear frames", onClick: () => {
      capture.clear();
      ui.addMessage("System", "Capture buffer cleared.");
    } },
```

### 2.4 Hotkeys

**WHERE:** inside the `window.addEventListener("keydown", …)` handler,
next to the existing `KeyP` block:

```js
    if (e.code === "KeyG") {
      ui.showCaptureGallery(capture.getFrames());
    }
    if (e.code === "KeyC") {
      const next = !capture.isEnabled();
      capture.setEnabled(next);
      ui.setControl("cap-auto", next);
      ui.addMessage("System", next
        ? "Auto capture ON — a frame is stored every 2 s while the camera moves."
        : `Auto capture OFF — ${capture.count()} frame(s) in the buffer.`);
    }
```

### 2.5 Debug handle

**WHERE:** add `capture` to the `window.__research = { … }` object:

```js
  window.__research = {
    THREE, scene, camera, renderer, environmentRoot, player, minimap,
    annotations, worldState, capture,
    // …existing getters unchanged…
  };
```

### 2.6 Render-loop call

**WHERE:** at the very end of the `renderer.setAnimationLoop(() => { … })`
callback, immediately **after** `renderer.render(scene, camera);`

```js
    // Must run AFTER render — the WebGL canvas is only readable in the
    // same tick (preserveDrawingBuffer is false).
    capture.afterRender(delta);
```

---

## 3. `src/ui.js` — two edits

### 3.1 Gallery method

**WHERE:** inside the object returned by `createUI`, right **before**
the existing `focusInput() { … }` method:

```js
    /**
     * Fullscreen gallery of captured frames (capture.js). Click a photo
     * for a lightbox; Esc / Close / backdrop click dismisses.
     */
    showCaptureGallery(frames) {
      const overlay = document.createElement("div");
      overlay.className = "cap-overlay";
      overlay.innerHTML = `
        <div class="cap-header">
          <span class="cap-title">Captured frames (${frames.length})</span>
          <button type="button" class="cap-close">Close (Esc)</button>
        </div>
        <div class="cap-grid"></div>`;
      const grid = overlay.querySelector(".cap-grid");

      if (frames.length === 0) {
        grid.innerHTML = `<div class="cap-empty">
          No frames yet — enable Auto capture (C) and move around,
          or press "Capture now".</div>`;
      }
      for (const f of [...frames].reverse()) {
        const cell = document.createElement("div");
        cell.className = "cap-cell";
        const time = new Date(f.timestamp).toLocaleTimeString();
        const p = f.camera.position;
        cell.innerHTML = `
          <img src="${f.image}" alt="frame ${f.id}">
          <div class="cap-meta">#${f.id} · ${time} ·
            cam (${p[0].toFixed(1)}, ${p[1].toFixed(1)}, ${p[2].toFixed(1)})</div>`;
        cell.addEventListener("click", () => {
          const box = document.createElement("div");
          box.className = "cap-lightbox";
          box.innerHTML = `<img src="${f.image}" alt="frame ${f.id}">`;
          box.addEventListener("click", () => box.remove());
          document.body.appendChild(box);
        });
        grid.appendChild(cell);
      }

      function close() {
        window.removeEventListener("keydown", onKey, true);
        overlay.remove();
      }
      function onKey(e) {
        if (e.code === "Escape") {
          const box = document.querySelector(".cap-lightbox");
          if (box) box.remove();
          else close();
          e.stopPropagation();
        }
      }
      window.addEventListener("keydown", onKey, true);
      overlay.querySelector(".cap-close").addEventListener("click", close);
      overlay.addEventListener("click", (e) => {
        if (e.target === overlay) close();
      });
      document.body.appendChild(overlay);
    },
```

### 3.2 Help text

**WHERE:** the `.hud-help` block in the HUD template. Add
`<b>C</b> auto capture ·` to the hotkey line so it reads:

```html
<b>L</b> raw PLY points · <b>T</b> name tags · <b>B</b> bounding boxes ·
<b>C</b> auto capture · <b>P</b> spawn point · <b>Enter</b> chat
```

---

## 4. `src/style.css` — append at the end

```css
/* capture gallery overlay */
.cap-overlay {
  position: fixed;
  inset: 0;
  z-index: 50;
  background: rgba(8, 8, 11, 0.88);
  backdrop-filter: blur(4px);
  display: flex;
  flex-direction: column;
  color: #e8e8ee;
}

.cap-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 18px;
  flex-shrink: 0;
}

.cap-title {
  font-size: 14px;
  font-weight: 600;
  letter-spacing: 0.4px;
}

.cap-close {
  font: inherit;
  font-size: 13px;
  color: #e8e8ee;
  background: rgba(255, 255, 255, 0.1);
  border: 1px solid rgba(255, 255, 255, 0.2);
  border-radius: 6px;
  padding: 5px 14px;
  cursor: pointer;
}

.cap-close:hover {
  background: rgba(255, 255, 255, 0.2);
}

.cap-grid {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 12px;
  padding: 0 18px 18px;
}

.cap-cell {
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 8px;
  overflow: hidden;
  cursor: pointer;
}

.cap-cell:hover {
  border-color: rgba(120, 180, 255, 0.6);
}

.cap-cell img {
  display: block;
  width: 100%;
  aspect-ratio: 16 / 9;
  object-fit: cover;
}

.cap-meta {
  font-size: 11px;
  line-height: 1.5;
  padding: 6px 9px;
  opacity: 0.75;
  font-variant-numeric: tabular-nums;
}

.cap-empty {
  grid-column: 1 / -1;
  opacity: 0.6;
  font-size: 13px;
  padding: 30px 0;
  text-align: center;
}

.cap-lightbox {
  position: fixed;
  inset: 0;
  z-index: 60;
  background: rgba(5, 5, 8, 0.95);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: zoom-out;
}

.cap-lightbox img {
  max-width: 92vw;
  max-height: 88vh;
  border-radius: 6px;
}
```

---

## 5. Post-merge verification (2 minutes)

1. `npx vite build` → passes.
2. Run the app, press **C** → chat says "Auto capture ON".
3. Walk around ~5 s in third person, press **G** → gallery shows frames
   that are **first-person** (no avatar back visible), each with
   timestamp + camera coords.
4. Click a thumbnail → lightbox; **Esc** closes.
5. "Export frames JSON" downloads a file; spot-check one frame has
   `view: "ego"`, `camera.position` ≈ avatar position + 1.6 m height.
6. Consumer contract for the exported file / live hooks:
   `docs/ai-bridge.md`.
