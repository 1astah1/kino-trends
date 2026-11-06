// «Аниме в офисе»: 2D-перекладка в духе аниме на холсте — офис, три героя с большими глазами, ауры, линии скорости, флешбэк.
import { install } from '../capture.js';

const qs = new URLSearchParams(location.search);
const W = +qs.get('w') || 720, H = +qs.get('h') || 1280;
const canvas = document.createElement('canvas'); canvas.width = W; canvas.height = H; document.body.appendChild(canvas);
const g = canvas.getContext('2d'); const U = W / 720;
let TL = null, FPS = 30;
const clamp = (v, a, b) => Math.max(a, Math.min(b, v)), ease = k => k * k * (3 - 2 * k), lerp = (a, b, k) => a + (b - a) * k;
const hash = (n) => { const x = Math.sin(n * 127.1) * 43758.5453; return x - Math.floor(x); };

const CH = {
  yulia: { x: 250, skin: '#ffe2cf', hair: '#1d1a26', hairHi: '#3b3550', eye: '#7a4a2a', top: '#f7f7fb', bottom: '#2b2f45', style: 'pony', h: 1.0 },
  boss: { x: 520, skin: '#f2cfb4', hair: '#8b8d96', hairHi: '#b2b4bc', eye: '#3a4a5a', top: '#2f3a52', bottom: '#2a2f3a', style: 'short', glasses: '#222', tie: '#b8323a', h: 1.06 },
  buh: { x: 480, skin: '#f6dccb', hair: '#6e1d2e', hairHi: '#9a3346', eye: '#5a1f7a', top: '#5a3a7a', bottom: '#2a2233', style: 'bun', glasses: '#5a1f7a', cat: 1, h: 0.98 },
};
const EMO = { 'спокойно': [1, 0, 0.1], 'уверенно': [0.95, -0.1, 0.3], 'ехидно': [0.7, 0.2, 0.35], 'шок': [1.35, 0.5, -0.1], 'злость': [0.85, -0.5, -0.3],
  'паника': [1.3, 0.4, -0.2], 'шёпот': [0.9, 0.1, 0], 'восторг': [1.25, 0.4, 0.6], 'грусть': [0.95, 0.35, -0.35], 'невозмутимо': [0.65, 0, 0] };

function office(set, t) {
  const sky = g.createLinearGradient(0, 0, 0, H * 0.55); sky.addColorStop(0, '#8fc9ff'); sky.addColorStop(1, '#e9f4ff'); g.fillStyle = sky; g.fillRect(0, 0, W, H);
  if (set === 'кухня с кулером') { g.fillStyle = '#eef0e8'; g.fillRect(0, 0, W, H); g.fillStyle = '#d7dbe0'; g.fillRect(0, H * 0.62, W, H);
    g.fillStyle = '#cfe8ff'; g.fillRect(560 * U, 420 * U, 90 * U, 120 * U); g.fillStyle = '#fff'; g.fillRect(565 * U, 540 * U, 80 * U, 230 * U); g.fillStyle = '#6fa8dc'; g.fillRect(585 * U, 330 * U, 50 * U, 90 * U);
    g.fillStyle = '#c9ccd2'; g.fillRect(40 * U, 380 * U, 160 * U, 420 * U); return; }
  // окна с городом
  for (let k = 0; k < 4; k++) { const x = (20 + k * 175) * U; g.fillStyle = 'rgba(255,255,255,0.0)';
    for (let b = 0; b < 4; b++) { g.fillStyle = `rgba(80,110,160,${0.25 + 0.1 * (b % 2)})`; const bh = (120 + hash(k * 9 + b) * 200) * U; g.fillRect(x + b * 40 * U, H * 0.5 - bh, 34 * U, bh); } }
  g.fillStyle = '#f1efe9'; for (let k = 0; k <= 4; k++) g.fillRect((k * 175) * U, 0, 20 * U, H * 0.5);
  g.fillStyle = '#f1efe9'; g.fillRect(0, H * 0.5, W, H * 0.5);
  g.fillStyle = '#d8d4cc'; g.fillRect(0, H * 0.62, W, H * 0.38);
  if (set === 'переговорка') { g.fillStyle = '#fff'; g.fillRect(140 * U, 360 * U, 440 * U, 220 * U); g.strokeStyle = '#999'; g.lineWidth = 4 * U; g.strokeRect(140 * U, 360 * U, 440 * U, 220 * U);
    g.strokeStyle = '#d9483b'; g.beginPath(); g.moveTo(170 * U, 540 * U); g.lineTo(260 * U, 470 * U); g.lineTo(350 * U, 500 * U); g.lineTo(450 * U, 400 * U); g.stroke(); return; }
  for (let k = 0; k < 3; k++) { const x = (40 + k * 230) * U, y = H * 0.66; g.fillStyle = '#b9b3a8'; g.fillRect(x, y, 200 * U, 16 * U); g.fillStyle = '#2b2f3a'; g.fillRect(x + 50 * U, y - 90 * U, 100 * U, 70 * U);
    g.fillStyle = '#7fd4ff'; g.fillRect(x + 56 * U, y - 84 * U, 88 * U, 58 * U); g.fillStyle = '#2b2f3a'; g.fillRect(x + 92 * U, y - 20 * U, 16 * U, 20 * U); }
  g.fillStyle = '#4c9a5a'; g.beginPath(); g.ellipse(690 * U, H * 0.6, 40 * U, 70 * U, 0, 0, 7); g.fill();
}

