// «Кат-сцена»: интерьер клуба, свои воксельные герои, мимика, жесты и планы камеры по монтажному плану.
// Всё состояние — чистая функция номера кадра (детерминированный рендер, без rAF/Date/Math.random).
import * as THREE from 'three';

const qs = new URLSearchParams(location.search);
const W = +qs.get('w') || 720, H = +qs.get('h') || 1280;
let FPS = 30;

function mulberry32(a) { return () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
const hash3 = (x, y, z) => { let h = (x * 374761393 + y * 668265263 + z * 2147483647) | 0; h = Math.imul(h ^ (h >>> 13), 1274126177); return ((h ^ (h >>> 16)) >>> 0) / 4294967296; };
const lerp = (a, b, k) => a + (b - a) * k;
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const ease = k => k * k * (3 - 2 * k);

// ---------- рендерер ----------
const renderer = new THREE.WebGLRenderer({ antialias: false, preserveDrawingBuffer: false, powerPreference: 'high-performance' });
renderer.setPixelRatio(1); renderer.setSize(W, H);
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.25;
renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFShadowMap;
document.body.appendChild(renderer.domElement);
const canvas = renderer.domElement, gl = renderer.getContext();
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0b0a14);
scene.fog = new THREE.Fog(0x0b0a14, 260, 520);
const camera = new THREE.PerspectiveCamera(34, W / H, 1, 1500);

const ramp = new THREE.DataTexture(new Uint8Array([80, 160, 255]), 3, 1, THREE.RedFormat);
ramp.minFilter = ramp.magFilter = THREE.NearestFilter; ramp.needsUpdate = true;

// ---------- воксельный мешер (только видимые грани, цвет по вершинам) ----------
const FACES = [
  { n: [1, 0, 0], c: [[1, 0, 0], [1, 1, 0], [1, 1, 1], [1, 0, 1]] }, { n: [-1, 0, 0], c: [[0, 0, 1], [0, 1, 1], [0, 1, 0], [0, 0, 0]] },
  { n: [0, 1, 0], c: [[0, 1, 1], [1, 1, 1], [1, 1, 0], [0, 1, 0]] }, { n: [0, -1, 0], c: [[0, 0, 0], [1, 0, 0], [1, 0, 1], [0, 0, 1]] },
  { n: [0, 0, 1], c: [[1, 0, 1], [1, 1, 1], [0, 1, 1], [0, 0, 1]] }, { n: [0, 0, -1], c: [[0, 0, 0], [0, 1, 0], [1, 1, 0], [1, 0, 0]] },
];
for (const f of FACES) {
  const [a, b, c] = f.c; const u = b.map((v, i) => v - a[i]), v = c.map((w, i) => w - a[i]);
  const cr = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]];
  if (cr[0] * f.n[0] + cr[1] * f.n[1] + cr[2] * f.n[2] < 0) f.c.reverse();
}
function voxelGeometry(sx, sy, sz, ox, oy, oz, colorAt) {
  const pos = [], nor = [], col = [], idx = []; const tmp = new THREE.Color();
  const at = (x, y, z) => (x < 0 || y < 0 || z < 0 || x >= sx || y >= sy || z >= sz) ? 0 : colorAt(x, y, z);
  for (let x = 0; x < sx; x++) for (let y = 0; y < sy; y++) for (let z = 0; z < sz; z++) {
    const c = at(x, y, z); if (!c) continue;
    tmp.setHex(c); const j = 0.92 + 0.16 * hash3(x, y, z);
    for (const f of FACES) {
      if (at(x + f.n[0], y + f.n[1], z + f.n[2])) continue;
      const base = pos.length / 3;
      for (const p of f.c) { pos.push(x + p[0] + ox, y + p[1] + oy, z + p[2] + oz); nor.push(...f.n); col.push(tmp.r * j, tmp.g * j, tmp.b * j); }
      idx.push(base, base + 1, base + 2, base, base + 2, base + 3);
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3)); g.setAttribute('normal', new THREE.Float32BufferAttribute(nor, 3));
  g.setAttribute('color', new THREE.Float32BufferAttribute(col, 3)); g.setIndex(idx); return g;
}
const vcMat = new THREE.MeshToonMaterial({ vertexColors: true, gradientMap: ramp });
const voxMesh = (...a) => { const m = new THREE.Mesh(voxelGeometry(...a), vcMat); m.castShadow = m.receiveShadow = true; return m; };
const box = (w, h, d, color, opts = {}) => {
  const mat = opts.emissive ? new THREE.MeshBasicMaterial({ color, fog: false }) : new THREE.MeshToonMaterial({ color, gradientMap: ramp });
  const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat); m.castShadow = !opts.emissive; m.receiveShadow = true; return m;
};

