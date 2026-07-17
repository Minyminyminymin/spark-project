/**
 * avatar.js — GLB character loading + animation control (plan step 7).
 *
 * Character used: three.js example "Soldier.glb" (Mixamo character,
 * distributed with three.js examples; Idle / Walk / Run / TPose clips).
 * Any humanoid GLB with similar clips works — set CONFIG.avatar.url.
 *
 * GLB characters are ordinary Three.js meshes: they need lights
 * (scene.js provides them) and coexist with Gaussian splats.
 */
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

const loader = new GLTFLoader();

/**
 * @param {object} opts
 * @param {string}   opts.url          GLB url (local "/models/x.glb" or remote)
 * @param {number[]} opts.position     [x, y, z]
 * @param {number}   opts.scale        Uniform scale
 * @param {number}   opts.facingOffset Y-rotation (radians) added so the model
 *                                     faces its movement direction. Soldier.glb
 *                                     rests facing -Z, so it needs Math.PI.
 */
export async function loadAvatar({
  url,
  position = [0, 0, 0],
  scale = 1,
  facingOffset = 0,
} = {}) {
  const gltf = await loader.loadAsync(url);

  const object = gltf.scene;
  object.position.set(...position);
  object.scale.setScalar(scale);

  let mixer = null;
  const actions = new Map();
  if (gltf.animations.length > 0) {
    mixer = new THREE.AnimationMixer(object);
    for (const clip of gltf.animations) {
      actions.set(clip.name.toLowerCase(), mixer.clipAction(clip));
    }
  }

  let current = null;

  // Find a clip by exact (case-insensitive) or partial name.
  function findAction(name) {
    const key = name.toLowerCase();
    if (actions.has(key)) return actions.get(key);
    for (const [clipName, action] of actions) {
      if (clipName.includes(key)) return action;
    }
    return null;
  }

  return {
    object,
    mixer,
    facingOffset,
    animations: gltf.animations.map((c) => c.name),

    /** Crossfade to the named clip ("idle" / "walk" / "run"). */
    setAnimation(name, fadeSeconds = 0.25) {
      const next = findAction(name);
      if (!next || next === current) return;
      next.enabled = true;
      next.reset().play();
      if (current) current.crossFadeTo(next, fadeSeconds, false);
      current = next;
    },

    /** Call every frame with the render delta. */
    update(delta) {
      mixer?.update(delta);
    },
  };
}
