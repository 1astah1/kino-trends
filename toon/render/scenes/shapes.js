// «Геометрия»: Круг-киноман, Квадрат-геймер, Треугольник-критик на 2D-холсте. Состояние — функция номера кадра.
import { install } from '../capture.js';

const qs = new URLSearchParams(location.search);
const W = +qs.get('w') || 720, H = +qs.get('h') || 1280;
const canvas = document.createElement('canvas'); canvas.width = W; canvas.height = H; document.body.appendChild(canvas);
const g = canvas.getContext('2d');
const U = W / 720;                      // единица масштаба: вёрстка задана под 720×1280
let TL = null, FPS = 30;

const lerp = (a, b, k) => a + (b - a) * k, clamp = (v, a, b) => Math.max(a, Math.min(b, v)), ease = k => k * k * (3 - 2 * k);
const hash = (n) => { const x = Math.sin(n * 127.1) * 43758.5453; return x - Math.floor(x); };

const BG = {
  'светлый фон': ['#f6f1e9', '#efe6f2'], 'вечер': ['#ffd9b8', '#c9b6f0'], 'кинозал': ['#3a3352', '#6b5a8e'], 'игровая': ['#c8f0ff', '#e3d4ff'],
};
const CH = {
  circle: { x: 150, y: 840, color: '#f3dcc0', dark: '#d9b892', shape: 'circle', size: 150 },
  square: { x: 590, y: 820, color: '#b8a8f0', dark: '#9884de', shape: 'square', size: 150 },
  triangle: { x: 370, y: 640, color: '#f7b98d', dark: '#e39a68', shape: 'triangle', size: 70, glasses: true, back: 1 },
};
const EMO = {
  'спокойно': { lid: 0, brow: 0, browY: 0, mouth: 0.2, open: 0 }, 'уверенно': { lid: 0.15, brow: -0.15, browY: 0, mouth: 0.35, open: 0 },
  'ехидно': { lid: 0.35, brow: 0.0, browY: 0.3, mouth: 0.3, open: 0, smirk: 0.5, asym: 1 }, 'шок': { lid: -0.25, brow: 0, browY: 1, mouth: 0, open: 0.8, round: 1 },
  'злость': { lid: 0.3, brow: 0.5, browY: -0.4, mouth: -0.4, open: 0 }, 'паника': { lid: -0.2, brow: -0.45, browY: 0.8, mouth: -0.3, open: 0.5 },
  'шёпот': { lid: 0.2, brow: -0.2, browY: 0.2, mouth: 0, open: 0, small: 1 }, 'восторг': { lid: -0.1, brow: 0, browY: 0.7, mouth: 0.9, open: 0.6 },
  'грусть': { lid: 0.35, brow: -0.45, browY: 0.3, mouth: -0.6, open: 0 }, 'невозмутимо': { lid: 0.5, brow: 0, browY: -0.1, mouth: 0, open: 0 },
};

function segAt(t) { const s = TL.segments; for (let i = s.length - 1; i >= 0; i--) if (t >= s[i].t0) return [s[i], s[i - 1] || null]; return [s[0], null]; }
function env(who, i) { const e = (TL.env || {})[who]; return e ? (e[Math.min(e.length - 1, i)] || 0) : 0; }

function rr(x, y, w, h, r) { g.beginPath(); g.moveTo(x + r, y); g.arcTo(x + w, y, x + w, y + h, r); g.arcTo(x + w, y + h, x, y + h, r); g.arcTo(x, y + h, x, y, r); g.arcTo(x, y, x + w, y, r); g.closePath(); }

function body(c, s) {
  g.fillStyle = c.color; g.strokeStyle = c.dark; g.lineWidth = 6;
  if (c.shape === 'circle') { g.beginPath(); g.arc(0, 0, s, 0, Math.PI * 2); g.fill(); g.stroke(); }
  else if (c.shape === 'square') { rr(-s * 0.85, -s, s * 1.7, s * 2, 34); g.fill(); g.stroke(); }
  else { g.beginPath(); g.moveTo(0, -s * 1.15); g.lineTo(s * 1.1, s * 0.75); g.lineTo(-s * 1.1, s * 0.75); g.closePath(); g.lineJoin = 'round'; g.fill(); g.stroke(); }
}

