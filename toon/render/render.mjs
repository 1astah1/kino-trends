// Рендер монтажного плана в MP4 без звука: node render.mjs timeline.json out.mp4 [--w 720 --h 1280] [--png кадр.png --at 3.2]
// Chrome без окна, WebGL через ANGLE→Vulkan→llvmpipe (в 3,5 раза быстрее SwiftShader на сервере без видеокарты),
// кадры кодируются в H.264 прямо в странице (WebCodecs), ffmpeg только упаковывает поток в mp4.
import { createRequire } from 'node:module';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';

const require = createRequire(import.meta.url);
const PW = process.env.TOON_PLAYWRIGHT || (fs.existsSync(new URL('./node_modules/playwright', import.meta.url)) ? new URL('./node_modules/playwright', import.meta.url).pathname : '/home/devaccess/auto-shorts/web/node_modules/playwright');
const { chromium } = require(PW);
const CHROME = process.env.TOON_CHROME || (process.platform === 'darwin' ? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
  : process.env.HOME + '/.cache/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell-linux64/chrome-headless-shell');
const ROOT = path.dirname(new URL(import.meta.url).pathname);
const MIME = { '.html': 'text/html', '.js': 'text/javascript', '.ttf': 'font/ttf' };

const args = process.argv.slice(2);
const opt = (k, d) => { const i = args.indexOf('--' + k); return i >= 0 ? args[i + 1] : d; };
const [tlPath, outPath] = args.filter((a, i) => !a.startsWith('--') && !(i > 0 && args[i - 1].startsWith('--')));
const W = +opt('w', 720), H = +opt('h', 1280);
const tl = JSON.parse(fs.readFileSync(tlPath, 'utf8'));

const srv = await new Promise(res => { const s = http.createServer((q, r) => {
  const p = path.join(ROOT, decodeURIComponent(new URL(q.url, 'http://x').pathname));
  if (!p.startsWith(ROOT) || !fs.existsSync(p) || fs.statSync(p).isDirectory()) { r.writeHead(404); return r.end(); }
  r.writeHead(200, { 'content-type': MIME[path.extname(p)] || 'application/octet-stream' }); fs.createReadStream(p).pipe(r);
}).listen(0, '127.0.0.1', () => res(s)); });
// мак (видеокарта M4, ANGLE→Metal) — основной рендер; сервер без видеокарты — запасной (ANGLE→Vulkan→llvmpipe)
const MAC = process.platform === 'darwin';
const env = MAC ? process.env : { ...process.env, VK_ICD_FILENAMES: '/usr/share/vulkan/icd.d/lvp_icd.json' };
const browser = await chromium.launch({ executablePath: CHROME, env,
  args: MAC ? ['--use-angle=metal', '--enable-gpu', '--ignore-gpu-blocklist']
            : ['--use-angle=vulkan', '--enable-features=Vulkan', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
try {
  const page = await browser.newPage({ viewport: { width: W, height: H }, deviceScaleFactor: 1 });
  page.on('pageerror', e => console.error('[сцена]', e.message));
  await page.goto(`http://127.0.0.1:${srv.address().port}/index.html?w=${W}&h=${H}&scene=${tl.scene || 'voxel'}`);
  await page.waitForFunction(() => window.__ready === true, null, { timeout: 60000 });
  const info = await page.evaluate(t => window.__load(t), tl);
  info.gl = await page.evaluate(() => window.__gl());
  if (opt('png')) {  // один кадр для проверки
    const url = await page.evaluate(i => window.__png(i), Math.round(+opt('at', 1) * (tl.fps || 30)));
    fs.writeFileSync(opt('png'), Buffer.from(url.split(',')[1], 'base64'));
    console.log(JSON.stringify({ png: opt('png'), ...info }));
  } else {
    const h264 = outPath.replace(/\.mp4$/, '') + '.h264'; const out = fs.createWriteStream(h264);
    await page.evaluate(() => window.__wcInit('avc1.640028', 6_000_000));
    const t0 = Date.now();
    for (let i = 0; i < info.frames; i++) {
      for (const b of await page.evaluate(n => window.__wcFrame(n), i)) out.write(Buffer.from(b, 'base64'));
      if (i % 150 === 0) console.error(`кадр ${i}/${info.frames}, ${((Date.now() - t0) / 1000 / (i + 1)).toFixed(2)} с/кадр`);
    }
    for (const b of await page.evaluate(() => window.__wcFlush())) out.write(Buffer.from(b, 'base64'));
    await new Promise(r => out.end(r));
    await new Promise((ok, bad) => spawn(MAC ? '/opt/homebrew/bin/ffmpeg' : 'ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y', '-r', String(tl.fps || 30), '-i', h264, '-c', 'copy', outPath], { stdio: 'inherit' })
      .on('close', c => c === 0 ? ok() : bad(new Error('ffmpeg ' + c))));
    fs.unlinkSync(h264);
    console.log(JSON.stringify({ mp4: outPath, frames: info.frames, sec_per_frame: +((Date.now() - t0) / 1000 / info.frames).toFixed(3) }));
  }
} finally { await browser.close(); srv.close(); }
