/**
 * scene.js — Renderer, scene, camera, SparkRenderer.
 *
 * Owns the core Three.js objects. Everything else (player, splats, XR)
 * receives these via the context object returned by createScene().
 */
import * as THREE from "three";
import { SparkRenderer } from "@sparkjsdev/spark";

export function createScene({ container = document.body, background = 0x0e0e12 } = {}) {
  // Spark docs: antialias must stay OFF — WebGL MSAA doesn't improve
  // Gaussian splat quality and significantly reduces performance.
  const renderer = new THREE.WebGLRenderer({ antialias: false });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(window.innerWidth, window.innerHeight);
  container.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(background);

  const camera = new THREE.PerspectiveCamera(
    60,
    window.innerWidth / window.innerHeight,
    0.05,
    1000
  );

  // Spark requires a SparkRenderer instance somewhere in the scene graph.
  // It performs splat sorting/LOD outside the normal render pass.
  // (In WebXR, Spark automatically switches preUpdate off for low latency.)
  const spark = new SparkRenderer({ renderer });
  scene.add(spark);

  // Lights only affect triangle meshes (GLB/OBJ). Splats have baked lighting.
  const hemi = new THREE.HemisphereLight(0xffffff, 0x445566, 1.5);
  const sun = new THREE.DirectionalLight(0xffffff, 1.5);
  sun.position.set(3, 8, 5);
  scene.add(hemi, sun);

  function onResize() {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
  }
  window.addEventListener("resize", onResize);

  function dispose() {
    window.removeEventListener("resize", onResize);
    renderer.dispose();
  }

  return { renderer, scene, camera, spark, dispose };
}
