/**
 * worldstate.js — Read-only query API over the tagged objects
 * (splat-analyzer-plan.md, Phase B: "the AI bridge").
 *
 * This is the contract a future agent.js (Phase D, not built here) will
 * consume. Deliberately free of DOM/UI dependencies so it stays reusable —
 * everything it returns is plain, JSON-safe data in WORLD coordinates.
 */
import * as THREE from "three";

const _pos = new THREE.Vector3();
const _dir = new THREE.Vector3();
const COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];

// 0 = facing world -Z ("north"), increasing clockwise toward +X ("east") —
// matches minimap.js's convention (map "up" is world -Z).
function headingFromDirection(dir) {
  return Math.atan2(dir.x, -dir.z);
}

function compassLabel(angleRad) {
  const deg = (THREE.MathUtils.radToDeg(angleRad) + 360) % 360;
  return COMPASS[Math.round(deg / 45) % 8];
}

/**
 * @param {object} deps
 * @param {ReturnType<import("./annotations.js").createAnnotations>} deps.annotations
 * @param {ReturnType<import("./player.js").createPlayer>} deps.player
 * @param {THREE.Camera} deps.camera
 * @param {() => (ReturnType<import("./avatar.js").loadAvatar> | null)} deps.getAvatar
 *   Lazily-resolved: the avatar loads async, so this reads main.js's live
 *   `avatar` binding rather than a snapshot taken at creation time.
 */
export function createWorldState({ annotations, player, camera, getAvatar }) {
  function playerState() {
    const avatar = getAvatar?.();
    let heading;
    if (avatar && player.mode === "third") {
      _pos.copy(avatar.object.position);
      heading = avatar.object.rotation.y - (avatar.facingOffset ?? 0);
    } else {
      camera.getWorldPosition(_pos);
      camera.getWorldDirection(_dir);
      heading = headingFromDirection(_dir);
    }
    return {
      position: [_pos.x, _pos.y, _pos.z],
      heading,
      mode: player.mode,
    };
  }

  function listObjects() {
    const { position: playerPos } = playerState();
    return annotations.getObjects().map((o) => {
      const w = o.worldPosition;
      const distanceFromPlayer = Math.hypot(
        w.x - playerPos[0],
        w.y - playerPos[1],
        w.z - playerPos[2]
      );
      return {
        id: o.id,
        label: o.label,
        aliases: o.aliases ?? [],
        position: [w.x, w.y, w.z],
        radius: o.radius,
        distanceFromPlayer,
      };
    });
  }

  /** Best match by label/alias (case-insensitive, substring ok), or null. */
  function findObject(query) {
    if (!query) return null;
    const q = query.trim().toLowerCase();
    if (!q) return null;
    const all = listObjects();
    const exact = all.find(
      (o) => o.label.toLowerCase() === q || o.aliases.some((a) => a.toLowerCase() === q)
    );
    if (exact) return exact;
    const partial = all.find(
      (o) => o.label.toLowerCase().includes(q) || o.aliases.some((a) => a.toLowerCase().includes(q))
    );
    return partial ?? null;
  }

  function nearest(k = 3) {
    return listObjects()
      .sort((a, b) => a.distanceFromPlayer - b.distanceFromPlayer)
      .slice(0, k);
  }

  /** Compact single-string summary for LLM context — kept under maxLen. */
  function describeScene(maxLen = 500) {
    const { position: pos, heading } = playerState();
    const all = listObjects().sort((a, b) => a.distanceFromPlayer - b.distanceFromPlayer);
    const shown = all.slice(0, 8);
    const parts = shown.map((o) => {
      const bearing = compassLabel(Math.atan2(o.position[0] - pos[0], -(o.position[2] - pos[2])));
      return `${o.id} (${o.distanceFromPlayer.toFixed(1)}m ${bearing})`;
    });
    const suffix = all.length > shown.length ? `, +${all.length - shown.length} more` : "";
    const objectsStr = parts.length > 0 ? parts.join(", ") + suffix : "none";
    const facing = compassLabel(heading);

    let summary =
      `Objects: ${objectsStr}. Player at ` +
      `(${pos[0].toFixed(1)}, ${pos[1].toFixed(1)}, ${pos[2].toFixed(1)}) facing ${facing}.`;
    if (summary.length > maxLen) summary = `${summary.slice(0, maxLen - 1)}…`;
    return summary;
  }

  return { listObjects, findObject, nearest, playerState, describeScene };
}