// ---------- герои: голова 10³, туловище 10×11×5, руки 3×11×3, ноги 4×9×4 ----------
const SPEC = {
  gosha: { skin: 0xd9a27a, hair: 0x9a958e, top: 0x3b2a22, inner: 0xe8e4dc, pants: 0x2b2f3a, shoe: 0x1a1a1a, eye: 0x3b4b5a, brow: 0x6e6a64,
           style: 'cap', cap: 0xc2263f, stubble: 0x8d8780, scale: 1.0 },
  nika: { skin: 0xf0c8a8, hair: 0x7b3fd1, top: 0x1f8f8a, inner: 0x1f8f8a, pants: 0x262338, shoe: 0xf2f2f2, eye: 0x2b1d3a, brow: 0x4b2a80,
          style: 'bob', phones: 0x222226, scale: 0.94 },
  palych: { skin: 0xc98f6e, hair: 0x2a2420, top: 0x2d2f36, inner: 0xd8d8d8, pants: 0x2d2f36, shoe: 0x120f0e, eye: 0x1a1a1a, brow: 0x1e1a18,
            style: 'bald', chain: 0xe0b23a, scale: 1.22 },
  batya: { skin: 0xd8a07c, hair: 0x2f2a26, top: 0xeeeae2, inner: 0xeeeae2, pants: 0x2d3340, shoe: 0x6a4a3a, eye: 0x2a2a2a, brow: 0x2a2420,
           style: 'dad', mustache: 0x3a2e26, glasses: 0x222222, tank: 1, scale: 1.04 },
  timur: { skin: 0xe9bf9c, hair: 0x5a3b26, top: 0x2b2f3d, inner: 0x2b2f3d, pants: 0x3a4660, shoe: 0xf0f0f0, eye: 0x2a2a2a, brow: 0x4a3020,
           style: 'teen', phones: 0x2f7fe0, hood: 1, scale: 0.96 },
  guest: { skin: 0xe2b08c, hair: 0x4a3524, top: 0x6d7f3a, inner: 0x6d7f3a, pants: 0x3b3a44, shoe: 0x2a2018, eye: 0x2a2a2a, brow: 0x3a2a1c,
           style: 'short', scale: 1.0 },
};
function makeChar(key, p) {
  const root = new THREE.Group(); root.scale.setScalar(p.scale);
  const hips = [-1, 1].map(s => { const g = new THREE.Group(); g.position.set(s * 2.5, 9, 0); g.add(voxMesh(4, 9, 4, -2, -9, -2, (x, y) => y <= 1 ? p.shoe : p.pants)); root.add(g); return g; });
  const torso = voxMesh(10, 11, 5, -5, 0, -2.5, (x, y, z) => {
    if (p.style === 'bald' && z === 4 && (x === 4 || x === 5) && y >= 6) return p.inner;              // рубашка в вырезе пиджака
    if (p.chain && z === 4 && y === 8 && x >= 3 && x <= 6) return p.chain;                             // цепь
    if (p.style === 'cap' && z === 4 && x >= 3 && x <= 6) return p.inner;                              // футболка под кожанкой
    if (p.style === 'bob' && z === 4 && y === 5 && (x === 3 || x === 6)) return 0xeeeeee;             // шнурки худи
    return p.top;
  });
  torso.position.y = 9; root.add(torso);
  const neck = new THREE.Group(); neck.position.y = 20; root.add(neck);
  const head = voxMesh(10, 10, 10, -5, 0, -5, (x, y, z) => {
    if (p.style === 'bald') return (y >= 9 && z <= 2) ? p.hair : p.skin;
    if (p.style === 'bob') { if (y >= 8 || z <= 2 || ((x <= 1 || x >= 8) && y >= 2)) return p.hair; if (y === 7 && z >= 8 && x >= 2 && x <= 7) return p.hair; return p.skin; }
    if (p.style === 'cap') { if (y >= 8) return p.cap; if (z <= 2 && y >= 4) return p.hair; if (p.stubble && y <= 2 && z >= 6 && (x <= 2 || x >= 7 || y <= 1)) return p.stubble; return p.skin; }
    if (p.style === 'dad') return (y >= 9 || (z <= 1 && y >= 4)) ? ((x + z) % 4 === 0 ? 0x8a847c : p.hair) : p.skin;
    if (p.style === 'teen') return (y >= 8 || z <= 2 || ((x === 0 || x === 9) && y >= 5) || (y === 7 && z >= 7 && (x % 3 !== 1))) ? p.hair : p.skin;
    return (y >= 8 || z <= 1) ? p.hair : p.skin;
  });
  neck.add(head);
  if (p.mustache) { const m = box(6, 1.2, 0.6, p.mustache); m.position.set(0, 3.4, 5.25); neck.add(m); }
  if (p.glasses) { for (const sx of [-2.4, 2.4]) { const fr = box(3.0, 0.4, 0.3, p.glasses); fr.position.set(sx, 6.6, 5.2); neck.add(fr);
      const fb = box(3.0, 0.4, 0.3, p.glasses); fb.position.set(sx, 3.9, 5.2); neck.add(fb); } const br = box(1.2, 0.4, 0.3, p.glasses); br.position.set(0, 5.6, 5.2); neck.add(br); }
  if (p.hood) { const hood = voxMesh(8, 3, 3, -4, 0, -1.5, () => p.top); hood.position.set(0, -2, -6); neck.add(hood); }
  if (p.style === 'cap') { const visor = box(10, 1, 4, p.cap); visor.position.set(0, 8.3, 6.5); neck.add(visor);
    const logo = box(3, 1.4, 0.2, 0xffd34d, { emissive: true }); logo.position.set(0, 9.2, 5.05); neck.add(logo); }
  if (p.phones) { const band = voxMesh(12, 2, 2, -6, 0, -1, (x, y) => (x <= 1 || x >= 10) ? p.phones : (y === 1 ? p.phones : 0)); band.position.set(0, -1.5, 1); neck.add(band); }
  const white = new THREE.MeshBasicMaterial({ color: 0xffffff }), pupilM = new THREE.MeshBasicMaterial({ color: p.eye });
  const browM = new THREE.MeshBasicMaterial({ color: p.brow }), mouthM = new THREE.MeshBasicMaterial({ color: 0x3a1010 });
  const eyes = [-1, 1].map(s => { const e = new THREE.Group(); e.position.set(s * 2.4, 5.2, 5.03);
    const w = new THREE.Mesh(new THREE.BoxGeometry(2.2, 2.2, 0.1), white); const pu = new THREE.Mesh(new THREE.BoxGeometry(1.1, 1.3, 0.12), pupilM);
    pu.position.z = 0.02; e.add(w, pu); e.userData.pupil = pu; neck.add(e); return e; });
  const brows = [-1, 1].map(s => { const b = new THREE.Mesh(new THREE.BoxGeometry(2.8, 0.7, 0.14), browM); b.position.set(s * 2.4, 7.0, 5.05); neck.add(b); return b; });
  const mouth = new THREE.Group(); mouth.position.set(0, 2.4, 5.04); neck.add(mouth);
  const lip = new THREE.Mesh(new THREE.BoxGeometry(3.6, 1, 0.1), mouthM); mouth.add(lip);
  const arm = (side) => { const g = new THREE.Group(); g.position.set(side * 6.5, 19, 0);
    g.add(voxMesh(3, 11, 3, -1.5, -11, -1.5, (x, y) => (p.tank || y < 3) ? p.skin : p.top)); root.add(g); return g; };
  const shoulders = [arm(-1), arm(1)];
  const tape = box(4, 6, 1.2, 0x15151a); tape.visible = false; tape.position.set(0, -11, 1.8); shoulders[1].add(tape);
  const label = box(3.2, 2.6, 0.1, 0xe94b6a, { emissive: true }); label.position.set(0, 0.6, 0.65); tape.add(label);
  root.visible = false; scene.add(root);
  return { key, root, torso, neck, eyes, brows, mouth, lip, shoulders, hips, tape };
}