function face(c, s, emo, talk, look, t, key) {
  const ex = c.shape === 'triangle' ? s * 0.32 : s * 0.38, ey = c.shape === 'triangle' ? s * 0.05 : -s * 0.22, er = c.shape === 'triangle' ? s * 0.2 : s * 0.19;
  const bt = (t + key.length * 0.9) % 4.1, blink = bt < 0.14 ? Math.sin(bt / 0.14 * Math.PI) : 0;
  for (const side of [-1, 1]) {
    const x = side * ex, y = ey;
    g.fillStyle = '#fff'; g.strokeStyle = '#2b2333'; g.lineWidth = 4;
    g.beginPath(); g.ellipse(x, y, er, er * 1.15, 0, 0, Math.PI * 2); g.fill(); g.stroke();
    g.fillStyle = '#2b2333'; g.beginPath(); g.arc(x + look * er * 0.45, y + er * 0.12, er * 0.48, 0, Math.PI * 2); g.fill();
    g.fillStyle = '#fff'; g.beginPath(); g.arc(x + look * er * 0.45 - er * 0.18, y - er * 0.1, er * 0.14, 0, Math.PI * 2); g.fill();
    const lid = clamp(emo.lid + blink * 1.4 + (emo.asym && side === 1 ? 0.25 : 0), -0.3, 1.15);
    if (lid > 0) { g.fillStyle = c.color; g.beginPath(); g.ellipse(x, y - er * 1.15 + lid * er * 1.15, er * 1.05, er * 1.15 * lid, 0, Math.PI, 0); g.fill();
      g.strokeStyle = '#2b2333'; g.beginPath(); g.moveTo(x - er, y - er * 1.15 + lid * er * 2.0); g.lineTo(x + er, y - er * 1.15 + lid * er * 2.0); g.stroke(); }
    const by = y - er * 1.55 - emo.browY * er * 0.6 - (emo.asym && side === -1 ? er * 0.35 : 0);
    g.save(); g.translate(x, by); g.rotate(-side * emo.brow); g.strokeStyle = '#2b2333'; g.lineWidth = 7; g.lineCap = 'round';
    g.beginPath(); g.moveTo(-er * 0.8, 0); g.lineTo(er * 0.8, 0); g.stroke(); g.restore();
  }
  if (c.glasses) { g.strokeStyle = '#2b2333'; g.lineWidth = 4; for (const side of [-1, 1]) { g.beginPath(); g.arc(side * ex, ey, er * 1.35, 0, Math.PI * 2); g.stroke(); }
    g.beginPath(); g.moveTo(-ex + er * 1.35, ey); g.lineTo(ex - er * 1.35, ey); g.stroke(); }
  // рот: улыбка/хмурость + раскрытие по голосу
  const my = c.shape === 'triangle' ? s * 0.42 : s * 0.38, mw = c.shape === 'triangle' ? s * 0.32 : s * 0.42;
  const open = clamp(emo.open + talk * 1.3, 0, 1.4) * (emo.small ? 0.5 : 1);
  g.save(); g.translate(0, my); g.rotate((emo.smirk || 0) * 0.35);
  g.strokeStyle = '#2b2333'; g.lineWidth = 6; g.lineCap = 'round'; g.fillStyle = '#5a1f2c';
  if (open > 0.08) {
    g.beginPath(); if (emo.round) g.ellipse(0, 0, mw * 0.35, mw * 0.3 + open * mw * 0.25, 0, 0, Math.PI * 2);
    else { g.moveTo(-mw * 0.6, 0); g.quadraticCurveTo(0, emo.mouth * mw * 0.35, mw * 0.6, 0); g.quadraticCurveTo(0, mw * (0.15 + open * 0.55) + emo.mouth * mw * 0.3, -mw * 0.6, 0); }
    g.fill(); g.stroke();
  } else { g.beginPath(); g.moveTo(-mw * 0.55, 0); g.quadraticCurveTo(0, emo.mouth * mw * 0.55, mw * 0.55, 0); g.stroke(); }
  g.restore();
}

