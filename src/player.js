/**
 * player.js — Locomotion + camera modes.
 *
 * Two modes (switch via setMode, UI buttons, or V key):
 *
 *   "first" — free-fly camera (Unity: an editor fly-cam / FPS controller).
 *             WASD moves the rig, Q/E fly down/up, mouse look via pointer lock.
 *
 *   "third" — the camera orbits the avatar (Unity: a follow camera boom).
 *             WASD moves the AVATAR relative to the camera direction; the
 *             avatar rotates to face its movement and plays idle/walk/run.
 *             Mouse look orbits the boom around the avatar's head.
 *
 * Rig pattern: the camera lives inside `rig` (THREE.Group) — locomotion
 * moves the rig, mouse look rotates the camera.
 *
 * Collision comes from collision.js (setCollider); without it the avatar
 * moves freely on its horizontal plane.
 */
import * as THREE from "three";
import { PointerLockControls } from "three/addons/controls/PointerLockControls.js";

export function createPlayer({
  camera,
  domElement,
  eyeHeight = 1.6,
  speed = 5,
  sprintMultiplier = 4,
  thirdPerson = {},
} = {}) {
  const tp = {
    distance: thirdPerson.distance ?? 4, // boom length (m behind the head)
    headHeight: thirdPerson.headHeight ?? 1.7,
    walkSpeed: thirdPerson.walkSpeed ?? 2,
    runSpeed: thirdPerson.runSpeed ?? 6,
    damping: thirdPerson.damping ?? 12, // camera follow smoothing
  };

  const rig = new THREE.Group();
  rig.name = "playerRig";
  camera.position.set(0, eyeHeight, 0);
  rig.add(camera);

  const controls = new PointerLockControls(camera, domElement);

  function onClick() {
    controls.lock();
  }
  domElement.addEventListener("click", onClick);

  // --- keyboard state (ignored while the user types in a UI field) ---
  const keys = new Set();
  const TRACKED = new Set([
    "KeyW", "KeyA", "KeyS", "KeyD", "KeyQ", "KeyE",
    "ShiftLeft", "ShiftRight",
  ]);
  const isTyping = () => {
    const el = document.activeElement;
    return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);
  };
  function onKeyDown(e) {
    if (isTyping()) return;
    if (TRACKED.has(e.code)) keys.add(e.code);
  }
  function onKeyUp(e) {
    keys.delete(e.code);
  }
  window.addEventListener("keydown", onKeyDown);
  window.addEventListener("keyup", onKeyUp);

  // --- mode state ---
  let mode = "first"; // "first" | "third"
  let avatar = null;  // object returned by loadAvatar()
  let collider = null; // optional, from collision.js (Unity Mesh Collider)
  let snapBoom = false;
  const modeListeners = new Set();

  // --- agent walk target ---
  // When set, updateThird drives the avatar toward this position instead of
  // reading keyboard input. The agent calls walkAgentTo(); the render loop
  // fulfils it frame-by-frame; onDone() fires when the avatar arrives (or
  // gets permanently blocked by a wall).
  let agentTarget = null; // { x, z, onDone: fn | null }

  const BODY_RADIUS = 0.4;  // horizontal clearance to walls/trunks
  const CHEST_HEIGHT = 1.0; // wall ray origin height
  const MAX_STEP_UP = 0.5;  // highest ledge the avatar can walk up
  const BOOM_MARGIN = 0.3;  // keep the camera this far off obstacles

  // Reused temporaries
  const move = new THREE.Vector3();
  const forward = new THREE.Vector3();
  const right = new THREE.Vector3();
  const back = new THREE.Vector3();
  const head = new THREE.Vector3();
  const boomTarget = new THREE.Vector3();
  const worldPos = new THREE.Vector3();

  const sprinting = () => keys.has("ShiftLeft") || keys.has("ShiftRight");

  function readMoveInput(allowVertical) {
    move.set(0, 0, 0);
    camera.getWorldDirection(forward);
    forward.y = 0;
    forward.normalize();
    right.crossVectors(forward, camera.up).normalize();
    if (keys.has("KeyW")) move.add(forward);
    if (keys.has("KeyS")) move.sub(forward);
    if (keys.has("KeyD")) move.add(right);
    if (keys.has("KeyA")) move.sub(right);
    if (allowVertical) {
      if (keys.has("KeyE")) move.y += 1;
      if (keys.has("KeyQ")) move.y -= 1;
    }
    if (move.lengthSq() > 0) move.normalize();
  }

  function updateFirst(delta) {
    readMoveInput(true);
    if (move.lengthSq() === 0) return;
    rig.position.addScaledVector(
      move,
      speed * (sprinting() ? sprintMultiplier : 1) * delta
    );
  }

  function updateThird(delta) {
    if (!avatar) return;
    const pos = avatar.object.position;

    if (agentTarget) {
      // Agent-driven walk: steer avatar toward target, ignore keyboard.
      const dx = agentTarget.x - pos.x;
      const dz = agentTarget.z - pos.z;
      const dist = Math.hypot(dx, dz);

      if (dist < 0.15) {
        // Arrived.
        avatar.setAnimation("idle");
        const cb = agentTarget.onDone;
        agentTarget = null;
        cb?.();
      } else {
        move.set(dx / dist, 0, dz / dist);
        let moving = true;
        if (collider) {
          head.copy(pos);
          head.y += CHEST_HEIGHT;
          if (collider.blocked(head, move, BODY_RADIUS)) moving = false;
        }
        if (moving) {
          pos.addScaledVector(move, tp.walkSpeed * delta);
          avatar.object.rotation.y =
            Math.atan2(move.x, move.z) + (avatar.facingOffset ?? 0);
          avatar.setAnimation("walk");
        } else {
          // Permanently blocked — fire callback anyway so the agent can re-plan.
          avatar.setAnimation("idle");
          const cb = agentTarget.onDone;
          agentTarget = null;
          cb?.(true); // true = blocked
        }
      }
    } else {
      // Keyboard-driven movement (unchanged from original).
      readMoveInput(false);
      let moving = move.lengthSq() > 0;
      if (moving && collider) {
        head.copy(pos);
        head.y += CHEST_HEIGHT;
        if (collider.blocked(head, move, BODY_RADIUS)) moving = false;
      }
      if (moving) {
        const s = sprinting() ? tp.runSpeed : tp.walkSpeed;
        pos.addScaledVector(move, s * delta);
        avatar.object.rotation.y =
          Math.atan2(move.x, move.z) + (avatar.facingOffset ?? 0);
        avatar.setAnimation(sprinting() ? "run" : "walk");
      } else {
        avatar.setAnimation("idle");
      }
    }

    // Ground clamp (Unity: CharacterController grounding). Cast down from
    // just above step height; walk up ledges ≤ MAX_STEP_UP, fall otherwise.
    if (collider) {
      const groundY = collider.groundY(pos.x, pos.z, pos.y + MAX_STEP_UP + 0.1);
      if (groundY !== null && groundY - pos.y <= MAX_STEP_UP) {
        pos.y = groundY;
      }
    }

    // Camera boom: sit `distance` metres behind the head along the current
    // look direction (mouse look = orbit), with smoothing.
    head.copy(pos);
    head.y += tp.headHeight;
    camera.getWorldDirection(forward); // full 3D, including pitch
    let boomLength = tp.distance;
    if (collider) {
      // Camera clip (Unity: boom spherecast) — pull in if something sits
      // between the head and the camera.
      back.copy(forward).negate();
      const hit = collider.blocked(head, back, tp.distance + BOOM_MARGIN);
      if (hit) boomLength = Math.max(0.5, hit.distance - BOOM_MARGIN);
    }
    boomTarget.copy(head).addScaledVector(forward, -boomLength);
    if (snapBoom) {
      rig.position.copy(boomTarget);
      snapBoom = false;
    } else {
      rig.position.lerp(boomTarget, Math.min(1, tp.damping * delta));
    }
  }

  function setMode(next) {
    if (next === mode) return mode;
    if (next === "third") {
      if (!avatar) return mode; // no character yet — stay in first person
      camera.position.set(0, 0, 0); // rig itself becomes the boom origin
      snapBoom = true;
    } else {
      // Continue first-person flight from where the camera currently is.
      camera.getWorldPosition(worldPos);
      camera.position.set(0, eyeHeight, 0);
      rig.position.set(worldPos.x, worldPos.y - eyeHeight, worldPos.z);
    }
    mode = next;
    modeListeners.forEach((fn) => fn(mode));
    return mode;
  }

  function update(delta) {
    if (mode === "third") updateThird(delta);
    else updateFirst(delta);
  }

  function dispose() {
    domElement.removeEventListener("click", onClick);
    window.removeEventListener("keydown", onKeyDown);
    window.removeEventListener("keyup", onKeyUp);
    controls.dispose();
  }

  return {
    rig,
    controls,
    update,
    dispose,
    setMode,
    get mode() {
      return mode;
    },
    setAvatar(a) {
      avatar = a;
    },
    setCollider(c) {
      collider = c;
    },
    /** Live-tune movement/camera from the UI panel. */
    setTuning({ walkSpeed, runSpeed, distance, flySpeed } = {}) {
      if (walkSpeed !== undefined) tp.walkSpeed = walkSpeed;
      if (runSpeed !== undefined) tp.runSpeed = runSpeed;
      if (distance !== undefined) tp.distance = distance;
      if (flySpeed !== undefined) speed = flySpeed;
    },
    onModeChange(fn) {
      modeListeners.add(fn);
    },

    // ---- agent locomotion API -------------------------------------------- //

    /**
     * Walk the avatar smoothly toward (worldX, worldZ) using the existing
     * third-person collision + animation system. `onDone(blocked)` fires when
     * the avatar arrives within 0.15 m or gets permanently blocked.
     * Has no effect in first-person mode (no avatar to drive).
     */
    walkAgentTo(worldX, worldZ, onDone) {
      agentTarget = { x: worldX, z: worldZ, onDone: onDone ?? null };
    },

    /**
     * Rotate the avatar in place by `degrees` (positive = clockwise).
     * Applies instantly; the next walkAgentTo will use the new heading.
     */
    rotateAgent(degrees) {
      if (!avatar) return;
      // Positive = clockwise → decreasing rotation.y in Three.js (right-hand Y-up).
      avatar.object.rotation.y -= degrees * (Math.PI / 180);
    },

    /**
     * Live avatar position and facing for pose calculation.
     * Returns null in first-person mode or before the avatar is loaded.
     *
     * headingRad = avatar.rotation.y − facingOffset
     *   → 0 when facing +Z (south in Three.js)
     *   → π when facing −Z (north in Three.js)
     * Backend yaw convention: (180 − headingRad × 180/π + 360) % 360
     *   maps Three.js north (headingRad=π) → backend 0° (north) ✓
     */
    getAvatarPose() {
      if (!avatar) return null;
      const pos = avatar.object.position;
      return {
        x: pos.x,
        y: pos.y,
        z: pos.z,
        headingRad: avatar.object.rotation.y - (avatar.facingOffset ?? 0),
      };
    },

    /** Cancel any in-progress agent walk target without firing onDone. */
    clearAgentTarget() {
      agentTarget = null;
    },
  };
}
