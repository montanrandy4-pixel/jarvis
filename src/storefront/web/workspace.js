/* The shop, as a place you can walk around.
 *
 * Every visual choice carries information rather than decoration:
 *   position  -- a stable grid slot, so a product stays where you last saw it
 *   height    -- price, so the catalogue reads as a skyline
 *   colour    -- health: deliverable, needs attention, cannot be delivered
 *   pulsing   -- a blocker, which is money being lost right now
 *   the sweep -- the agent actually working, tinted by which check is running
 */
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { onUpdate, escapeHtml } from './hud.js';

const COLOR = {
  ok:      0x5FB49C,
  warning: 0xD9A441,
  blocker: 0xE0603F,
  note:    0x5FB49C,
};
const STAGE_TINT = { catalogue: 0x5FB49C, orders: 0xD9A441, storefront: 0x8FB3E8 };

const canvas = document.getElementById('stage');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x15171C);
scene.fog = new THREE.Fog(0x15171C, 26, 68);

const camera = new THREE.PerspectiveCamera(46, 1, 0.1, 200);
camera.position.set(13, 11, 17);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.07;
controls.maxPolarAngle = Math.PI * 0.48;
controls.minDistance = 7;
controls.maxDistance = 46;
controls.target.set(0, 1.2, 0);

scene.add(new THREE.AmbientLight(0xffffff, 0.55));
const key = new THREE.DirectionalLight(0xfff4e6, 1.15);
key.position.set(9, 16, 8);
scene.add(key);
const rim = new THREE.DirectionalLight(0x7fa9d0, 0.42);
rim.position.set(-10, 6, -9);
scene.add(rim);

const grid = new THREE.GridHelper(60, 60, 0x2C3038, 0x1E2128);
grid.position.y = -0.01;
scene.add(grid);

const floor = new THREE.Mesh(
  new THREE.CircleGeometry(30, 64).rotateX(-Math.PI / 2),
  new THREE.MeshStandardMaterial({ color: 0x1A1D23, roughness: 0.95, metalness: 0 })
);
floor.position.y = -0.02;
scene.add(floor);

/* ---- the sweep: proof the agent is alive ---- */
const sweep = new THREE.Mesh(
  new THREE.RingGeometry(0.4, 0.62, 80).rotateX(-Math.PI / 2),
  new THREE.MeshBasicMaterial({ color: COLOR.ok, transparent: true, opacity: 0 })
);
sweep.position.y = 0.04;
scene.add(sweep);
let sweepT = -1;

function startSweep(stage) {
  sweepT = 0;
  sweep.material.color.setHex(STAGE_TINT[stage] ?? COLOR.ok);
}

/* ---- products ---- */
const cards = new Map();           // title -> mesh
const group = new THREE.Group();
scene.add(group);

const BOX = new THREE.BoxGeometry(1, 1, 1);

function layout(n) {
  const cols = Math.max(1, Math.ceil(Math.sqrt(n)));
  return { cols, gap: 2.35, offset: ((Math.max(1, Math.ceil(n / cols))) - 1) / 2 };
}

function syncProducts(products) {
  const seen = new Set();
  const { cols, gap, offset } = layout(products.length || 1);
  const half = (cols - 1) / 2;

  products.forEach((p, i) => {
    seen.add(p.title);
    const state = p.state === 'note' ? 'ok' : p.state;
    const height = Math.max(0.7, Math.min(4.2, (p.price ? p.price / 38 : 1) + 0.6));
    let mesh = cards.get(p.title);

    if (!mesh) {
      mesh = new THREE.Mesh(BOX, new THREE.MeshStandardMaterial({
        color: COLOR[state] ?? COLOR.ok, roughness: 0.42, metalness: 0.12,
        emissive: new THREE.Color(COLOR[state] ?? COLOR.ok), emissiveIntensity: 0.06,
      }));
      mesh.scale.set(1.5, 0.05, 1.5);
      group.add(mesh);
      cards.set(p.title, mesh);
      mesh.userData.grow = height;          // animate up on first sight
    } else {
      mesh.userData.grow = height;
    }

    mesh.userData.product = p;
    mesh.userData.state = state;
    mesh.material.color.setHex(COLOR[state] ?? COLOR.ok);
    mesh.material.emissive.setHex(COLOR[state] ?? COLOR.ok);

    mesh.position.x = ((i % cols) - half) * gap;
    mesh.position.z = (Math.floor(i / cols) - offset) * gap;
  });

  for (const [title, mesh] of cards) {
    if (!seen.has(title)) { group.remove(mesh); cards.delete(title); }
  }
}

