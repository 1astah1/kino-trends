// «Что если»: светлая студия, рассказчик-кубик с указкой и предметы из простых форм, которые выскакивают по сценарию.
import * as THREE from 'three';
import { install } from '../capture.js';

const qs = new URLSearchParams(location.search);
const W = +qs.get('w') || 720, H = +qs.get('h') || 1280;
let TL = null, FPS = 30;
const clamp = (v, a, b) => Math.max(a, Math.min(b, v)), ease = k => k * k * (3 - 2 * k), lerp = (a, b, k) => a + (b - a) * k;
const back = (k) => { const c = 1.70158; return 1 + (c + 1) * Math.pow(k - 1, 3) + c * Math.pow(k - 1, 2); };

const renderer = new THREE.WebGLRenderer({ antialias: true }); renderer.setPixelRatio(1); renderer.setSize(W, H);
renderer.outputColorSpace = THREE.SRGBColorSpace; renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.05;
renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFSoftShadowMap; document.body.appendChild(renderer.domElement);
const canvas = renderer.domElement, gl = renderer.getContext();
const scene = new THREE.Scene();
const bgC = document.createElement('canvas'); bgC.width = 4; bgC.height = 256; const bg = bgC.getContext('2d');
const gr = bg.createLinearGradient(0, 0, 0, 256); gr.addColorStop(0, '#cfe8f7'); gr.addColorStop(0.55, '#f6e3ee'); gr.addColorStop(1, '#fbeadf'); bg.fillStyle = gr; bg.fillRect(0, 0, 4, 256);
scene.background = new THREE.CanvasTexture(bgC); scene.background.colorSpace = THREE.SRGBColorSpace;
const camera = new THREE.PerspectiveCamera(36, W / H, 1, 2000);
const mat = (c, o = {}) => new THREE.MeshStandardMaterial({ color: c, roughness: 0.55, metalness: 0.0, ...o });
const mesh = (geo, c, o) => { const m = new THREE.Mesh(geo, mat(c, o)); m.castShadow = m.receiveShadow = true; return m; };
const B = (w, h, d, c, o) => mesh(new THREE.BoxGeometry(w, h, d), c, o), S = (r, c, o) => mesh(new THREE.SphereGeometry(r, 24, 16), c, o);
const C = (r1, r2, h, c, seg = 24, o) => mesh(new THREE.CylinderGeometry(r1, r2, h, seg), c, o);

const floor = new THREE.Mesh(new THREE.CircleGeometry(400, 64), new THREE.MeshStandardMaterial({ color: 0xfaf3ee, roughness: 1 })); floor.rotation.x = -Math.PI / 2; floor.receiveShadow = true; scene.add(floor);
scene.add(new THREE.HemisphereLight(0xffffff, 0xd8c8d8, 1.6));
const sun = new THREE.DirectionalLight(0xffffff, 2.2); sun.position.set(60, 160, 120); sun.castShadow = true; sun.shadow.mapSize.set(1024, 1024);
Object.assign(sun.shadow.camera, { left: -120, right: 120, top: 120, bottom: -60, near: 20, far: 500 }); sun.shadow.radius = 6; scene.add(sun);

// рассказчик
const cube = new THREE.Group(); scene.add(cube);
const bodyM = B(30, 30, 30, 0xf7f1e8); bodyM.position.y = 27; cube.add(bodyM);
const dark = new THREE.MeshBasicMaterial({ color: 0x2b2333 });
const eyes = [-1, 1].map(s => { const e = new THREE.Mesh(new THREE.SphereGeometry(2.6, 16, 12), dark); e.scale.set(1, 1.3, 0.4); e.position.set(s * 6, 31, 15.2); cube.add(e); return e; });
const brows = [-1, 1].map(s => { const b = new THREE.Mesh(new THREE.BoxGeometry(5.5, 1.1, 0.6), dark); b.position.set(s * 6, 36.5, 15.3); cube.add(b); return b; });
const mouth = new THREE.Mesh(new THREE.BoxGeometry(6, 1.2, 0.6), new THREE.MeshBasicMaterial({ color: 0x5a1f2c })); mouth.position.set(0, 23, 15.3); cube.add(mouth);
const legs = [-1, 1].map(s => { const l = C(1.6, 1.6, 12, 0x2b2333); l.position.set(s * 7, 6, 0); cube.add(l); return l; });
const armR = new THREE.Group(); armR.position.set(15, 26, 0); cube.add(armR);
const armRm = C(1.3, 1.3, 14, 0x2b2333); armRm.position.y = -7; armR.add(armRm);
const stick = C(0.6, 0.6, 30, 0x8a5a3a); stick.position.set(0, -14, 12); stick.rotation.x = Math.PI / 2; armR.add(stick);
const armL = new THREE.Group(); armL.position.set(-15, 26, 0); cube.add(armL); const armLm = C(1.3, 1.3, 14, 0x2b2333); armLm.position.y = -7; armL.add(armLm);
cube.position.set(-17, 0, 12); cube.rotation.y = 0.3;