function limbs(c, s, act, talk, t, mine, key) {
  g.strokeStyle = '#2b2333'; g.lineWidth = 7; g.lineCap = 'round';
  const base = c.shape === 'square' ? s : c.shape === 'triangle' ? s * 0.75 : s * 0.92;
  for (const side of [-1, 1]) { // ноги
    const sx = side * s * 0.38; g.beginPath(); g.moveTo(sx, base - 4); g.lineTo(sx + side * 8, base + s * 0.55); g.lineTo(sx + side * 30, base + s * 0.55); g.stroke(); }
  const wave = Math.sin(t * 5 + key.length);
  for (const side of [-1, 1]) { // руки
    const sx = side * (c.shape === 'triangle' ? s * 0.62 : s * 0.9), sy = c.shape === 'triangle' ? s * 0.2 : s * 0.05;
    let ang = side * 0.9 + 0.15 * Math.sin(t * 2 + side);
    if (mine && side === 1 && (act === 'talk' || !act)) ang = 0.2 - 0.6 * talk - 0.25 * wave;
    if (act === 'jump' || act === 'grow') ang = side * 2.4;
    if (act === 'shake') ang = side * 0.4 + 0.3 * Math.sin(t * 40);
    if (act === 'turn_away') ang = side * 0.5;
    if (act === 'lean_in' && side === 1) ang = -0.3;
    const len = s * 0.6, ex = sx + Math.sin(ang) * len * side, ey = sy + Math.cos(ang) * len * (side === 1 ? -0.2 : 0.4);
    g.beginPath(); g.moveTo(sx, sy); g.lineTo(ex, ey); g.stroke(); g.fillStyle = '#2b2333'; g.beginPath(); g.arc(ex, ey, 7, 0, Math.PI * 2); g.fill();
  }
}

