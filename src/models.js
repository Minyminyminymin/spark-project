/**
 * models.js — Triangle-mesh loading (GLB/GLTF) — development plan step 4.
 *
 * GLB/GLTF files are ordinary Three.js meshes. Spark renders Gaussian splats
 * alongside them in the same scene (splats are depth-tested against meshes),
 * which is exactly the coexistence question this research investigates.
 *
 * Note: unlike splats, meshes need lights (created in scene.js).
 */
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { PLYLoader } from "three/addons/loaders/PLYLoader.js";

const loader = new GLTFLoader();
const plyLoader = new PLYLoader();

/**
 * Load a GLB/GLTF file and return { object, gltf }.
 *
 * @param {object} opts
 * @param {string}   opts.url       Model URL (e.g. "/models/castle.glb")
 * @param {number[]} opts.position  [x, y, z]
 * @param {number[]} opts.rotation  Euler [x, y, z] in radians
 * @param {number}   opts.scale     Uniform scale
 * @param {boolean}  opts.visible   Initial visibility
 */
export async function loadModel({
  url,
  position = [0, 0, 0],
  rotation = [0, 0, 0],
  scale = 1,
  visible = true,
} = {}) {
  const gltf = await loader.loadAsync(url);

  const object = gltf.scene;
  object.position.set(...position);
  object.rotation.set(...rotation);
  object.scale.setScalar(scale);
  object.visible = visible;

  return { object, gltf };
}

/**
 * Load a PLY point cloud (e.g. a Polycam scan export) as THREE.Points.
 * Fallback visualization for scans that aren't real gaussian splats —
 * position + RGB only, rendered as small view-scaled dots.
 *
 * @param {object} opts
 * @param {string} opts.url        PLY url (point cloud, no faces needed)
 * @param {number} opts.pointSize  World-space dot size in metres
 * @param {function} opts.onProgress
 */
export async function loadPointCloud({ url, pointSize = 0.01, onProgress } = {}) {
  const geometry = await plyLoader.loadAsync(url, onProgress);

  // Polycam writes double-precision coordinates; PLYLoader keeps them as
  // Float64Array, which WebGL cannot upload ("Unsupported buffer data
  // format" — this silently killed the whole render loop). Convert every
  // float64 attribute to float32 (millimetre-scale precision is plenty).
  for (const name of Object.keys(geometry.attributes)) {
    const attr = geometry.attributes[name];
    if (attr.array instanceof Float64Array) {
      geometry.setAttribute(
        name,
        new THREE.BufferAttribute(new Float32Array(attr.array), attr.itemSize, attr.normalized)
      );
    }
  }

  const material = new THREE.PointsMaterial({
    size: pointSize,
    sizeAttenuation: true,
    vertexColors: geometry.hasAttribute("color"),
  });
  const object = new THREE.Points(geometry, material);
  return { object, geometry, material, count: geometry.attributes.position.count };
}
