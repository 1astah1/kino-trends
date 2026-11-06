// «VHS-ужасы»: тёмная комната 90-х, ЭЛТ-телевизор с жуткими картинками, приставка; поверх — эффект найденной кассеты.
import * as THREE from 'three';
import { install } from '../capture.js';

const qs = new URLSearchParams(location.search);
const W = +qs.get('w') || 720, H = +qs.get('h') || 1280;
let TL = null, FPS = 30;
const clamp = (v, a, b) => Math.max(a, Math.min(b, v)), ease = k => k * k * (3 - 2 * k), lerp = (a, b, k) => a + (b - a) * k;
const hash = (n) => { const x = Math.sin(n * 127.1) * 43758.5453; return x - Math.floor(x); };

const renderer = new THREE.WebGLRenderer({ antialias: false, preserveDrawingBuffer: false });
renderer.setPixelRatio(1); renderer.setSize(W, H); renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.1; renderer.shadowMap.enabled = true;
document.body.appendChild(renderer.domElement);
const canvas = renderer.domElement, gl = renderer.getContext();
const scene = new THREE.Scene(); scene.background = new THREE.Color(0x07060a); scene.fog = new THREE.Fog(0x07060a, 120, 380);
const camera = new THREE.PerspectiveCamera(40, W / H, 1, 1000);
const ramp = new THREE.DataTexture(new Uint8Array([60, 140, 255]), 3, 1, THREE.RedFormat); ramp.minFilter = ramp.magFilter = THREE.NearestFilter; ramp.needsUpdate = true;
const box = (w, h, d, color, basic) => { const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), basic ? new THREE.MeshBasicMaterial({ color, fog: false }) : new THREE.MeshToonMaterial({ color, gradientMap: ramp }));
  m.castShadow = !basic; m.receiveShadow = true; scene.add(m); return m; };