// предметы: рисуются кодом, без картинок
function prop(name, x, y, sz, t) {
  g.save(); g.translate(x, y); const p = 1 + 0.06 * Math.sin(t * 6); g.scale(p, p); g.lineWidth = 5; g.strokeStyle = '#2b2333'; g.lineJoin = 'round';
  const fill = (c) => { g.fillStyle = c; g.fill(); g.stroke(); };
  if (name === 'попкорн') { g.beginPath(); g.moveTo(-sz * 0.5, -sz * 0.3); g.lineTo(sz * 0.5, -sz * 0.3); g.lineTo(sz * 0.38, sz * 0.6); g.lineTo(-sz * 0.38, sz * 0.6); g.closePath(); fill('#e94b4b');
    for (let k = 0; k < 7; k++) { g.beginPath(); g.arc(-sz * 0.4 + k * sz * 0.13, -sz * 0.35 - (k % 2) * sz * 0.12, sz * 0.14, 0, Math.PI * 2); fill('#fff6d8'); } }
  else if (name === 'геймпад') { rr(-sz * 0.6, -sz * 0.3, sz * 1.2, sz * 0.6, sz * 0.28); fill('#3a3a46'); g.fillStyle = '#ff5c8a'; g.beginPath(); g.arc(sz * 0.3, 0, sz * 0.09, 0, 7); g.fill(); g.fillStyle = '#5fd4ff'; g.fillRect(-sz * 0.42, -sz * 0.04, sz * 0.24, sz * 0.08); g.fillRect(-sz * 0.34, -sz * 0.12, sz * 0.08, sz * 0.24); }
  else if (name === 'сердце') { g.beginPath(); g.moveTo(0, sz * 0.45); g.bezierCurveTo(-sz * 0.9, -sz * 0.1, -sz * 0.35, -sz * 0.75, 0, -sz * 0.25); g.bezierCurveTo(sz * 0.35, -sz * 0.75, sz * 0.9, -sz * 0.1, 0, sz * 0.45); fill('#ff4f7b'); }
  else if (name === 'молния') { g.beginPath(); g.moveTo(sz * 0.1, -sz * 0.6); g.lineTo(-sz * 0.3, sz * 0.05); g.lineTo(0, sz * 0.05); g.lineTo(-sz * 0.1, sz * 0.6); g.lineTo(sz * 0.32, -sz * 0.08); g.lineTo(0, -sz * 0.08); g.closePath(); fill('#ffd23f'); }
  else if (name === 'звезда') { g.beginPath(); for (let k = 0; k < 10; k++) { const r = k % 2 ? sz * 0.25 : sz * 0.6, a = k * Math.PI / 5 - Math.PI / 2; g.lineTo(Math.cos(a) * r, Math.sin(a) * r); } g.closePath(); fill('#ffd23f'); }
  else if (name === 'слеза') { g.beginPath(); g.moveTo(0, -sz * 0.5); g.quadraticCurveTo(sz * 0.45, sz * 0.15, 0, sz * 0.45); g.quadraticCurveTo(-sz * 0.45, sz * 0.15, 0, -sz * 0.5); fill('#7fc8ff'); }
  else if (name === 'деньги') { rr(-sz * 0.6, -sz * 0.32, sz * 1.2, sz * 0.64, 8); fill('#8fd58a'); g.fillStyle = '#2b2333'; g.font = `bold ${sz * 0.5}px "DejaVu Sans", sans-serif`; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText('₽', 0, 2); }
  else if (name === 'очки') { for (const s of [-1, 1]) { g.beginPath(); g.arc(s * sz * 0.3, 0, sz * 0.22, 0, 7); g.stroke(); } g.beginPath(); g.moveTo(-sz * 0.08, 0); g.lineTo(sz * 0.08, 0); g.stroke(); }
  else if (name === 'вопрос' || name === 'восклицание') { g.fillStyle = '#fff'; g.beginPath(); g.arc(0, 0, sz * 0.5, 0, 7); g.fill(); g.stroke(); g.fillStyle = '#2b2333';
    g.font = `bold ${sz * 0.7}px "DejaVu Sans", sans-serif`; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(name === 'вопрос' ? '?' : '!', 0, 3); }
  g.restore();
}

