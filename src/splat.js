/**
 * splat.js — Gaussian splat loading via Spark.
 *
 * Spark is a RENDERER only: it loads existing splat files
 * (.ply / .spz / .splat / .ksplat / .sog / .zip / .rad).
 * It cannot convert OBJ/GLB meshes into splats.
 */
import { SplatMesh } from "@sparkjsdev/spark";

/**
 * Create a SplatMesh and return it immediately (loads async).
 * Await `splat.initialized` or pass `onLoad` to know when it's ready.
 *
 * @param {object} opts
 * @param {string}  opts.url        Splat file URL (e.g. "/splats/scene.ply")
 * @param {boolean} opts.lod        Build an LOD tree in a background worker
 *                                  (recommended for large scenes and Quest)
 * @param {boolean} opts.flipped    Most 3DGS captures use a y-down convention;
 *                                  rotates 180° about X so the scene is upright.
 *                                  Set false if your scene appears upside down.
 * @param {number[]} opts.position  [x, y, z]
 * @param {number}  opts.scale      Uniform scale (splats only scale uniformly)
 * @param {function} opts.onProgress ProgressEvent callback while downloading
 * @param {function} opts.onLoad     Called when fully initialized
 */
export function loadSplat({
  url,
  lod = true,
  flipped = true,
  position = [0, 0, 0],
  scale = 1,
  onProgress,
  onLoad,
} = {}) {
  const splat = new SplatMesh({ url, lod, onProgress, onLoad });

  if (flipped) splat.quaternion.set(1, 0, 0, 0);
  splat.position.set(...position);
  splat.scale.setScalar(scale);

  // NOTE: freshly loaded SplatMeshes can stay invisible with a static
  // camera (Spark 2.1 quirk) — see the "splat kick" in main.js's render
  // loop for the workaround and full explanation.

  return splat;
}
