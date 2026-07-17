/**
 * collision.js — Unity-style Mesh Collider for the environment GLB.
 *
 * The Gaussian splat is just "paint" — it has no surface to collide with.
 * The trick (same as Unity): keep an INVISIBLE triangle mesh (Tree.glb)
 * aligned under the splat and do all physics queries against it.
 *
 * Uses three-mesh-bvh so raycasts against ~100K-triangle meshes cost
 * well under a millisecond per frame.
 *
 * Provided queries (all in world space):
 *   groundY(x, z, fromY)              → terrain height under a point, or null
 *   blocked(origin, dir, maxDist)     → first hit within maxDist, or null
 */
import * as THREE from "three";
import {
  computeBoundsTree,
  disposeBoundsTree,
  acceleratedRaycast,
} from "three-mesh-bvh";

// Register the BVH-accelerated raycast globally (idempotent).
THREE.BufferGeometry.prototype.computeBoundsTree = computeBoundsTree;
THREE.BufferGeometry.prototype.disposeBoundsTree = disposeBoundsTree;
THREE.Mesh.prototype.raycast = acceleratedRaycast;

/**
 * Build a collider from any Object3D hierarchy (e.g. the environment GLB).
 * Call AFTER the object is in the scene; transforms are read per query via
 * matrixWorld, so moving/scaling the environment root keeps working.
 */
export function createCollider(object) {
  const meshes = [];
  object.traverse((child) => {
    if (child.isMesh && child.geometry) {
      child.geometry.computeBoundsTree(); // one-time BVH build
      // Raycaster honours material.side — scan meshes and hole-filled
      // patches have inconsistent winding, so collide with BOTH faces.
      const mats = Array.isArray(child.material) ? child.material : [child.material];
      for (const mat of mats) if (mat) mat.side = THREE.DoubleSide;
      meshes.push(child);
    }
  });

  if (meshes.length === 0) {
    console.warn(
      "[collision] No triangle meshes found — the object is probably a " +
      "point cloud (e.g. a splat exported as GLB points). Use primitive " +
      "colliders instead (see main.js CONFIG.environment.colliders)."
    );
  }

  const raycaster = new THREE.Raycaster();
  raycaster.firstHitOnly = true; // BVH fast path

  const down = new THREE.Vector3(0, -1, 0);
  const origin = new THREE.Vector3();

  return {
    /** Number of triangle meshes found (0 → this collider is useless). */
    meshCount: meshes.length,

    /** Height of the surface under (x, z), casting down from fromY. */
    groundY(x, z, fromY = 100, maxDrop = 200) {
      origin.set(x, fromY, z);
      raycaster.set(origin, down);
      raycaster.far = maxDrop;
      const hit = raycaster.intersectObjects(meshes, false)[0];
      return hit ? hit.point.y : null;
    },

    /** First obstacle along dir (normalized) within maxDist, else null. */
    blocked(from, dir, maxDist) {
      raycaster.set(from, dir);
      raycaster.far = maxDist;
      return raycaster.intersectObjects(meshes, false)[0] ?? null;
    },

    dispose() {
      for (const mesh of meshes) mesh.geometry.disposeBoundsTree();
    },
  };
}

/**
 * Unity-style PRIMITIVE colliders, for when no collision mesh exists
 * (e.g. the environment GLB turned out to be a point cloud). Returns an
 * invisible Group of simple shapes — add it to the scene, then feed it to
 * createCollider(). Toggle .visible to inspect it like a Unity gizmo.
 *
 * @param {object} cfg
 * @param {object} cfg.ground    { y, size } horizontal floor plane
 * @param {Array}  cfg.cylinders [{ x, z, radius, height }] e.g. tree trunks
 */
export function createPrimitiveColliders({ ground, cylinders = [] } = {}) {
  const group = new THREE.Group();
  group.name = "PrimitiveColliders";

  // Rendered only when toggled visible (M key) — acts as a collider gizmo.
  const gizmoMat = new THREE.MeshBasicMaterial({
    color: 0x44ff66,
    wireframe: true,
    transparent: true,
    opacity: 0.45,
  });

  if (ground) {
    const plane = new THREE.Mesh(
      new THREE.PlaneGeometry(ground.size ?? 200, ground.size ?? 200),
      gizmoMat
    );
    plane.rotation.x = -Math.PI / 2; // horizontal, facing up
    plane.position.y = ground.y ?? 0;
    group.add(plane);
  }

  for (const c of cylinders) {
    const cyl = new THREE.Mesh(
      new THREE.CylinderGeometry(c.radius, c.radius * 1.3, c.height, 12),
      gizmoMat
    );
    cyl.position.set(c.x ?? 0, (c.y ?? 0) + c.height / 2, c.z ?? 0);
    group.add(cyl);
  }

  group.visible = false; // invisible collider, like Unity
  return group;
}