function renderFrame(i) {
  const t = i / FPS; const [seg, prev] = segAt(t);
  // фон
  const [c1, c2] = BG[TL.set] || BG['светлый фон'];
  const gr = g.createLinearGradient(0, 0, 0, H); gr.addColorStop(0, c1); gr.addColorStop(1, c2); g.fillStyle = gr; g.fillRect(0, 0, W, H);
  for (let k = 0; k < 60; k++) { g.fillStyle = `rgba(0,0,0,${0.025 + 0.02 * hash(k)})`; g.fillRect(hash(k * 3.1) * W, hash(k * 7.7) * H, 2 * U, 2 * U); }
  // камера: общий / двое / крупно / наезд — масштаб и центр, плавный переход 0.25 с
  const cast = (seg.cast || TL.cast).filter(k => CH[k]);
  const camFor = (s) => {
    if (!s) return { z: 1.12, x: 365, y: 780 };
    const shot = s.shot || 'двое', who = s.who;
    const pick = shot.includes('Круг') ? 'circle' : shot.includes('Квадрат') ? 'square' : shot.includes('Треугольник') ? 'triangle' : null;
    if (shot.startsWith('крупно')) { const c = CH[pick || who] || CH.circle; return { z: 1.75, x: c.x, y: c.y - 30 }; }
    if (shot === 'двое' && s.to && CH[s.to] && CH[who]) { const a = CH[who], b = CH[s.to]; return { z: 1.25, x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 + 60 }; }
    return { z: 1.12, x: 365, y: 780 };
  };
  const k = ease(clamp((t - seg.t0) / 0.25, 0, 1));
  const ca = camFor(prev), cb = camFor(seg);
  let z = lerp(ca.z, cb.z, k), cx = lerp(ca.x, cb.x, k), cy = lerp(ca.y, cb.y, k);
  if (seg.move === 'push' || seg.shot === 'наезд') z *= 1 + 0.12 * ease(clamp((t - seg.t0) / Math.max(0.5, seg.t1 - seg.t0), 0, 1));
  g.save(); g.translate(W / 2, H * 0.52); g.scale(z * U, z * U); g.translate(-cx, -cy);
  // пол
  g.fillStyle = 'rgba(0,0,0,0.06)'; g.fillRect(-400, 1010, 1600, 900);
  if (cast.includes('triangle')) { g.fillStyle = '#d8cfe6'; g.strokeStyle = '#b9add0'; g.lineWidth = 5; rr(300, 735, 140, 275, 14); g.fill(); g.stroke(); }
  for (const key of ['triangle', 'circle', 'square']) {
    if (!cast.includes(key)) continue;
    const c = CH[key], mine = seg.who === key, talk = env(key, i);
    const emo = EMO[(mine ? seg.emotion : seg.react?.[key]) || 'спокойно'] || EMO['спокойно'];
    const act = (mine ? seg.action : seg.react_action?.[key]) || (mine ? 'talk' : 'idle');
    let x = c.x, y = c.y, sc = 1, rot = 0;
    const lt = t - seg.t0;
    y += Math.sin(t * 2.2 + key.length) * 4 - talk * 6;
    if (act === 'bounce') y -= Math.abs(Math.sin(t * 7)) * 18;
    if (act === 'jump') y -= Math.max(0, Math.sin(clamp(lt / 0.5, 0, 1) * Math.PI)) * 90;
    if (act === 'shake') x += Math.sin(t * 50) * 4;
    if (act === 'lean_in') rot = (CH[seg.to || 'circle']?.x > x ? 1 : -1) * 0.12;
    if (act === 'turn_away') rot = -0.18;
    if (act === 'shrink') sc = lerp(1, 0.75, ease(clamp(lt / 0.4, 0, 1)));
    if (act === 'grow') sc = lerp(1, 1.2, ease(clamp(lt / 0.4, 0, 1)));
    if (act === 'spin') rot = ease(clamp(lt / 0.6, 0, 1)) * Math.PI * 2;
    const other = mine ? (seg.to || cast.find(o => o !== key)) : seg.who;
    const look = other && CH[other] ? clamp((CH[other].x - x) / 250, -1, 1) : 0;
    g.fillStyle = 'rgba(0,0,0,0.12)'; g.beginPath(); g.ellipse(x, c.shape === 'triangle' ? y + c.size * 1.33 : 1010 + 12, c.size * 0.9, 14, 0, 0, 7); g.fill();
    g.save(); g.translate(x, y); g.rotate(rot); g.scale(sc, sc);
    limbs(c, c.size, act, talk, t, mine, key); body(c, c.size); face(c, c.size, emo, talk, look, t, key);
    g.restore();
    const p = mine ? seg.prop : null;
    if (p) { const pk = ease(clamp(lt / 0.2, 0, 1)); const held = p === 'попкорн' || p === 'геймпад' || p === 'деньги';
      prop(p, held ? x + c.size * 1.05 : x, held ? y + c.size * 0.35 : y - c.size * 1.65, (held ? 70 : 64) * pk, t); }
    if (emo.round && mine) prop('восклицание', x, y - c.size * 1.55, 46, t);
  }
  g.restore();
}

install({ canvas, renderFrame, load: (tl) => { TL = tl; FPS = tl.fps || 30; return {}; } });
