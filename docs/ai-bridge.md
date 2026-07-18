# Bridge Guide — connecting the AI agent to the splat engine

**Audience: the bridging teammate.** You sit between two systems:

```
[ ScavengeAI agent (Python) ] ⇄ [ YOUR BRIDGE ] ⇄ [ splat engine (this repo, browser) ]
      speaks HTTP /view /action        you build this        exposes JS hooks + JSON exports
```

- The **splat engine** (this repo, Min) renders the scanned space, runs
  the character + collision, and produces pose-tagged camera frames.
  It deliberately ships **hooks, not a server**.
- The **AI side** (ScavengeAI) expects an HTTP world server (`/view`,
  `/action`) per the contract its author circulated.
- **The bridge (you)** implements that HTTP server on top of our hooks,
  owning all format/coordinate translation. This doc gives you every
  interface and formula you need.

---

## 1. What the splat engine provides

### 1a. Batch: exported captures JSON

In-app: `C` = auto-capture every 2 s while the camera moves (30-frame
ring buffer, 640-px JPEG each), `G` = gallery viewer, "Export frames
JSON" = one file:

```json
{
  "version": 1,
  "scene": "MGstudio_SmallRoom",
  "exportedAt": "2026-07-18T19:20:56.340Z",
  "frameCount": 6,
  "frames": [
    {
      "id": 1,
      "timestamp": 1784402387573,
      "image": "data:image/jpeg;base64,/9j/4AAQ…",
      "camera": {
        "position": [-0.608, 2.526, -1.541],
        "quaternion": [-0.2147, -0.17471, -0.03907, 0.96013],
        "fov": 60,
        "aspect": 1.9815
      },
      "player": {
        "mode": "third",
        "playerPosition": [-0.61, 2.53, -1.54],
        "avatarPosition": [0.35, -0.45, -4.08],
        "heading": 1.113
      }
    }
  ]
}
```

| Field | Meaning |
| --- | --- |
| `image` | JPEG data-URL, 640 px wide (⚠ has the `data:image/jpeg;base64,` prefix — strip before decoding) |
| `camera.position` | world coords `[x, y, z]`, metres, three.js **Y-up** |
| `camera.quaternion` | `[x, y, z, w]`, three.js order |
| `camera.fov` / `aspect` | vertical FOV (deg) / true width÷height of the source frame |
| `view` | `"ego"` = rendered first-person from the avatar's eyes (avatar hidden); `"camera"` = the on-screen camera (only when already in 1st person) |
| `player.mode` | on-screen mode `"first"`/`"third"` — the IMAGE is always first-person either way |
| `player.avatarPosition` | character position, world coords |
| `player.heading` | avatar facing, **radians**, 0 = −Z, positive = left turn |

### 1b. Live: in-page JS hooks (`window.__capture`)

```js
const off = __capture.onFrame((frame) => { /* same shape as frames[] */ });
__capture.getFrames();       // ring buffer, most recent 30
__capture.latest();          // newest frame or null
__capture.setEnabled(true);  // start/stop auto-capture programmatically
__capture.captureNow();      // force a frame right now (call after a render)
```

### 1c. Control surfaces (for implementing `/action`)

Two ways to move the character programmatically, both used successfully
in testing:

1. **Synthetic key events** (recommended — runs the full collision
   pipeline: wall blocking, ground clamp, camera boom):
   ```js
   window.dispatchEvent(new KeyboardEvent("keydown", { code: "KeyW" }));
   // …wait distance/speed seconds…
   window.dispatchEvent(new KeyboardEvent("keyup", { code: "KeyW" }));
   ```
   Walk speed default 2 m/s (readable/settable via the Controls panel or
   `__research.player.setTuning({ walkSpeed })`). Turning: set
   `__research.avatar.object.rotation.y` (radians; remember
   `facingOffset` — see `player.heading` docs above) — or emulate mouse
   look. **Blocked-move detection:** compare avatar position before/after;
   if displacement ≪ requested, the collider stopped it — that's your
   `success: false`.