// эмоция → лицо и голова
const EMO = {
  'спокойно': { by: 0, bt: 0, eye: 1, mo: 0, tilt: 0 }, 'уверенно': { by: 0.2, bt: -0.12, eye: 0.95, mo: 0, tilt: -0.06 },
  'ехидно': { by: 0.5, bt: 0.0, eye: 0.7, mo: 0.15, tilt: 0.08, smirk: 0.3, asym: 1 }, 'шок': { by: 1.3, bt: 0, eye: 1.45, mo: 0.6, tilt: -0.08, round: 1 },
  'злость': { by: -0.4, bt: 0.45, eye: 0.8, mo: 0, tilt: 0.1 }, 'паника': { by: 1.0, bt: -0.35, eye: 1.3, mo: 0.4, tilt: 0 },
  'шёпот': { by: 0.3, bt: -0.15, eye: 0.9, mo: 0, tilt: 0.15, lean: 0.18 }, 'восторг': { by: 1.0, bt: 0, eye: 1.25, mo: 0.5, tilt: -0.12 },
  'грусть': { by: 0.2, bt: -0.4, eye: 0.85, mo: 0, tilt: 0.22 }, 'невозмутимо': { by: -0.1, bt: 0, eye: 0.55, mo: 0, tilt: 0 },
};
// действие → углы рук/корпуса (x — вперёд, z — в стороны)
const ACT = {
  idle: { la: [0, -0.08], ra: [0, 0.08] }, talk: { la: [0, -0.08], ra: [-0.55, 0.1], gesture: 1 },
  point: { la: [0, -0.08], ra: [-1.45, 0.15] }, facepalm: { la: [0, -0.08], ra: [-2.4, -0.55], head: 0.25 },
  shrug: { la: [-0.35, -0.9], ra: [-0.35, 0.9], shrug: 1 }, arms_up: { la: [-2.7, -0.3], ra: [-2.7, 0.3], bounce: 1 },
  cross_arms: { la: [-1.25, 0.85], ra: [-1.25, -0.85] }, slam: { la: [0, -0.08], ra: [-1.0, 0.1], slam: 1 },
  hold_tape: { la: [0, -0.08], ra: [-1.1, 0.25], tape: 1 }, jump: { la: [-2.4, -0.5], ra: [-2.4, 0.5], jump: 1 },
  typing: { la: [-1.05, 0.25], ra: [-1.05, -0.25], typing: 1 }, walk_in: { walk: 1 },
};