// предметы из простых форм
function person(c) { const g = new THREE.Group(); const b = C(3, 4, 12, c); b.position.y = 10; const h = S(3.6, 0xf2c9a8); h.position.y = 19; const l1 = C(1.2, 1.2, 6, 0x2b2333), l2 = l1.clone(); l1.position.set(-1.6, 3, 0); l2.position.set(1.6, 3, 0); g.add(b, h, l1, l2); return g; }
const PROPS = {
  'машина': () => { const g = new THREE.Group(); const b = B(36, 9, 16, 0x6fa8dc); b.position.y = 9; const cab = B(18, 8, 14, 0xa9d1f0); cab.position.set(-2, 17, 0);
    g.add(b, cab); for (const [x, z] of [[-11, 8], [11, 8], [-11, -8], [11, -8]]) { const w = C(4, 4, 3, 0x2b2333); w.rotation.x = Math.PI / 2; w.position.set(x, 4, z); g.add(w); } return g; },
  'взрыв': () => { const g = new THREE.Group(); for (let k = 0; k < 9; k++) { const s = S(5 + (k % 3) * 3, [0xffb347, 0xff6f3c, 0xffe08a][k % 3], { emissive: [0xff8a00, 0xff3c00, 0xffc000][k % 3], emissiveIntensity: 0.6 });
    s.position.set(Math.cos(k * 1.3) * 10, 14 + Math.sin(k * 2.1) * 8, Math.sin(k * 1.7) * 6); g.add(s); } return g; },
  'человечек': () => person(0xe46a6a),
  'толпа': () => { const g = new THREE.Group(); for (let k = 0; k < 7; k++) { const p = person([0xe46a6a, 0x6fa8dc, 0x8fd58a, 0xf2c14e][k % 4]); p.position.set((k % 4) * 9 - 13, 0, Math.floor(k / 4) * -9); g.add(p); } return g; },
  'деньги': () => { const g = new THREE.Group(); for (let k = 0; k < 5; k++) { const b = B(20, 2.2, 10, 0x7cc47a); b.position.set(0, 1.1 + k * 2.4, 0); b.rotation.y = k * 0.12; g.add(b); }
    const coin = C(6, 6, 1.5, 0xf2c14e, 32, { metalness: 0.4 }); coin.rotation.x = Math.PI / 2; coin.position.set(12, 8, 6); g.add(coin); return g; },
  'дом': () => { const g = new THREE.Group(); const b = B(24, 18, 20, 0xf3d9b1); b.position.y = 9; const roof = mesh(new THREE.ConeGeometry(19, 12, 4), 0xd9604a); roof.rotation.y = Math.PI / 4; roof.position.y = 24;
    const door = B(5, 9, 0.5, 0x8a5a3a); door.position.set(0, 4.5, 10.2); g.add(b, roof, door); return g; },
  'телефон': () => { const g = new THREE.Group(); const b = B(12, 22, 2, 0x2b2333); b.position.y = 14; const scr = B(10, 18, 0.3, 0x7fe3ff, { emissive: 0x2a8aff, emissiveIntensity: 0.5 }); scr.position.set(0, 14, 1.1); g.add(b, scr); return g; },
  'часы': () => { const g = new THREE.Group(); const f = C(11, 11, 3, 0xffffff, 32); f.rotation.x = Math.PI / 2; f.position.y = 15; const r = C(12, 12, 2, 0xe46a6a, 32); r.rotation.x = Math.PI / 2; r.position.set(0, 15, -1);
    const h1 = B(1.2, 7, 0.5, 0x2b2333); h1.position.set(0, 18, 1.8); const h2 = B(1, 9, 0.5, 0x2b2333); h2.position.set(3, 15, 1.9); h2.rotation.z = -1.2; g.add(r, f, h1, h2); return g; },
  'сердце': () => { const g = new THREE.Group(); const a = S(6, 0xff4f7b), b = S(6, 0xff4f7b); a.position.set(-4, 18, 0); b.position.set(4, 18, 0); const c = mesh(new THREE.ConeGeometry(8.5, 12, 24), 0xff4f7b); c.rotation.z = Math.PI; c.position.y = 10; g.add(a, b, c); return g; },
  'молния': () => { const sh = new THREE.Shape(); sh.moveTo(2, 12); sh.lineTo(-5, 0); sh.lineTo(0, 0); sh.lineTo(-3, -12); sh.lineTo(6, 3); sh.lineTo(1, 3); sh.lineTo(5, 12);
    const m = mesh(new THREE.ExtrudeGeometry(sh, { depth: 3, bevelEnabled: false }), 0xffd23f, { emissive: 0xffa000, emissiveIntensity: 0.4 }); m.position.y = 16; return m; },
  'пицца': () => { const g = new THREE.Group(); const p = C(14, 14, 2, 0xf5c16c, 32); p.position.y = 3; g.add(p); for (let k = 0; k < 6; k++) { const s = C(2, 2, 2.2, 0xd9483b, 16); s.position.set(Math.cos(k) * 8, 3.5, Math.sin(k) * 8); g.add(s); } g.rotation.x = 0.6; g.position.y = 6; return g; },
  'геймпад': () => { const g = new THREE.Group(); const b = B(24, 7, 12, 0x3a3a46); b.position.y = 14; g.add(b); const d1 = B(5, 1, 1.5, 0x7fe3ff), d2 = B(1.5, 1, 5, 0x7fe3ff); d1.position.set(-6, 17.6, 0); d2.position.set(-6, 17.6, 0);
    const bt = S(1.6, 0xff5c8a); bt.position.set(6, 18, 0); g.add(d1, d2, bt); return g; },
  'камера': () => { const g = new THREE.Group(); const b = B(18, 12, 10, 0x2b2333); b.position.y = 12; const l = C(4, 5, 8, 0x55556a); l.rotation.x = Math.PI / 2; l.position.set(0, 12, 8); g.add(b, l); return g; },
  'поезд': () => { const g = new THREE.Group(); for (let k = 0; k < 3; k++) { const c = B(14, 10, 10, [0xd9483b, 0x6fa8dc, 0xf2c14e][k]); c.position.set(k * 16 - 16, 8, 0); g.add(c); } const ch = C(2, 2, 6, 0x2b2333); ch.position.set(-20, 16, 0); g.add(ch); return g; },
  'ракета': () => { const g = new THREE.Group(); const b = C(5, 5, 24, 0xf7f1e8); b.position.y = 16; const n = mesh(new THREE.ConeGeometry(5, 9, 24), 0xd9483b); n.position.y = 32.5;
    for (let k = 0; k < 3; k++) { const f = B(1, 8, 6, 0xd9483b); f.position.set(Math.cos(k * 2.1) * 5, 6, Math.sin(k * 2.1) * 5); f.rotation.y = -k * 2.1; g.add(f); } const fl = S(3.5, 0xffb347, { emissive: 0xff7a00, emissiveIntensity: 0.9 }); fl.position.y = 2; g.add(b, n, fl); return g; },
  'череп': () => { const g = new THREE.Group(); const s = S(10, 0xf3eee4); s.position.y = 14; const j = B(10, 5, 8, 0xf3eee4); j.position.set(0, 6, 2); g.add(s, j);
    for (const x of [-4, 4]) { const e = S(2.6, 0x2b2333); e.position.set(x, 15, 8.4); g.add(e); } return g; },
  'чек': () => { const c = document.createElement('canvas'); c.width = 128; c.height = 256; const x = c.getContext('2d'); x.fillStyle = '#fff'; x.fillRect(0, 0, 128, 256); x.fillStyle = '#888';
    for (let y = 30; y < 230; y += 22) x.fillRect(14, y, 60 + (y * 7) % 40, 6); x.fillStyle = '#2b2333'; x.font = 'bold 26px "DejaVu Sans"'; x.fillText('ИТОГО', 14, 245);
    const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; const m = new THREE.Mesh(new THREE.PlaneGeometry(14, 28), new THREE.MeshStandardMaterial({ map: t, side: THREE.DoubleSide }));
    m.position.y = 16; m.castShadow = true; return m; },
  'кровать': () => { const g = new THREE.Group(); const b = B(30, 6, 18, 0x8a5a3a); b.position.y = 5; const m = B(28, 4, 16, 0xffffff); m.position.y = 10; const p = B(8, 3, 12, 0xcfe8f7); p.position.set(-10, 13, 0); g.add(b, m, p); return g; },
  'солнце': () => { const g = new THREE.Group(); const s = S(9, 0xffd23f, { emissive: 0xffb000, emissiveIntensity: 0.7 }); s.position.y = 20; g.add(s);
    for (let k = 0; k < 8; k++) { const r = B(1.5, 6, 1.5, 0xffd23f); const a = k * Math.PI / 4; r.position.set(Math.cos(a) * 14, 20 + Math.sin(a) * 14, 0); r.rotation.z = a - Math.PI / 2; g.add(r); } return g; },
  'луна': () => { const s = S(9, 0xdfe3ea, { emissive: 0x99a4c0, emissiveIntensity: 0.3 }); s.position.y = 20; return s; },
};
const built = {};
function propObj(name) { if (!PROPS[name]) return null; if (!built[name]) { built[name] = PROPS[name](); built[name].visible = false; scene.add(built[name]); } return built[name]; }