// экран телевизора: картинки рисуются кодом
const tvC = document.createElement('canvas'); tvC.width = 512; tvC.height = 384; const tv = tvC.getContext('2d');
const tvTex = new THREE.CanvasTexture(tvC); tvTex.colorSpace = THREE.SRGBColorSpace;
function drawTV(what, t, lt) {
  tv.fillStyle = '#05040a'; tv.fillRect(0, 0, 512, 384);
  const glow = (c) => { tv.shadowColor = c; tv.shadowBlur = 30; };
  tv.save(); tv.translate(256, 200);
  const k = ease(clamp(lt / 0.6, 0, 1));
  if (what === 'силуэт' || what === 'рука') { glow('#ff3fd0'); tv.fillStyle = '#c21fa0'; tv.fillRect(-256, -200, 512, 384); tv.fillStyle = '#0a0310';
    if (what === 'силуэт') { tv.beginPath(); tv.arc(0, -70 + (1 - k) * 40, 46, 0, 7); tv.fill(); tv.fillRect(-60, -30 + (1 - k) * 40, 120, 220); tv.fillRect(-100, -20 + (1 - k) * 40, 40, 150); tv.fillRect(60, -20 + (1 - k) * 40, 40, 150);
      tv.fillStyle = '#fff'; tv.beginPath(); tv.arc(-16, -76, 6, 0, 7); tv.arc(16, -76, 6, 0, 7); tv.fill(); }
    else { for (let f = 0; f < 4; f++) tv.fillRect(-90 + f * 45, -150 + (1 - k) * 120, 26, 170); tv.fillRect(-100, 0 + (1 - k) * 120, 190, 180); } }
  else if (what === 'глаза' || what === 'лицо') { tv.fillStyle = '#000'; tv.fillRect(-256, -200, 512, 384); glow('#ff2a2a'); tv.fillStyle = '#ff3030';
    const blink = (Math.sin(t * 1.3) > 0.96) ? 0.1 : 1;
    for (const sx of [-60, 60]) { tv.beginPath(); tv.ellipse(sx, -10, 26, 12 * blink * k, 0, 0, 7); tv.fill(); }
    if (what === 'лицо') { tv.strokeStyle = '#ff3030'; tv.lineWidth = 6; tv.beginPath(); tv.arc(0, 70, 70, 0.15 * Math.PI, 0.85 * Math.PI); tv.stroke(); } }
  else if (what === 'картридж') { glow('#5fd4ff'); tv.fillStyle = '#1a1a22'; tv.fillRect(-256, -200, 512, 384); tv.fillStyle = '#3a3a46'; tv.fillRect(-110, -130, 220, 240);
    tv.fillStyle = '#d9483b'; tv.fillRect(-90, -110, 180, 120); tv.fillStyle = '#05040a'; tv.font = 'bold 40px "DejaVu Sans Mono", monospace'; tv.textAlign = 'center'; tv.fillText('9999 в 1', 0, -40); }
  else if (what === 'кассета') { tv.fillStyle = '#111'; tv.fillRect(-256, -200, 512, 384); tv.fillStyle = '#222'; tv.fillRect(-150, -90, 300, 180); tv.fillStyle = '#ddd'; tv.fillRect(-120, -70, 240, 60);
    tv.fillStyle = '#000'; for (const sx of [-55, 55]) { tv.beginPath(); tv.arc(sx, 40, 26 + Math.sin(t * 3) * 2, 0, 7); tv.fill(); } }
  else if (what === 'лес' || what === 'коридор') { const g = tv.createLinearGradient(0, -200, 0, 184); g.addColorStop(0, '#0b1a12'); g.addColorStop(1, '#020604'); tv.fillStyle = g; tv.fillRect(-256, -200, 512, 384);
    tv.fillStyle = '#000'; if (what === 'лес') for (let k2 = 0; k2 < 9; k2++) tv.fillRect(-250 + k2 * 60 + Math.sin(k2) * 20, -200, 18 + (k2 % 3) * 8, 400);
    else { tv.strokeStyle = '#3a4a3a'; tv.lineWidth = 3; for (let d = 1; d < 7; d++) { const s = 256 / d; tv.strokeRect(-s, -s * 0.75 + 20, s * 2, s * 1.5); } }
    tv.fillStyle = '#fff'; tv.beginPath(); tv.arc(0, 20, 3, 0, 7); tv.fill(); }
  else if (what === 'надпись') { tv.fillStyle = '#000'; tv.fillRect(-256, -200, 512, 384); glow('#ff3030'); tv.fillStyle = '#ff3030'; tv.font = 'bold 54px "DejaVu Sans", sans-serif';
    tv.textAlign = 'center'; tv.fillText((TL.tv_text || 'НЕ СМОТРИ').slice(0, 14), 0, 10); }
  else { for (let y = -200; y < 184; y += 2) { tv.fillStyle = `rgba(255,255,255,${hash(y + Math.floor(t * 30)) * 0.5})`; tv.fillRect(-256, y, 512, 2); } } // помехи / по умолчанию
  tv.restore(); tv.shadowBlur = 0;
  for (let y = 0; y < 384; y += 3) { tv.fillStyle = 'rgba(0,0,0,0.25)'; tv.fillRect(0, y, 512, 1); }
  tvTex.needsUpdate = true;
}