2. **Direct pose writes** via `__research.avatar.object.position` /
   `.rotation` — instant, but bypasses collision; only for teleports/reset.

Full debug surface: `window.__research` (scene, camera, player, avatar,
capture, worldState…).

---

## 2. What the AI side expects (contract recap)

Their Python agent calls `SPLAT_ENGINE_URL` (e.g. `http://localhost:8090`):

- `GET /view` → `{ image_base64 (NO data: prefix), width, height,
  pose: {x, y, z, yaw_deg}, frame_id }` — width/height must be the TRUE
  pixel dims of the returned frame.
- `POST /action` with exactly one of `{"type":"move","distance":1.0}`,
  `{"type":"turn","degrees":±90}`, `{"type":"stop","reason":"…"}` →
  `{ success, pose, message }`. Blocked move = **200 with
  `success:false`**, never 4xx/5xx.
- Server is stateful (holds the authoritative pose); `/view` must answer
  within a few seconds; a reset-to-home endpoint is a nice-to-have.

---

## 3. Translation cheat sheet (the bridge owns these)

| Concern | Splat engine | Agent side | Conversion |
| --- | --- | --- | --- |
| Ground plane | Y-up, floor on X–Z | `(x, y)` floor, `z` ignored | `agent.x = our.x`, `agent.y = our.z`, `agent.z = our.y` |
| Facing | `heading` radians, 0 = −Z, + = left/CCW | `yaw_deg` degrees | `yaw_deg = degrees(heading)`; forward in agent coords = `(−sin h, −cos h)` |
| Turn action | rotate avatar by radians | `degrees` (+90 = left) | `heading += radians(degrees)` |
| Move action | walk speed m/s over time | `distance: 1.0` per step | 1 step = **1.0 m**: hold KeyW for `1.0 / walkSpeed` s, then keyup |
| Image | data-URL with prefix, 640 px | raw base64, true dims | strip prefix; report the frame's actual w×h (or re-encode at fixed 1280×720) |
| Blocked | displacement ≪ requested | `success:false`, 200 | measure before/after positions |
| Ego view | frames are ALWAYS first-person (`view: "ego"`) from the avatar's eyes, even in third-person play | ego-centric perception | nothing to do — `camera.position/quaternion` in each frame already describe the eye |

## 4. Recommended bridge architecture

The engine renders in browser WebGL, so the bridge must reach into a
live page. Two proven options:

1. **Vite middleware + WebSocket relay** (lightest): a dev-server
   middleware (in `vite.config.js`) exposes `/view` `/action`, relays
   each request to the open tab over Vite's built-in WS, the page runs
   the hooks above and replies. No extra processes; requires the dev
   server running AND the tab open — return **503** when the tab is
   gone (the agent treats /view failures as fatal by design, which is
   correct).
2. **Puppeteer/headless Chrome**: your server owns a headless page of
   `http://localhost:5173` and calls the same `window.__capture` /
   key-event APIs via `page.evaluate`. Heavier, but no human tab needed.

Either way: serialize requests (one action at a time), keep the
authoritative pose on your server by reading it back from
`__research.avatar` after every action, and clamp `/view` time by using
`__capture.captureNow()` (≈ms) instead of waiting for the 2 s timer.

## 5. Bonus context the agent can use

- `__world.describeScene()` → one-line LLM-ready summary of all
  name-tagged objects with distances/directions from the player.
- `__world.listObjects()` / `.findObject("sofa")` / `.nearest(3)` →
  structured object data in world coords (same axes as §3).
- Annotations persist in `public/annotations/<scene>.json`
  (environment-local coords — read via `__world` to get world coords).

Implementation references: `src/capture.js` (~150 lines),
`src/worldstate.js`, wiring in `src/main.js` ("Camera capture" section).
Questions about engine internals → Min.