// ---------- клуб «Кат-сцена» ----------
function neonTexture(text, color, w = 1024, h = 256, size = 150) {
  const c = document.createElement('canvas'); c.width = w; c.height = h; const g = c.getContext('2d');
  g.fillStyle = '#000'; g.fillRect(0, 0, w, h); g.font = `bold ${size}px "DejaVu Sans", sans-serif`; g.textAlign = 'center'; g.textBaseline = 'middle';
  for (const [blur, a] of [[40, 0.9], [18, 1], [0, 1]]) { g.shadowColor = color; g.shadowBlur = blur; g.fillStyle = a === 1 && blur === 0 ? '#fff' : color; g.fillText(text, w / 2, h / 2); }
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t;
}
const tvCanvas = document.createElement('canvas'); tvCanvas.width = 512; tvCanvas.height = 384;
const tvTex = new THREE.CanvasTexture(tvCanvas); tvTex.colorSpace = THREE.SRGBColorSpace;
function drawTV(text, t) {
  const g = tvCanvas.getContext('2d'); g.fillStyle = '#0d1a2a'; g.fillRect(0, 0, 512, 384);
  g.fillStyle = '#7fe3ff'; g.font = 'bold 44px "DejaVu Sans", sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle';
  const words = (text || 'КАТ-СЦЕНА').split(' '); const lines = []; let cur = '';
  for (const w of words) { if ((cur + ' ' + w).trim().length > 16) { lines.push(cur.trim()); cur = w; } else cur += ' ' + w; }
  lines.push(cur.trim());
  lines.slice(0, 5).forEach((ln, i, a) => g.fillText(ln, 256, 192 + (i - (a.length - 1) / 2) * 56));
  for (let y = 0; y < 384; y += 4) { g.fillStyle = `rgba(0,0,0,${0.18 + 0.05 * Math.sin(y * 0.3 + t * 8)})`; g.fillRect(0, y, 512, 2); }
  tvTex.needsUpdate = true;
}
function buildClub() {
  const R = mulberry32(11);
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(260, 200), new THREE.MeshToonMaterial({ color: 0x24202c, gradientMap: ramp }));
  floor.rotation.x = -Math.PI / 2; floor.receiveShadow = true; scene.add(floor);
  for (let i = -6; i <= 6; i++) for (let k = -4; k <= 4; k++) if ((i + k) % 2 === 0) { const tl = box(20, 0.2, 20, 0x2f2a3a); tl.castShadow = false; tl.position.set(i * 20, 0.1, k * 20); scene.add(tl); }
  const back = box(260, 110, 4, 0x2a2238); back.position.set(0, 55, -82); scene.add(back);
  const left = box(4, 110, 200, 0x261f33); left.position.set(-130, 55, 0); scene.add(left);
  const right = box(4, 110, 200, 0x261f33); right.position.set(130, 55, 0); scene.add(right);
  // полки с кассетами
  const spines = [];
  for (let s = 0; s < 4; s++) { const shelf = box(120, 2, 14, 0x4a3326); shelf.position.set(-40, 14 + s * 18, -73); scene.add(shelf);
    for (let n = 0; n < 26; n++) spines.push([-96 + n * 4.4, 15 + s * 18, -73, [0xd9483b, 0x2f6fd8, 0xf2c14e, 0x2fbf71, 0x9b59b6, 0xeeeeee, 0x111111][Math.floor(R() * 7)], 11 + R() * 4]); }
  const tapeGeo = new THREE.BoxGeometry(3.6, 1, 10); tapeGeo.translate(0, 0.5, 0);
  const tapes = new THREE.InstancedMesh(tapeGeo, new THREE.MeshToonMaterial({ gradientMap: ramp }), spines.length);
  const m4 = new THREE.Matrix4(), cc = new THREE.Color();
  spines.forEach(([x, y, z, c, h], i) => { m4.makeScale(1, h, 1).setPosition(x, y, z); tapes.setMatrixAt(i, m4); tapes.setColorAt(i, cc.setHex(c)); });
  tapes.castShadow = true; scene.add(tapes);
  // неоновая вывеска
  const sign = new THREE.Mesh(new THREE.PlaneGeometry(100, 25), new THREE.MeshBasicMaterial({ map: neonTexture('КАТ-СЦЕНА', '#ff3fa4'), transparent: true, blending: THREE.AdditiveBlending, fog: false }));
  sign.position.set(-40, 96, -79.5); scene.add(sign);
  const signL = new THREE.PointLight(0xff3fa4, 5200, 220, 2); signL.position.set(-40, 92, -55); scene.add(signL);
  // стойка с кассой
  const counter = box(60, 11, 14, 0x51372a); counter.position.set(-55, 5.5, -18); scene.add(counter);
  const top = box(62, 1.5, 16, 0x7a5a42); top.position.set(-55, 11.7, -18); scene.add(top);
  const till = box(10, 7, 8, 0x22252c); till.position.set(-72, 15.9, -20); scene.add(till);
  const tillScr = box(7, 3, 0.2, 0x5ef2a0, { emissive: true }); tillScr.position.set(-72, 17.4, -15.9); scene.add(tillScr);
  // ЭЛТ-телевизор и диван
  const tv = box(30, 24, 24, 0x2b2b30); tv.position.set(55, 30, -62); scene.add(tv);
  const stand = box(34, 18, 20, 0x3a2c22); stand.position.set(55, 9, -62); scene.add(stand);
  const screen = new THREE.Mesh(new THREE.PlaneGeometry(24, 18), new THREE.MeshBasicMaterial({ map: tvTex, fog: false })); screen.position.set(55, 31, -49.9); scene.add(screen);
  const tvL = new THREE.PointLight(0x7fe3ff, 1800, 120, 2); tvL.position.set(55, 30, -38); scene.add(tvL);
  const couch = box(46, 10, 16, 0x7a2f3a); couch.position.set(55, 5, 4); scene.add(couch);
  const couchBack = box(46, 14, 5, 0x6a2833); couchBack.position.set(55, 12, 12); scene.add(couchBack);
  // ряд компьютеров с подсветкой
  for (let n = 0; n < 3; n++) { const z = -50 + n * 30;
    const desk = box(18, 2, 24, 0x2c2c34); desk.position.set(112, 16, z); scene.add(desk);
    const mon = box(2, 12, 18, 0x15151a); mon.position.set(120, 25, z); scene.add(mon);
    const glow = box(0.2, 10, 16, [0x6a5cff, 0x2fe0c0, 0xff5c8a][n], { emissive: true }); glow.position.set(118.8, 25, z); scene.add(glow);
    const rgb = box(18, 0.6, 0.6, [0xff3fa4, 0x3fd2ff, 0x9dff3f][n], { emissive: true }); rgb.position.set(112, 15, z + 12); scene.add(rgb); }
  const pcL = new THREE.PointLight(0x6a5cff, 2400, 160, 2); pcL.position.set(100, 30, -20); scene.add(pcL);
  // вход
  const door = box(2, 44, 28, 0x1b1b22); door.position.set(-128, 22, 50); scene.add(door);
  const exit = new THREE.Mesh(new THREE.PlaneGeometry(26, 7), new THREE.MeshBasicMaterial({ map: neonTexture('ВХОД', '#5cff9d', 512, 128, 90), transparent: true, blending: THREE.AdditiveBlending, fog: false }));
  exit.rotation.y = Math.PI / 2; exit.position.set(-127, 50, 50); scene.add(exit);
  // свет: тёплый верхний с тенью + холодное заполнение
  scene.add(new THREE.HemisphereLight(0x6a5a9a, 0x140f18, 1.25));
  const key = new THREE.DirectionalLight(0xffd9b0, 2.1); key.position.set(-60, 160, 120); key.castShadow = true; key.shadow.mapSize.set(1024, 1024);
  Object.assign(key.shadow.camera, { left: -140, right: 140, top: 120, bottom: -40, near: 20, far: 420 }); key.shadow.bias = -0.0008; scene.add(key);
  const rim = new THREE.DirectionalLight(0x5cc8ff, 0.9); rim.position.set(120, 60, -160); scene.add(rim);
}