function speedLines(cx, cy, n, t, color = 'rgba(255,255,255,0.75)') {
  g.save(); g.strokeStyle = color; for (let k = 0; k < n; k++) { const a = hash(k + Math.floor(t * 12) * 0.13) * Math.PI * 2, r0 = (260 + hash(k * 3) * 200) * U, r1 = r0 + (400 + hash(k * 7) * 400) * U;
    g.lineWidth = (2 + hash(k * 5) * 6) * U; g.beginPath(); g.moveTo(cx + Math.cos(a) * r0, cy + Math.sin(a) * r0); g.lineTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1); g.stroke(); } g.restore();
}

function person(c, key, emo, talk, act, t, lt, mine) {
  const s = 1.0 * c.h, [eyeK, browK, mouthK] = emo;
  g.save(); g.scale(s, s);
  // аура превращения
  if (act === 'power_up' && mine) { for (let k = 0; k < 3; k++) { const r = (150 + k * 40 + Math.sin(t * 6 + k) * 10); g.strokeStyle = `rgba(255,214,90,${0.65 - k * 0.15})`; g.lineWidth = 10 - k * 2;
      g.beginPath(); g.ellipse(0, -60, r * 0.75, r * 1.4, Math.sin(t * 2 + k) * 0.2, 0, 7); g.stroke(); }
    for (let k = 0; k < 14; k++) { g.fillStyle = 'rgba(255,240,180,0.9)'; const a = t * 2 + k; g.fillRect(Math.cos(a) * 170, -60 + Math.sin(a * 1.3) * 260 - (t * 60 % 80), 6, 6); } }
  // ноги и тело
  g.fillStyle = c.bottom; g.fillRect(-48, 150, 40, 190); g.fillRect(8, 150, 40, 190); g.fillStyle = '#1b1b22'; g.fillRect(-52, 330, 46, 16); g.fillRect(6, 330, 46, 16);
  g.fillStyle = c.top; g.beginPath(); g.moveTo(-70, 0); g.lineTo(70, 0); g.lineTo(62, 165); g.lineTo(-62, 165); g.closePath(); g.fill();
  if (c.tie) { g.fillStyle = c.tie; g.beginPath(); g.moveTo(0, 6); g.lineTo(10, 30); g.lineTo(0, 120); g.lineTo(-10, 30); g.closePath(); g.fill(); g.fillStyle = '#fff'; g.beginPath(); g.moveTo(-20, 0); g.lineTo(0, 22); g.lineTo(20, 0); g.fill(); }
  // руки
  const armAng = { point: [-0.15, -1.45], attack: [-0.3, -1.6], power_up: [-2.6, 2.6], shock: [-0.9, 0.9], bow: [0.2, -0.2] }[act] || [0.12 + 0.05 * Math.sin(t * 2), -(0.12 + (mine ? 0.5 * talk : 0))];
  for (const [side, ang] of [[-1, armAng[0]], [1, armAng[1]]]) { g.save(); g.translate(side * 66, 18); g.rotate(-side * 0 + ang * (side === 1 ? 1 : -1) * -1);
    g.fillStyle = c.top; g.fillRect(-14, 0, 28, 120); g.fillStyle = c.skin; g.beginPath(); g.arc(0, 130, 15, 0, 7); g.fill(); g.restore(); }
  // шея и голова
  g.fillStyle = c.skin; g.fillRect(-16, -26, 32, 30);
  g.save(); g.translate(0, -120); g.rotate(act === 'bow' ? 0.35 : Math.sin(t * 1.3 + key.length) * 0.03);
  if (c.style === 'pony') { g.fillStyle = c.hair; g.beginPath(); g.ellipse(70 + Math.sin(t * 5) * (act === 'power_up' ? 14 : 3), 10, 26, 80, 0.3, 0, 7); g.fill(); }
  if (c.style === 'bun') { g.fillStyle = c.hair; g.beginPath(); g.arc(0, -98, 34, 0, 7); g.fill(); }
  g.fillStyle = c.hair; g.beginPath(); g.ellipse(0, -10, 92, 100, 0, Math.PI, 0); g.lineTo(92, 40); g.lineTo(-92, 40); g.fill();
  g.fillStyle = c.skin; g.beginPath(); g.moveTo(-74, -10); g.quadraticCurveTo(-78, 60, -30, 92); g.lineTo(0, 104); g.lineTo(30, 92); g.quadraticCurveTo(78, 60, 74, -10); g.quadraticCurveTo(0, -50, -74, -10); g.fill();
  // чёлка
  g.fillStyle = c.hair; g.beginPath(); g.moveTo(-82, -20);
  const spikes = c.style === 'short' ? 5 : 7; for (let k = 0; k <= spikes; k++) { const x = -82 + k * 164 / spikes; g.lineTo(x, k % 2 ? 12 + (c.style === 'pony' ? 14 : 0) : -40); } g.lineTo(82, -60); g.lineTo(-82, -60); g.fill();
  g.fillStyle = c.hairHi; g.fillRect(-40, -62, 30, 6);
  // глаза
  const bt = (t + key.length) % 3.6 < 0.12 ? 0.12 : 1, eh = 34 * eyeK * bt;
  for (const side of [-1, 1]) { const ex = side * 34, ey = 22;
    g.fillStyle = '#fff'; g.beginPath(); g.ellipse(ex, ey, 22, eh, 0, 0, 7); g.fill();
    if (bt > 0.5) { const ig = g.createLinearGradient(0, ey - eh, 0, ey + eh); ig.addColorStop(0, '#1a1018'); ig.addColorStop(1, c.eye); g.fillStyle = ig;
      g.beginPath(); g.ellipse(ex + side * 2, ey + 4, 15, Math.max(2, eh * 0.78), 0, 0, 7); g.fill();
      g.fillStyle = '#fff'; g.beginPath(); g.arc(ex - 6, ey - eh * 0.35, 6, 0, 7); g.fill(); g.beginPath(); g.arc(ex + 5, ey + eh * 0.35, 3, 0, 7); g.fill(); }
    g.strokeStyle = '#1a1018'; g.lineWidth = 6; g.beginPath(); g.moveTo(ex - 24, ey - eh * 0.9 + (c.cat ? side * 4 : 0)); g.quadraticCurveTo(ex, ey - eh * 1.15, ex + 24, ey - eh * 0.85 - (c.cat ? 6 : 0)); g.stroke();
    g.lineWidth = 4; g.beginPath(); g.moveTo(ex - 18, ey - 52 - browK * 14 + side * browK * -6); g.lineTo(ex + 18, ey - 52 - browK * 14 - side * browK * -6); g.stroke(); }
  if (c.glasses) { g.strokeStyle = c.glasses; g.lineWidth = 4; for (const side of [-1, 1]) { g.beginPath(); if (c.cat) { g.moveTo(side * 34 - 30, 10); g.lineTo(side * 34 + 30, 0 - (side > 0 ? 10 : -6)); g.lineTo(side * 34 + 26, 40); g.lineTo(side * 34 - 26, 40); g.closePath(); } else g.rect(side * 34 - 28, 0, 56, 44); g.stroke(); }
    g.beginPath(); g.moveTo(-6, 18); g.lineTo(6, 18); g.stroke(); }
  // румянец, рот
  g.fillStyle = 'rgba(255,120,140,0.35)'; for (const side of [-1, 1]) { g.beginPath(); g.ellipse(side * 50, 60, 14, 6, 0, 0, 7); g.fill(); }
  const open = clamp(talk * 1.3 + (eyeK > 1.2 ? 0.4 : 0), 0, 1.3);
  g.fillStyle = '#7a2a3a'; g.strokeStyle = '#3a1018'; g.lineWidth = 3;
  if (open > 0.1) { g.beginPath(); g.ellipse(0, 78, 12 + 6 * open, 6 + 14 * open, 0, 0, 7); g.fill(); g.stroke(); }
  else { g.beginPath(); g.moveTo(-12, 78); g.quadraticCurveTo(0, 78 + mouthK * 14, 12, 78 - (c.cat ? 6 : 0)); g.stroke(); }
  if (act === 'shock' || eyeK > 1.25) { g.strokeStyle = 'rgba(70,110,220,0.6)'; g.lineWidth = 4; for (let k = 0; k < 7; k++) { g.beginPath(); g.moveTo(-60 + k * 20, -40); g.lineTo(-60 + k * 20, 10); g.stroke(); }
    g.fillStyle = '#9fd4ff'; g.beginPath(); g.moveTo(84, -10); g.quadraticCurveTo(100, 20, 84, 30); g.quadraticCurveTo(68, 20, 84, -10); g.fill(); }
  g.restore(); g.restore();
}

