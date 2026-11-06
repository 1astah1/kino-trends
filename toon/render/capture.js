// Общий захват кадров для всех сцен: драйвер (render.mjs) зовёт __load(план) и __wcFrame(i); сцена отдаёт canvas,
// renderFrame(i) и load(план) → { frames }. Кадры кодируются в H.264 прямо в странице (WebCodecs).
// Субтитры рисуются здесь же, поверх кадра сцены (у ffmpeg на маке нет libass): план.captions = [{t0, t1, text, hl}].
const font = new FontFace('Caps', 'url(./fonts/Montserrat-Black.ttf)');
const fontReady = font.load().then(f => document.fonts.add(f)).catch(() => null);

export function install({ canvas, renderFrame, load, info, post }) {
  let W = canvas.width, H = canvas.height, FPS = 30, TL = null;
  const out = document.createElement('canvas'), o = out.getContext('2d');
  const compose = (i) => {
    renderFrame(i); const t = i / FPS;
    o.drawImage(canvas, 0, 0, W, H);
    if (post) post(o, t, W, H);
    const cap = (TL.captions || []).find(c => t >= c.t0 && t < c.t1);
    if (cap) {
      const k = Math.min(1, (t - cap.t0) / 0.08), size = Math.round(W * 0.085 * (0.85 + 0.15 * k));
      o.font = `${size}px Caps, "DejaVu Sans", sans-serif`; o.textAlign = 'center'; o.textBaseline = 'middle'; o.lineJoin = 'round';
      const words = cap.text.toUpperCase().split(' '), y = H * (TL.caption_y || 0.69), lh = size * 1.12;
      const lines = []; let cur = '';
      for (const w of words) { const tryL = (cur + ' ' + w).trim(); if (o.measureText(tryL).width > W * 0.82 && cur) { lines.push(cur); cur = w; } else cur = tryL; }
      lines.push(cur);
      const widest = Math.max(...lines.map(l => o.measureText(l).width));
      if (widest > W * 0.9) { const k2 = W * 0.9 / widest; o.font = `${Math.floor(size * k2)}px Caps, "DejaVu Sans", sans-serif`; }
      lines.forEach((ln, n) => { const yy = y + (n - (lines.length - 1) / 2) * lh;
        o.lineWidth = size * 0.22; o.strokeStyle = '#111'; o.strokeText(ln, W / 2, yy);
        o.fillStyle = cap.hl ? '#ffd23f' : '#ffffff'; o.fillText(ln, W / 2, yy); });
    }
  };
  window.__load = async (tl) => { await fontReady; TL = tl; FPS = tl.fps || 30; const r = load(tl) || {}; W = canvas.width; H = canvas.height;
    out.width = W; out.height = H; compose(0); return { frames: Math.round(tl.duration * FPS), ...r }; };
  window.__gl = () => (info ? info() : '2d');
  window.__png = (i) => { compose(i); return out.toDataURL('image/png'); };
  let enc = null, chunks = [], encErr = null;
  const b64 = (u8) => { let s = ''; for (let i = 0; i < u8.length; i += 0x8000) s += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000)); return btoa(s); };
  window.__wcInit = async (codec, bitrate) => {
    chunks = []; encErr = null;
    enc = new VideoEncoder({ output: (ch) => { const b = new Uint8Array(ch.byteLength); ch.copyTo(b); chunks.push(b); }, error: (e) => { encErr = e.message; } });
    enc.configure({ codec, width: W, height: H, bitrate, framerate: FPS, latencyMode: 'quality', avc: { format: 'annexb' } });
  };
  const drain = () => { const out = chunks.map(b64); chunks = []; return out; };
  window.__wcFrame = async (i) => {
    compose(i);
    const vf = new VideoFrame(out, { timestamp: Math.round(i * 1e6 / FPS), duration: Math.round(1e6 / FPS) });
    enc.encode(vf, { keyFrame: i % (FPS * 2) === 0 }); vf.close();
    while (enc.encodeQueueSize > 3) await new Promise(r => enc.addEventListener('dequeue', r, { once: true }));
    if (encErr) throw new Error(encErr);
    return drain();
  };
  window.__wcFlush = async () => { await enc.flush(); enc.close(); return drain(); };
  window.__ready = true;
}