function patternTexture(draw, w = 256, h = 256) {
  const c = document.createElement('canvas'); c.width = w; c.height = h; const g = c.getContext('2d'); draw(g, w, h);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.magFilter = THREE.NearestFilter; return t;
}
function buildFlat() {
  scene.background = new THREE.Color(0x15121a); scene.fog = new THREE.Fog(0x15121a, 280, 560);
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(260, 200), new THREE.MeshToonMaterial({ color: 0x6b4a32, gradientMap: ramp }));
  floor.rotation.x = -Math.PI / 2; floor.receiveShadow = true; scene.add(floor);
  for (let i = -12; i <= 12; i++) { const plank = box(10, 0.2, 200, i % 2 ? 0x74513a : 0x684631); plank.castShadow = false; plank.position.set(i * 10.5, 0.1, 0); scene.add(plank); }
  const paper = patternTexture((g, w, h) => { g.fillStyle = '#c9b48a'; g.fillRect(0, 0, w, h); g.fillStyle = '#b39b70';
    for (let y = 0; y < h; y += 32) for (let x = (y / 32) % 2 * 16; x < w; x += 32) { g.beginPath(); g.arc(x + 8, y + 8, 5, 0, 7); g.fill(); } });
  paper.wrapS = paper.wrapT = THREE.RepeatWrapping; paper.repeat.set(6, 3);
  const wallMat = new THREE.MeshToonMaterial({ map: paper, gradientMap: ramp });
  const back = new THREE.Mesh(new THREE.BoxGeometry(260, 110, 4), wallMat); back.position.set(0, 55, -82); back.receiveShadow = true; scene.add(back);
  const left = new THREE.Mesh(new THREE.BoxGeometry(4, 110, 200), wallMat); left.position.set(-130, 55, 0); scene.add(left);
  // ковёр на стене
  const rug = patternTexture((g, w, h) => { g.fillStyle = '#8e1f24'; g.fillRect(0, 0, w, h); g.strokeStyle = '#e0b04a'; g.lineWidth = 6; g.strokeRect(10, 10, w - 20, h - 20);
    g.fillStyle = '#1f2a55'; g.fillRect(28, 28, w - 56, h - 56); g.fillStyle = '#e0b04a';
    for (let k = 0; k < 5; k++) { g.save(); g.translate(w / 2, h / 2); g.rotate(Math.PI / 4); const r = 90 - k * 16; g.strokeStyle = k % 2 ? '#e0b04a' : '#c7343a'; g.lineWidth = 7; g.strokeRect(-r / 2, -r / 2, r, r); g.restore(); }
    for (let x = 40; x < w - 40; x += 22) { g.fillRect(x, 34, 8, 8); g.fillRect(x, h - 42, 8, 8); } }, 256, 192);
  const carpet = new THREE.Mesh(new THREE.PlaneGeometry(110, 80), new THREE.MeshToonMaterial({ map: rug, gradientMap: ramp })); carpet.position.set(-20, 58, -79.7); scene.add(carpet);
  // диван, тумба, телевизор, приставка, коробка «Денди»
  const sofa = box(70, 12, 22, 0x2f6b4a); sofa.position.set(-20, 6, -40); scene.add(sofa);
  const sofaBack = box(70, 18, 6, 0x2a5e41); sofaBack.position.set(-20, 15, -52); scene.add(sofaBack);
  for (const sx of [-58, 18]) { const arm = box(6, 16, 22, 0x295a3e); arm.position.set(sx, 8, -40); scene.add(arm); }
  const stand = box(40, 14, 16, 0x5a3a26); stand.position.set(-20, 7, 45); scene.add(stand);
  const tv = box(34, 22, 6, 0x111114); tv.position.set(-20, 26, 45); scene.add(tv);
  const screen = new THREE.Mesh(new THREE.PlaneGeometry(30, 18), new THREE.MeshBasicMaterial({ map: tvTex, fog: false })); screen.rotation.y = Math.PI; screen.position.set(-20, 26, 41.9); scene.add(screen);
  const console_ = box(10, 3, 8, 0xf2f2f2); console_.position.set(-6, 15.5, 45); scene.add(console_);
  const dendy = box(12, 4, 9, 0x2a2a2e); dendy.position.set(-34, 15.9, 46); scene.add(dendy);
  const dendyLabel = box(6, 0.2, 4, 0xd9483b, { emissive: true }); dendyLabel.position.set(-34, 18, 46); scene.add(dendyLabel);
  const tvL = new THREE.PointLight(0x7fb8ff, 2600, 140, 2); tvL.position.set(-20, 26, 30); scene.add(tvL);
  // сервант с посудой
  const cab = box(40, 60, 16, 0x5a3a26); cab.position.set(75, 30, -72); scene.add(cab);
  const glass = box(34, 26, 0.4, 0x9fc4d8, { emissive: true }); glass.material.transparent = true; glass.material.opacity = 0.25; glass.position.set(75, 42, -63.6); scene.add(glass);
  for (let k = 0; k < 6; k++) { const cup = box(4, 5, 4, [0xf2f2f2, 0xe0b04a, 0xd9483b][k % 3]); cup.position.set(60 + k * 6, 33 + (k > 2 ? 12 : 0), -68); scene.add(cup); }
  // окно и торшер
  const win = box(36, 44, 1, 0x1a2a4a, { emissive: true }); win.position.set(-128, 60, -30); win.rotation.y = Math.PI / 2; scene.add(win);
  for (let k = 0; k < 14; k++) { const lit = box(2.5, 2.5, 0.4, 0xffd27a, { emissive: true }); lit.rotation.y = Math.PI / 2; lit.position.set(-127.4, 44 + (k * 7) % 30, -46 + (k * 13) % 32); scene.add(lit); }
  const lamp = box(3, 46, 3, 0x2a2a2a); lamp.position.set(30, 23, -60); scene.add(lamp);
  const shade = box(14, 10, 14, 0xf2d48a, { emissive: true }); shade.position.set(30, 50, -60); scene.add(shade);
  const lampL = new THREE.PointLight(0xffc27a, 5200, 200, 2); lampL.position.set(30, 46, -55); lampL.castShadow = false; scene.add(lampL);
  scene.add(new THREE.HemisphereLight(0x8a7aa0, 0x1a120e, 1.15));
  const key = new THREE.DirectionalLight(0xffe2c0, 1.7); key.position.set(80, 160, 120); key.castShadow = true; key.shadow.mapSize.set(1024, 1024);
  Object.assign(key.shadow.camera, { left: -140, right: 140, top: 120, bottom: -40, near: 20, far: 420 }); key.shadow.bias = -0.0008; scene.add(key);
}

