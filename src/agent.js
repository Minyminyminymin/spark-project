/**
 * agent.js — Browser-side agent loop for the ScavengeAI demo.
 *
 * ── Frame capture ──────────────────────────────────────────────────────────
 * Uses capture.requestNextFrame() which is fulfilled synchronously inside
 * capture.afterRender() — right after renderer.render(). This guarantees a
 * non-black frame (preserveDrawingBuffer=false clears the canvas between ticks).
 * In third-person mode, capture.js automatically re-renders from the avatar's
 * eye position (ego view) before resolving the promise — exactly what Qwen needs.
 *
 * ── Walking ────────────────────────────────────────────────────────────────
 * Actions are applied via player.walkAgentTo / player.rotateAgent, which hook
 * directly into updateThird's existing collision + animation system:
 *   move  → player.walkAgentTo(targetX, targetZ, onArrived)
 *           avatar walks at walkSpeed (2 m/s), playing the walk animation, with
 *           wall collision. onArrived callback triggers the next agent tick.
 *   turn  → player.rotateAgent(degrees) (instant; positive = clockwise)
 *           then next tick fires immediately.
 *   stop  → loop halts, avatar idles.
 *
 * ── Mode ───────────────────────────────────────────────────────────────────
 * The agent operates in third-person mode so the avatar is visible walking
 * around the scene. capture.js handles the ego-view re-render transparently.
 *
 * ── Coordinate mapping (Three.js → backend Pose) ───────────────────────────
 * Position (from avatar.object.position):
 *   backend.x = avatar.x        (east; same axis)
 *   backend.y = -avatar.z       (Three +Z = south → negate for backend +y = north)
 *   backend.z = avatar.y        (both vertical)
 *
 * Yaw (from avatar heading: avatar.rotation.y − facingOffset):
 *   headingRad = 0   → avatar faces +Z (south)  → backend yaw 180°
 *   headingRad = π   → avatar faces −Z (north)  → backend yaw   0°
 *   headingRad = π/2 → avatar faces +X (east)   → backend yaw  90°
 *   Formula: yaw_deg = (180 − headingRad × 180/π + 360) % 360
 *
 * ── Move target geometry ───────────────────────────────────────────────────
 * forward = (sin(headingRad), 0, cos(headingRad)) in Three.js world space
 *   headingRad = π → forward = (0, 0, −1) = north (−Z) ✓
 *   headingRad = 0 → forward = (0, 0, +1) = south (+Z) ✓
 *   headingRad = π/2 → forward = (1, 0, 0) = east (+X) ✓
 * target = avatarPos + forward × distance
 */

import * as THREE from "three";

const BACKEND_URL = "http://localhost:8000";

