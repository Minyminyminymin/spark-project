# Agent Movement System — Technical Guide

This document explains how the agent navigates toward a target object once it's been spotted in the scene. Written for the bridging AI / frontend integration work.

---

## High-Level Flow

```
Browser Frame (JPEG) + Pose (x, y, z, yaw)
        |
        v
POST /agent/step  (app/main.py:255)
        |
        v
Agent.step_from_frame()  (app/controller.py:395)
        |
        v
  ┌─────────────────────────────────┐
  │  Phase 1: SCANNING              │
  │  Goal not visible in frame      │
  │  → Turn 90° (up to 4x = 360°)  │
  │  → Then move 1m to new spot     │
  │  → Repeat until goal spotted    │
  └─────────────────────────────────┘
        |  (goal object found in perception)
        v
  ┌─────────────────────────────────┐
  │  Phase 2: APPROACHING           │
  │  Goal visible → compute world   │
  │  coordinates from bbox + pose   │
  │  → Emit walk_to(x, z) action   │
  │  → Avatar walks directly there  │
  │  → Re-evaluate on arrival       │
  │  → Stop when close enough       │
  └─────────────────────────────────┘
```

---

## Key Files

| File | Role |
|------|------|
| `app/controller.py` | Agent brain — FSM, approach logic, action emission |
| `app/perception.py` | Qwen VL call — image → structured Observation with bboxes |
| `app/planner.py` | Text-only planner (used in scanning, NOT in approach) |
| `app/world/base.py` | Action types: `MoveAction`, `TurnAction`, `WalkToAction`, `StopAction`, `StrafeAction` |
| `app/main.py` | FastAPI server, `/agent/step` endpoint |
| `src/agent.js` | Browser-side loop: captures frame, POSTs to backend, applies returned action |
| `src/player.js` | Avatar locomotion: `walkAgentTo(x, z)`, `rotateAgent(deg)` |

---

## The Approach Algorithm (controller.py, `_compute_approach_actions`)

Once perception confirms the goal object is in frame, the controller computes a **direct walk_to target** in world coordinates. No planner call, no turn-then-move — just geometry.

### Step 1: Get bbox from perception

Qwen returns bounding boxes on a **0–1000 normalized scale** (both axes). Example:
```json
{"x_min": 600, "y_min": 100, "x_max": 800, "y_max": 900}
```

### Step 2: Determine proximity (should we stop?)

```python
box_w = (x_max - x_min) / 1000   # 0-1 fraction of frame width
box_h = (y_max - y_min) / 1000   # 0-1 fraction of frame height
area_frac = box_w * box_h

# Stop if object fills enough of the frame
if area_frac > 0.30 or box_h > 0.75 or box_w > 0.60:
    return StopAction(reason="reached goal")
```

This handles both wide objects (sofa) and tall/thin ones (floor lamp).

### Step 3: Estimate distance from apparent size

```python
size = max(box_w, box_h)
est_distance = min(1.0 / max(size, 0.05), 8.0)
walk_distance = est_distance * 0.9  # don't overshoot
```

### Step 4: Compute bearing from screen position

The horizontal center of the bbox tells us the angle offset from the agent's current heading:

```python
H_FOV_DEG = 90.0  # assumed horizontal field of view
cx = (x_min + x_max) / 2 / 1000  # 0-1 normalized center
angle_offset_deg = (cx - 0.5) * H_FOV_DEG

bearing_deg = pose.yaw_deg + angle_offset_deg
```

### Step 5: Compute world-space target

Using the agent's current position + bearing + distance:

```python
bearing_rad = math.radians(bearing_deg)
target_x = pose.x + math.sin(bearing_rad) * walk_distance
target_y = pose.y + math.cos(bearing_rad) * walk_distance

return WalkToAction(x=target_x, z=target_y)
```

**Coordinate convention** (from `app/world/base.py`):
- yaw 0° = +y (north)
- yaw 90° = +x (east)

---

## Frontend: How walk_to Is Applied (src/agent.js)

The browser receives the action JSON and maps backend coords to Three.js:

```javascript
} else if (action.type === "walk_to") {
    // Backend coords → Three.js coords:
    //   action.x = backend x = Three.js x (east)
    //   action.z = backend y = Three.js -z (north)
    const threeX = action.x;
    const threeZ = -action.z;
    player.walkAgentTo(threeX, threeZ, () => onDone());
}
```

`player.walkAgentTo(x, z)` (in `src/player.js:278`) drives the avatar smoothly toward the target with:
- Collision detection against the GLB collider mesh
- Walk/run animation playback
- `onDone()` callback fires when arrived (triggers next agent tick)

---

## Coordinate Mapping (Critical for Bridging)

```
Backend (Python)          Three.js (Browser)
─────────────────         ──────────────────
pose.x  (east)      →    avatar.position.x
pose.y  (north)     →    -avatar.position.z
pose.z  (up)        →    avatar.position.y
pose.yaw_deg        ←    (180 - headingRad * 180/π + 360) % 360
```

The avatar's `headingRad` in Three.js:
- 0 → facing +Z (south)
- π → facing -Z (north)
- π/2 → facing +X (east)

---

## Action Types (app/world/base.py)

```python
MoveAction(type="move", distance=1.0)       # forward in faced direction
StrafeAction(type="strafe", distance=0.5)   # sidestep, positive=right
WalkToAction(type="walk_to", x=2.5, z=3.1) # absolute world coords
TurnAction(type="turn", degrees=90)         # rotate, positive=clockwise
StopAction(type="stop", reason="found")     # halt
```

---

## The Loop (src/agent.js → app/main.py → app/controller.py)

1. `agent.js:tick()` captures a frame via `capture.requestNextFrame()` (ego-view from avatar's eyes in 3rd person)
2. Sends `POST /agent/step` with `{image_base64, image_width, image_height, pose, goal}`
3. Backend runs perception (Qwen VL) → gets Observation with objects + bboxes
4. If goal visible: `_compute_approach_actions()` → `WalkToAction`
5. If goal not visible: scanning FSM → `TurnAction` or `MoveAction`
6. Response: `{action, turn_type, deviation, goal_status}`
7. `agent.js:applyAction()` maps action to `player.walkAgentTo()` or `player.rotateAgent()`
8. On arrival callback → next `tick()`

---

## Previous Approach (What Didn't Work)

The old system used a **turn-then-move** strategy via the Qwen planner:
- See object on right → turn +45° → move 1m forward
- Next frame: object on left → turn -45° → move 1m forward
- Result: endless oscillation, never converging

The fix: skip the planner entirely for approach. Compute the world-space target geometrically from the bbox and walk there in a straight line. Re-evaluate on arrival. This converges in 2-4 steps for indoor distances.