function buildRoom() {
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(300, 240), new THREE.MeshToonMaterial({ color: 0x3a2228, gradientMap: ramp })); floor.rotation.x = -Math.PI / 2; floor.receiveShadow = true; scene.add(floor);
  box(300, 120, 4, 0x1c1620).position.set(0, 60, -80); box(4, 120, 240, 0x1a141e).position.set(-120, 60, 0);
  box(60, 14, 30, 0x3a2a20).position.set(0, 7, -45);                                  // тумба
  const tvBody = box(46, 36, 34, 0x2a2a30); tvBody.position.set(0, 32, -48);
  const scr = new THREE.Mesh(new THREE.PlaneGeometry(36, 27), new THREE.MeshBasicMaterial({ map: tvTex, fog: false })); scr.position.set(0, 33, -30.9); scene.add(scr);
  box(3, 14, 3, 0x111111).position.set(-12, 56, -50); box(3, 14, 3, 0x111111).position.set(12, 56, -50);   // антенна
  box(26, 5, 18, 0x2b2b30).position.set(-30, 2.5, -10);                               // приставка на полу
  const cart = box(14, 3, 9, 0x2b2b33); cart.position.set(-30, 7.5, -12);                       // картридж с наклейкой
  const lab = document.createElement('canvas'); lab.width = 256; lab.height = 128; const lg = lab.getContext('2d');
  lg.fillStyle = '#d9483b'; lg.fillRect(0, 0, 256, 128); lg.fillStyle = '#ffd23f'; lg.font = 'bold 54px "DejaVu Sans", sans-serif'; lg.textAlign = 'center'; lg.fillText('9999 в 1', 128, 82);
  const labT = new THREE.CanvasTexture(lab); labT.colorSpace = THREE.SRGBColorSpace;
  const label = new THREE.Mesh(new THREE.PlaneGeometry(12, 6), new THREE.MeshBasicMaterial({ map: labT })); label.rotation.x = -Math.PI / 2; label.position.set(-30, 9.1, -12); scene.add(label);
  const led = box(1.2, 1.2, 0.4, 0xff2020, true); led.position.set(-20, 3, -0.8);
  for (const [x, z, r] of [[-8, 8, 0.4], [18, 14, -0.3]]) { const pad = box(12, 2.5, 7, 0x3a3a40); pad.position.set(x, 1.3, z); pad.rotation.y = r; }
  const win = box(40, 50, 1, 0x1a2a50, true); win.position.set(-118, 62, -10); win.rotation.y = Math.PI / 2;
  const door = box(2, 70, 30, 0x221a14); door.position.set(80, 35, -40); door.rotation.y = -0.6;
  const gap = box(1, 70, 3, 0xffd9a0, true); gap.position.set(93, 35, -53);          // свет из щели
  const gapL = new THREE.PointLight(0xffc27a, 2400, 90, 2); gapL.position.set(86, 40, -46); scene.add(gapL);
  const eyes = new THREE.Group(); for (const dy of [-2.5, 2.5]) { const e = box(1.4, 1.4, 0.6, 0xff2020, true); e.position.set(0, 50, dy); eyes.add(e); }
  eyes.position.set(92.2, 0, -53); eyes.visible = false; scene.add(eyes); scene.userData.eyes = eyes;
  // обстановка 90-х: кровать, полка с кассетами, плакаты
  box(70, 12, 40, 0x2a3a5a).position.set(-70, 6, 40); box(70, 4, 40, 0xd8d0c0).position.set(-70, 14, 40); box(16, 6, 30, 0xeeeeee).position.set(-98, 18, 40);
  box(40, 3, 12, 0x4a3222).position.set(-60, 60, -74);
  for (let k = 0; k < 8; k++) box(3.6, 11, 9, [0x222222, 0xd9483b, 0x2f6fd8, 0xf2c14e][k % 4]).position.set(-76 + k * 4.4, 67, -73);
  for (const [x, c] of [[45, 0xb02a6a], [70, 0x2a7ab0]]) { const pst = box(20, 28, 0.5, c); pst.position.set(x, 70, -77.5); }
  scene.add(new THREE.HemisphereLight(0x4a4a8a, 0x100810, 1.15));
  const moon = new THREE.DirectionalLight(0x8aa4ff, 1.6); moon.position.set(-200, 120, 40); moon.castShadow = true; scene.add(moon);
  const tvL = new THREE.PointLight(0xb048ff, 6000, 160, 2); tvL.position.set(0, 33, -15); scene.add(tvL); scene.userData.tvL = tvL;
}