const SITS = { 'диван у телевизора': true };   // здесь герои сидят
// где стоят герои в каждой локации: [x, z, поворот]
const SPOTS = {
  'стойка с кассой': { gosha: [-60, -30, 0.5], nika: [-22, -14, -0.95], palych: [-78, 4, 0.75], guest: [-40, 6, -0.25] },   // вполоборота к камере, как в ситкоме
  'ряд компьютеров': { gosha: [72, -10, 0.9], nika: [100, -24, 1.35], palych: [70, 18, 1.1], guest: [96, 10, 1.5] },
  'диван у ЭЛТ-телевизора': { gosha: [40, -20, -0.35], nika: [70, -22, 0.35], palych: [98, -6, -1.1], guest: [16, -6, 1.0] },
  'полки с кассетами': { gosha: [-60, -50, 0.35], nika: [-25, -48, -0.4], palych: [-42, -20, 3.0], guest: [-42, -24, 0] },
  'вход с неоновой вывеской': { gosha: [-80, 30, -1.1], nika: [-80, 58, -1.9], palych: [-110, 50, 1.55], guest: [-110, 46, 1.55] },
  'подсобка': { gosha: [-20, -40, 0.3], nika: [10, -40, -0.3], palych: [-5, -12, 3.1], guest: [-5, -14, 3.1] },
  'диван у телевизора': { batya: [-31, -36, 0.12], timur: [-9, -36, -0.12] },
  'кухня': { batya: [40, 0, -0.5], timur: [10, 20, 0.6] },
  'у серванта с ковром': { batya: [55, -50, 0.2], timur: [20, -40, 0.5] },
};