/* ---- orders: a sale you can feel ---- */
const motes = [];
function dropMote() {
  const mote = new THREE.Mesh(
    new THREE.SphereGeometry(0.17, 16, 16),
    new THREE.MeshBasicMaterial({ color: 0xF4F1EA, transparent: true, opacity: 0.95 })
  );
  mote.position.set((Math.random() - 0.5) * 12, 13 + Math.random() * 4, (Math.random() - 0.5) * 12);
  mote.userData.v = 0;
  scene.add(mote);
  motes.push(mote);
}

/* ---- hover ---- */
const ray = new THREE.Raycaster();
const pointer = new THREE.Vector2();
const tip = document.getElementById('tip');
let hovered = null;

addEventListener('pointermove', (e) => {
  pointer.x = (e.clientX / innerWidth) * 2 - 1;
  pointer.y = -(e.clientY / innerHeight) * 2 + 1;
  tip.style.left = `${Math.min(e.clientX + 14, innerWidth - 300)}px`;
  tip.style.top = `${e.clientY + 16}px`;
});

function updateHover() {
  ray.setFromCamera(pointer, camera);
  const hit = ray.intersectObjects([...cards.values()], false)[0];
  const mesh = hit ? hit.object : null;
  if (mesh === hovered) return;
  hovered = mesh;
  if (!mesh) { tip.hidden = true; return; }
  const p = mesh.userData.product;
  tip.innerHTML =
    `<b>${escapeHtml(p.title)}</b>` +
    (p.problems?.length
      ? p.problems.map((x) => `<div class="p">${escapeHtml(x)}</div>`).join('')
      : `<div class="muted">deliverable</div>`);
  tip.hidden = false;
}

/* ---- data, from the HUD layer ---- */
onUpdate((kind, payload) => {
  if (kind === 'state') syncProducts(payload.products || []);
  else if (kind === 'scan') { startSweep(payload); if (payload === 'orders') dropMote(); }
});

/* ---- loop ---- */
function resize() {
  const w = innerWidth, h = innerHeight;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
addEventListener('resize', resize);
resize();

const clock = new THREE.Clock();
renderer.setAnimationLoop(() => {
  const dt = Math.min(clock.getDelta(), 0.05);
  const t = clock.elapsedTime;

  for (const mesh of cards.values()) {
    const target = mesh.userData.grow ?? 1;
    mesh.scale.y += (target - mesh.scale.y) * Math.min(1, dt * 4);
    mesh.position.y = mesh.scale.y / 2;
    // A blocker breathes, so your eye finds it without reading anything.
    mesh.material.emissiveIntensity = mesh.userData.state === 'blocker'
      ? 0.3 + Math.sin(t * 3.4) * 0.22
      : mesh.userData.state === 'warning' ? 0.13 : 0.06;
  }

  if (sweepT >= 0) {
    sweepT += dt * 0.55;
    const r = sweepT * 26;
    sweep.scale.setScalar(Math.max(0.001, r));
    sweep.material.opacity = Math.max(0, 0.5 * (1 - sweepT));
    if (sweepT > 1) { sweepT = -1; sweep.material.opacity = 0; }
  }

  for (let i = motes.length - 1; i >= 0; i--) {
    const mote = motes[i];
    mote.userData.v += dt * 13;
    mote.position.y -= mote.userData.v * dt;
    if (mote.position.y <= 0.2) {
      scene.remove(mote);
      mote.geometry.dispose();
      mote.material.dispose();
      motes.splice(i, 1);
    }
  }

  updateHover();
  controls.update();
  renderer.render(scene, camera);
});