export function createAgent({ player, capture, camera, ui }) {
  let running = false;
  let loopTimer = null;
  let lastAction = null;
  let lastGoalStatus = "idle";
  let lastDeviation = false;
  let activeAbort = null; // AbortController for the in-flight fetch

  // ── Pose from live avatar state ───────────────────────────────────────────

  function getPose(avatarPose) {
    // avatarPose from player.getAvatarPose(); falls back to rig if no avatar.
    if (avatarPose) {
      const { x, y, z, headingRad } = avatarPose;
      const yaw_deg = ((180 - headingRad * 180 / Math.PI) + 360) % 360;
      return { x, y: -z, z: y, yaw_deg };
    }
    // First-person fallback: use camera world direction for yaw.
    const dir = new THREE.Vector3();
    camera.getWorldDirection(dir);
    const yaw_deg = Math.atan2(dir.x, -dir.z) * (180 / Math.PI);
    const p = player.rig.position;
    return { x: p.x, y: -p.z, z: p.y, yaw_deg };
  }

  // ── Move target: avatar forward × distance ────────────────────────────────

  function computeMoveTarget(distance) {
    const ap = player.getAvatarPose();
    if (!ap) {
      // First-person fallback: move along camera direction
      const dir = new THREE.Vector3();
      camera.getWorldDirection(dir);
      dir.y = 0;
      dir.normalize();
      const p = player.rig.position;
      return { x: p.x + dir.x * distance, z: p.z + dir.z * distance };
    }
    // forward = (sin(headingRad), 0, cos(headingRad)) — see file header
    const { x, z, headingRad } = ap;
    return {
      x: x + Math.sin(headingRad) * distance,
      z: z + Math.cos(headingRad) * distance,
    };
  }

  // ── Apply action ──────────────────────────────────────────────────────────

  // ── Strafe target: move sideways relative to avatar heading ──────────────

  function computeStrafeTarget(distance) {
    const ap = player.getAvatarPose();
    if (!ap) {
      // First-person fallback: strafe along camera right vector
      const dir = new THREE.Vector3();
      camera.getWorldDirection(dir);
      dir.y = 0;
      dir.normalize();
      // right = cross(forward, up)
      const rightVec = new THREE.Vector3().crossVectors(dir, new THREE.Vector3(0, 1, 0)).normalize();
      const p = player.rig.position;
      return { x: p.x + rightVec.x * distance, z: p.z + rightVec.z * distance };
    }
    // right = (cos(headingRad), 0, -sin(headingRad)) in Three.js world space
    const { x, z, headingRad } = ap;
    return {
      x: x + Math.cos(headingRad) * distance,
      z: z - Math.sin(headingRad) * distance,
    };
  }

  function applyAction(action, onDone) {
    if (!action) { onDone(); return; }

    if (action.type === "move") {
      const distance = action.distance ?? 1.0;
      const target = computeMoveTarget(distance);
      // Switch to third-person for visible walking (no-op if already there).
      if (player.mode !== "third") {
        player.setMode("third");
        ui.setViewMode("third");
      }
      player.walkAgentTo(target.x, target.z, () => onDone());

    } else if (action.type === "strafe") {
      const distance = action.distance ?? 1.0;
      const target = computeStrafeTarget(distance);
      if (player.mode !== "third") {
        player.setMode("third");
        ui.setViewMode("third");
      }
      player.walkAgentTo(target.x, target.z, () => onDone());

    } else if (action.type === "walk_to") {
      // walk_to has absolute world coords in backend convention:
      //   action.x = backend x = Three.js x
      //   action.z = backend y = -Three.js z
      const threeX = action.x;
      const threeZ = -action.z;
      if (player.mode !== "third") {
        player.setMode("third");
        ui.setViewMode("third");
      }
      player.walkAgentTo(threeX, threeZ, () => onDone());

    } else if (action.type === "turn") {
      const degrees = action.degrees ?? 90;
      if (player.mode === "third") {
        player.rotateAgent(degrees);
      } else {
        // First-person: rotate rig (camera parent)
        player.rig.rotation.y -= degrees * (Math.PI / 180);
      }
      // Turn is instant; proceed immediately.
      onDone();

    } else {
      // stop or unknown — no movement
      onDone();
    }
  }

  // ── Core tick ─────────────────────────────────────────────────────────────

  async function tick(inlineGoal) {
    if (!running) return;

    // Capture must happen after render — requestNextFrame() is fulfilled inside
    // capture.afterRender(), so the canvas is guaranteed populated.
    const frame = await capture.requestNextFrame();
    if (!frame) {
      ui.addMessage("Agent", "Frame capture failed — stopping.");
      _stop();
      return;
    }

    const image_base64 = frame.image.slice(frame.image.indexOf(",") + 1);
    const image_width = 640;
    const image_height = Math.round(640 / frame.camera.aspect);

    // Use the pose AT CAPTURE TIME (avatar position when the image was taken).
    const captureAvatarPos = frame.player?.avatarPosition; // [x, y, z] or null
    const captureHeading = frame.player?.heading ?? 0;     // radians
    let pose;
    if (captureAvatarPos) {
      const yaw_deg = ((180 - captureHeading * 180 / Math.PI) + 360) % 360;
      pose = { x: captureAvatarPos[0], y: -captureAvatarPos[2], z: captureAvatarPos[1], yaw_deg };
    } else {
      pose = getPose(null); // rig fallback
    }

    let resp;
    try {
      activeAbort = new AbortController();
      const body = { image_base64, image_width, image_height, pose };
      if (inlineGoal) body.goal = inlineGoal;
      const res = await fetch(`${BACKEND_URL}/agent/step`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: activeAbort.signal,
      });
      activeAbort = null;
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`HTTP ${res.status}: ${text.slice(0, 200)}`);
      }
      resp = await res.json();
    } catch (err) {
      if (err.name === "AbortError") return; // stopped cleanly mid-flight
      ui.addMessage("Agent", `Backend error: ${err.message}`);
      _stop();
      return;
    }

    lastAction = resp.action;
    lastGoalStatus = resp.goal_status;
    lastDeviation = resp.deviation;

    ui.setAgentStatus({
      running: true,
      action: resp.action,
      goalStatus: resp.goal_status,
      deviation: resp.deviation,
    });

    if (resp.action?.type === "stop") {
      // Apply stop (no movement) then end the loop.
      applyAction(resp.action, () => {});
      _stop();
      ui.addMessage("Agent", `Done — ${resp.action.reason ?? resp.goal_status}`);
      return;
    }

    // Apply the action; when done (walk complete or turn instant), fire next tick.
    applyAction(resp.action, () => {
      if (running) tick(); // no inline goal after the first tick
    });
  }

  // ── Public API ────────────────────────────────────────────────────────────

  function start(goal) {
    if (running) return;
    running = true;
    lastAction = null;
    lastGoalStatus = "searching";
    lastDeviation = false;

    // Third-person: avatar walks visibly; capture.js produces ego-view frames.
    if (player.mode !== "third") {
      player.setMode("third");
      ui.setViewMode("third");
    }

    ui.setAgentStatus({ running: true, action: null, goalStatus: "searching", deviation: false });
    ui.addMessage("Agent", goal ? `Goal: "${goal}"` : "Starting — using active goal.");

    tick(goal || null);
  }

  function _stop() {
    running = false;
    clearTimeout(loopTimer);
    loopTimer = null;
    activeAbort?.abort(); // cancel any in-flight fetch immediately
    activeAbort = null;
    player.clearAgentTarget();
    ui.setAgentStatus({
      running: false,
      action: lastAction,
      goalStatus: lastGoalStatus,
      deviation: lastDeviation,
    });
  }

  function stop() {
    _stop();
    ui.addMessage("Agent", "Stopped.");
  }

  return { start, stop, isRunning: () => running };
}