// ---------- монтажный план ----------
let TL = null, CAST = {};
const SH = new THREE.Vector3(), TG = new THREE.Vector3();
function segAt(t) { const s = TL.segments; for (let i = s.length - 1; i >= 0; i--) if (t >= s[i].t0) return [s[i], s[i - 1] || null, i]; return [s[0], null, 0]; }
function spot(who) { return (SPOTS[TL.set] || SPOTS['стойка с кассой'])[who] || [0, 0, 0]; }
function env(who, i) { const e = (TL.env || {})[who]; return e ? (e[Math.min(e.length - 1, i)] || 0) : 0; }

function pose(c, t, i, seg, prev) {
  const sp = spot(c.key); const active = (seg.cast || TL.cast).includes(c.key);
  c.root.visible = active || (TL.cast.includes(c.key) && !seg.cast);
  // герой в кадре только с первой своей реплики (Палыч «в худший момент»), главные двое — сразу
  const first = TL.segments.find(x => x.who === c.key);
  if (first && first.t0 > 0.5 && !['gosha', 'nika', 'batya', 'timur'].includes(c.key) && t < first.t0) c.root.visible = false;
  if (!c.root.visible) return;
  const mine = seg.who === c.key;
  const emo = EMO[(mine ? seg.emotion : seg.react?.[c.key]) || 'спокойно'] || EMO['спокойно'];
  const act = ACT[(mine ? seg.action : seg.react_action?.[c.key]) || (mine ? 'talk' : 'idle')] || ACT.idle;
  const k = ease(clamp((t - seg.t0) / 0.22, 0, 1));                 // мягкий переход позы на стыке реплик
  const pAct = prev && prev.who === c.key ? (ACT[prev.action] || ACT.idle) : ACT.idle;
  const talk = env(c.key, i);
  const b = Math.sin(t * 2.1 + c.key.length);
  let x = sp[0], z = sp[1], ry = sp[2], y = 0;
  if (act.walk && mine) { const w = clamp((t - seg.t0) / Math.max(0.6, seg.t1 - seg.t0), 0, 1); x = lerp(-110, sp[0], ease(w)); z = lerp(50, sp[1], ease(w));
    const s = Math.sin(t * 9) * 0.65 * (1 - w); c.hips[0].rotation.x = s; c.hips[1].rotation.x = -s; c.shoulders[0].rotation.x = -s; c.shoulders[1].rotation.x = s; y = Math.abs(Math.sin(t * 9)) * (1 - w); }
  else { c.hips[0].rotation.x = c.hips[1].rotation.x = 0; }
  if (act.jump && mine) y = Math.max(0, Math.sin(clamp((t - seg.t0) / 0.5, 0, 1) * Math.PI)) * 6;
  if (act.bounce) y = Math.abs(Math.sin(t * 6)) * 1.2;
  const sit = SITS[TL.set] && !(act.walk && mine) && !act.jump;
  if (sit) { y += 12.5 - 9 * (SPEC[c.key]?.scale || 1); c.hips[0].rotation.x = c.hips[1].rotation.x = -1.5; }
  c.root.position.set(x, y, z); c.root.rotation.y = ry;
  if (!act.walk || !mine) for (const [n, side] of [[0, 'la'], [1, 'ra']]) {
    const [ax, az] = act[side] || [0, 0], [bx, bz] = pAct[side] || [0, 0];
    let rx = lerp(bx, ax, k), rz = lerp(bz, az, k);
    if (act.gesture && n === 1) rx += -0.35 * talk + 0.12 * Math.sin(t * 5);
    if (act.slam && n === 1) rx += Math.max(0, Math.sin((t - seg.t0) * 7)) * -0.9;
    if (act.typing) rx += 0.08 * Math.sin(t * 22 + n * 2);
    c.shoulders[n].rotation.x = rx + 0.04 * b; c.shoulders[n].rotation.z = rz;
  }
  c.tape.visible = !!(act.tape && mine);
  c.torso.scale.y = 1 + 0.012 * b; c.torso.rotation.x = (emo.lean || 0) * (mine ? 1 : 0);
  // голова: к собеседнику, наклон эмоции, кивки в речи
  const other = mine ? (seg.to || TL.cast.find(w => w !== c.key)) : seg.who;
  let look = 0; if (other && CAST[other]) { const o = spot(other); look = clamp(Math.atan2(o[0] - x, o[1] - z) - ry, -0.7, 0.7); }
  c.neck.rotation.y = look * 0.8 + 0.06 * Math.sin(t * 0.8 + c.key.length);
  c.neck.rotation.x = emo.tilt + (act.head || 0) - 0.08 * talk + (act.shrug ? 0.05 : 0);
  c.neck.rotation.z = (emo.asym ? 0.08 : 0) + (act.shrug ? 0.12 : 0);
  // лицо
  const bt = (t + c.key.length * 0.7) % 3.9, blink = bt < 0.13 ? Math.sin(bt / 0.13 * Math.PI) : 0;
  c.eyes.forEach((e, n) => { const s = emo.eye * (1 - blink); e.scale.set(1, Math.max(0.08, s), 1); e.userData.pupil.position.x = clamp(look * 0.9, -0.5, 0.5); });
  c.brows.forEach((br, n) => { const side = n === 0 ? -1 : 1; br.position.y = 7.0 + emo.by * 0.9 + (emo.asym && n === 1 ? 0.8 : 0); br.rotation.z = side * emo.bt; });
  const open = clamp(emo.mo + talk * 1.6, 0, 1.6);
  if (emo.round) { c.lip.scale.set(0.55 + 0.2 * talk, 0.9 + open * 1.6, 1); } else { c.lip.scale.set(1 - 0.2 * open, 0.35 + open * 1.4, 1); }
  c.mouth.rotation.z = emo.smirk ? emo.smirk : 0;
}