const EMO = { 'шок': [1.3, 1.6, 0.6], 'восторг': [1.2, 1.2, 0.5], 'злость': [0.8, -0.8, 0], 'грусть': [0.9, -0.3, 0], 'ехидно': [0.7, 0.6, 0.1], 'невозмутимо': [0.6, 0, 0], 'паника': [1.3, 1.2, 0.4] };
function segAt(t) { const s = TL.segments; for (let i = s.length - 1; i >= 0; i--) if (t >= s[i].t0) return [s[i], i]; return [s[0], 0]; }
function renderFrame(i) {
  const t = i / FPS, [seg, idx] = segAt(t), lt = t - seg.t0, dur = Math.max(0.5, seg.t1 - seg.t0);
  let name = null; for (let k = idx; k >= 0 && !name; k--) name = TL.segments[k].prop;   // предмет держится до следующего
  const startIdx = TL.segments.findIndex(s => s.prop === name); const since = name ? t - TL.segments[startIdx].t0 : 0;
  for (const [n, o] of Object.entries(built)) o.visible = false;
  const o = name ? propObj(name) : null;
  if (o) { o.visible = true; const k = clamp(since / 0.45, 0, 1); o.scale.setScalar(Math.max(0.001, back(k)) * 1.25); o.position.set(17, 0, -22); o.rotation.y = -0.5 + Math.sin(t * 0.8) * 0.15; }
  // рассказчик: голос, эмоция, жест указкой
  const talk = (TL.env?.cube || [])[i] || 0, e = EMO[seg.emotion] || [1, 0, 0];
  eyes.forEach((ey, n) => { const bt = (t + 0.3) % 3.7 < 0.12 ? 0.15 : 1; ey.scale.set(e[0], 1.3 * e[0] * bt, 0.4); });
  brows.forEach((b, n) => { b.position.y = 36.5 + e[1]; b.rotation.z = (n ? -1 : 1) * (seg.emotion === 'злость' ? 0.35 : seg.emotion === 'грусть' ? -0.3 : 0); });
  mouth.scale.set(1 - 0.3 * talk, 1 + (e[2] + talk) * 4, 1);
  const point = seg.action === 'point' || (o && since < 1.2);
  armR.rotation.z = point ? lerp(0.2, 1.25, ease(clamp(lt / 0.3, 0, 1))) : 0.25 + 0.15 * Math.sin(t * 3) * talk;
  armL.rotation.z = seg.action === 'shrug' ? -1.0 : -0.2;
  cube.position.y = (seg.action === 'jump' ? Math.max(0, Math.sin(clamp(lt / 0.5, 0, 1) * Math.PI)) * 12 : 0) + Math.abs(Math.sin(t * 2.4)) * talk * 1.5;
  cube.rotation.z = seg.action === 'shrug' ? Math.sin(t * 4) * 0.04 : 0;
  // камера
  const shot = seg.shot || 'общий'; let p, tg, fov = 34;
  if (shot === 'крупно Кубик') { p = [-6, 38, 130]; tg = [-17, 29, 12]; fov = 30; }
  else if (shot === 'предмет' && o) { p = [30, 36, 125]; tg = [17, 16, -22]; fov = 32; }
  else { p = [0, 62, 235]; tg = [0, 22, -6]; fov = 36; }
  const push = (seg.move === 'push' || shot === 'наезд') ? 0.3 * ease(clamp(lt / dur, 0, 1)) : 0.05 * ease(clamp(lt / dur, 0, 1));
  camera.position.set(lerp(p[0], tg[0], push), lerp(p[1], tg[1], push), lerp(p[2], tg[2], push));
  if (seg.move === 'shake') { camera.position.x += Math.sin(t * 50) * 0.7; camera.position.y += Math.cos(t * 43) * 0.7; }
  camera.fov = fov; camera.updateProjectionMatrix(); camera.lookAt(tg[0], tg[1], tg[2]);
  renderer.render(scene, camera);
}
install({ canvas, renderFrame, load: (tl) => { TL = tl; FPS = tl.fps || 30; return {}; },
  info: () => { const ext = gl.getExtension('WEBGL_debug_renderer_info'); return ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : ''; } });