function segAt(t) { const s = TL.segments; for (let i = s.length - 1; i >= 0; i--) if (t >= s[i].t0) return s[i]; return s[0]; }
const SHOTS = {
  'общий': [[40, 70, 135], [-15, 25, -25]], 'телевизор': [[0, 34, 10], [0, 33, -31]], 'картридж': [[-24, 30, 22], [-30, 7, -12]],
  'окно': [[-60, 60, 40], [-118, 60, -10]], 'дверь': [[50, 48, 10], [92, 42, -52]], 'наезд': [[0, 36, 60], [0, 33, -31]],
};
function renderFrame(i) {
  const t = i / FPS, seg = segAt(t), lt = t - seg.t0, dur = Math.max(0.5, seg.t1 - seg.t0);
  drawTV(seg.prop || seg.tv || 'помехи', t, lt);
  const [p, tg] = SHOTS[seg.shot] || SHOTS['общий'];
  const k = ease(clamp(lt / dur, 0, 1)), push = seg.move === 'push' || seg.shot === 'наезд' ? 0.35 * k : 0.08 * k;
  camera.position.set(lerp(p[0], tg[0], push) + Math.sin(t * 0.7) * 1.2, lerp(p[1], tg[1], push) + Math.sin(t * 1.1) * 0.8, lerp(p[2], tg[2], push));
  if (seg.move === 'shake') { camera.position.x += Math.sin(t * 47) * 0.8; camera.position.y += Math.cos(t * 39) * 0.8; }
  camera.lookAt(tg[0], tg[1], tg[2]);
  scene.userData.tvL.intensity = 5200 + 1600 * Math.sin(t * 13) * Math.sin(t * 7);
  scene.userData.eyes.visible = (seg.prop === 'глаза' || seg.prop === 'лицо') && Math.sin(t * 1.7) > -0.9;
  renderer.render(scene, camera);
}

// эффект кассеты поверх кадра: расслоение, строки, шум, полоса трекинга, PLAY и время
function vhs(o, t, w, h) {
  o.globalCompositeOperation = 'lighter'; o.globalAlpha = 0.22; o.drawImage(o.canvas, 4, 0); o.drawImage(o.canvas, -3, 1);
  o.globalAlpha = 1; o.globalCompositeOperation = 'source-over';
  for (let y = 0; y < h; y += 4) { o.fillStyle = 'rgba(0,0,0,0.18)'; o.fillRect(0, y, w, 2); }
  const band = ((t * 0.37) % 1.3) * h; o.fillStyle = 'rgba(255,255,255,0.06)'; o.fillRect(0, band, w, h * 0.04);
  for (let k = 0; k < 260; k++) { const v = hash(k + Math.floor(t * 30) * 13); o.fillStyle = `rgba(255,255,255,${v * 0.18})`; o.fillRect(hash(k * 2.3 + t) * w, hash(k * 5.1 + t) * h, 2, 2); }
  const vg = o.createRadialGradient(w / 2, h / 2, h * 0.25, w / 2, h / 2, h * 0.75); vg.addColorStop(0, 'rgba(0,0,0,0)'); vg.addColorStop(1, 'rgba(0,0,0,0.55)'); o.fillStyle = vg; o.fillRect(0, 0, w, h);
  o.font = `${Math.round(w * 0.05)}px "DejaVu Sans Mono", monospace`; o.fillStyle = '#e8f0ff'; o.textAlign = 'left'; o.textBaseline = 'top';
  if (Math.floor(t * 2) % 2 === 0 || t > 2) o.fillText('▶ PLAY', w * 0.07, h * 0.06);
  const s = Math.floor(t), ts = `00:${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
  o.textAlign = 'right'; o.fillText('SP ' + ts, w * 0.93, h * 0.06);
  o.textAlign = 'left'; o.fillText('НОЯ. 14 1998', w * 0.07, h * 0.9);
}

buildRoom();
install({ canvas, renderFrame, post: vhs, load: (tl) => { TL = tl; FPS = tl.fps || 30; return {}; },
  info: () => { const ext = gl.getExtension('WEBGL_debug_renderer_info'); return ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : ''; } });