function cameraFor(seg, t) {
  const lt = t - seg.t0, dur = Math.max(0.5, seg.t1 - seg.t0);
  const head = (who) => { const s = spot(who), sc = SPEC[who]?.scale || 1; return new THREE.Vector3(s[0], 25 * sc, s[1]); };
  const mid = (a, b) => a.clone().add(b).multiplyScalar(0.5);
  const cast = TL.cast.filter(w => CAST[w]);
  const center = cast.map(head).reduce((a, v) => a.add(v), new THREE.Vector3()).multiplyScalar(1 / Math.max(1, cast.length));
  const fwd = (p, ry, d) => new THREE.Vector3(p.x + Math.sin(ry) * d, p.y, p.z + Math.cos(ry) * d);
  const who = seg.focus || seg.who; let fov = 34;
  const shot = seg.shot || 'двое';
  if (shot.startsWith('крупно')) {
    const w = shot.includes('Гоша') ? 'gosha' : shot.includes('Ника') ? 'nika' : shot.includes('Палыч') ? 'palych' : shot.includes('гость') ? 'guest' : shot.includes('Батя') ? 'batya' : shot.includes('Тимур') ? 'timur' : who;
    const h = head(CAST[w] ? w : who), ry = spot(CAST[w] ? w : who)[2]; SH.copy(fwd(h, ry + 0.18, 58)); SH.y = h.y + 2; TG.set(h.x, h.y - 3.5, h.z); fov = 30;
  } else if (shot === 'общий') { SH.set(center.x + 10, 48, center.z + 150); TG.set(center.x, 30, center.z - 20); fov = 40; }
  else if (shot === 'через плечо') {   // 06.11: затылок соседа закрывал кадр — везде три четверти спереди на говорящего
    const other = seg.to || cast.find(w => w !== who) || who; const a = head(other), b = head(who);
    const side = Math.sign(b.x - a.x) || 1; SH.set(b.x + side * 20, b.y + 4, b.z + 46); TG.set(b.x - side * 4, b.y - 4, b.z); fov = 34;
  } else if (shot === 'через плечо') {
    const other = seg.to || cast.find(w => w !== who) || who; const a = head(other), b = head(who);
    const dir = b.clone().sub(a).setY(0).normalize(); SH.copy(a).addScaledVector(dir, -22); SH.x += 9; SH.y = a.y + 6; TG.copy(b); fov = 32;
  } else if (shot === 'экран ТВ') { if (TL.world === 'flat') { SH.set(-20, 27, 0); TG.set(-20, 26, 45); } else { SH.set(55, 31, -10); TG.set(55, 31, -50); } fov = 26; }
  else { // «двое» и «наезд»: оба героя реплики в кадре
    const other = seg.to || cast.find(w => w !== who) || who; const m = mid(head(who), head(other));
    const span = head(who).distanceTo(head(other)); SH.set(m.x + 8, m.y + 12, m.z + 52 + span * 1.35); TG.set(m.x, m.y - 4, m.z); fov = 34;
  }
  const move = seg.move || (shot === 'наезд' ? 'push' : 'drift');
  if (move === 'push') { const k = ease(clamp(lt / dur, 0, 1)); SH.lerp(TG, 0.32 * k); }
  else if (move === 'drift') { SH.x += lt * 1.4; }
  else if (move === 'shake') { SH.x += Math.sin(t * 60) * 0.6; SH.y += Math.cos(t * 53) * 0.6; }
  if (TL.world === 'flat' && SH.z > 36) { const k = (SH.z - TG.z) / Math.max(1, 36 - TG.z); SH.z = 36; fov = Math.min(64, fov * Math.min(1.9, k)); }  // не за телевизором
  camera.fov = fov; camera.position.copy(SH); camera.updateProjectionMatrix(); camera.lookAt(TG);
}

function renderFrame(i) {
  const t = i / FPS; const [seg, prev] = segAt(t);
  for (const c of Object.values(CAST)) pose(c, t, i, seg, prev);
  drawTV(seg.tv || TL.tv, t);
  cameraFor(seg, t);
  renderer.render(scene, camera);
}

// ---------- API для драйвера ----------
import { install } from '../capture.js';
install({
  canvas, renderFrame,
  load: (tl) => { TL = tl; FPS = tl.fps || 30; (tl.world === 'flat' ? buildFlat : buildClub)();
    for (const key of new Set([...tl.cast, ...tl.segments.flatMap(s => s.cast || [])])) if (SPEC[key]) CAST[key] = makeChar(key, { ...SPEC[key], ...(tl.looks || {})[key] });
    return { tris: renderer.info.render.triangles }; },
  info: () => { const ext = gl.getExtension('WEBGL_debug_renderer_info'); return ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER); },
});