function segAt(t) { const s = TL.segments; for (let i = s.length - 1; i >= 0; i--) if (t >= s[i].t0) return [s[i], s[i - 1] || null]; return [s[0], null]; }
function renderFrame(i) {
  const t = i / FPS, [seg, prev] = segAt(t), lt = t - seg.t0, dur = Math.max(0.5, seg.t1 - seg.t0);
  const cast = (seg.cast || TL.cast).filter(k => CH[k]);
  const pos = {}; cast.forEach((k, n) => { pos[k] = cast.length === 1 ? 360 : n === 0 ? 220 : n === 1 ? 500 : 640; });
  const shot = seg.shot || 'общий', who = CH[seg.who] ? seg.who : cast[0];
  const pick = shot.includes('Юля') ? 'yulia' : shot.includes('Начальник') ? 'boss' : shot.includes('Бухгалтер') ? 'buh' : null;
  let z = 1, cx = 360, cy = 800, rot = 0;
  if (shot.startsWith('крупно')) { const k = CH[pick || who] && pos[pick || who] != null ? (pick || who) : who; z = 2.2; cx = pos[k] ?? 360; cy = 700; }
  if (shot === 'драма') { z = 1.9; cx = pos[who] ?? 360; cy = 690; rot = -0.12; }
  if (seg.move === 'push' || shot === 'наезд') { z *= 1 + 0.25 * ease(clamp(lt / dur, 0, 1)); if (shot === 'наезд') { cx = pos[who] ?? 360; cy = 720; z = Math.max(z, 1.6); } }
  const flash = seg.action === 'attack' || seg.action === 'shock' ? clamp(1 - lt / 0.18, 0, 1) : 0;
  g.save(); g.translate(W / 2, H / 2); g.rotate(rot); g.scale(z, z); g.translate(-W / 2 - (cx - 360) * U, -H / 2 - (cy - 760) * U);
  office(TL.set, t);
  const drama = shot === 'драма' || seg.action === 'power_up' || seg.action === 'attack';
  if (drama) { g.fillStyle = seg.action === 'power_up' ? 'rgba(255,200,60,0.35)' : 'rgba(30,20,60,0.35)'; g.fillRect(0, 0, W, H); speedLines(W / 2, H * 0.42, 70, t); }
  for (const k of cast) {
    const c = CH[k], mine = seg.who === k, talk = ((TL.env || {})[k] || [])[i] || 0;
    const emo = EMO[(mine ? seg.emotion : seg.react?.[k]) || 'спокойно'] || EMO['спокойно'];
    const act = (mine ? seg.action : seg.react_action?.[k]) || 'idle';
    g.save(); g.translate(pos[k] * U, 720 * U + (act === 'power_up' && mine ? -Math.sin(clamp(lt / 0.6, 0, 1) * Math.PI / 2) * 40 * U : 0) + Math.sin(t * 2 + k.length) * 3 * U);
    g.scale(U * 1.25, U * 1.25);
    person(c, k, emo, talk, act, t, lt, mine); g.restore();
  }
  g.restore();
  if (seg.action === 'flashback') { const d = g.getImageData(0, 0, W, H); const a = d.data; for (let p = 0; p < a.length; p += 4) { const v = a[p] * 0.3 + a[p + 1] * 0.59 + a[p + 2] * 0.11; a[p] = v * 1.08; a[p + 1] = v * 0.92; a[p + 2] = v * 0.72; } g.putImageData(d, 0, 0);
    const vg = g.createRadialGradient(W / 2, H / 2, H * 0.2, W / 2, H / 2, H * 0.7); vg.addColorStop(0, 'rgba(0,0,0,0)'); vg.addColorStop(1, 'rgba(40,20,0,0.6)'); g.fillStyle = vg; g.fillRect(0, 0, W, H); }
  if (flash > 0) { g.fillStyle = `rgba(255,255,255,${flash * 0.85})`; g.fillRect(0, 0, W, H); }
}
install({ canvas, renderFrame, load: (tl) => { TL = tl; FPS = tl.fps || 30; return {}; } });
