#!/usr/bin/env node
/**
 * Мини-сервер статического экспорта для e2e и ручной проверки без nginx.
 * Повторяет прод-раздачу (docs/ARCHITECTURE.md §7.1, §8.5): `try_files $uri $uri/ $uri.html =404`
 * и CSP-заголовок из §7.7 — так Playwright ловит нарушения CSP так же, как в проде.
 * `/api/*` проксируется на API (`AS_API_ORIGIN`, по умолчанию http://127.0.0.1:18300), включая SSE.
 *
 *   node scripts/serve-static.mjs [--dir out] [--port 18301] [--host 127.0.0.1]
 */
import { createReadStream, statSync } from "node:fs";
import http from "node:http";
import { extname, join, normalize, resolve } from "node:path";

const CSP =
  "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; font-src 'self'; " +
  "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; frame-ancestors 'none'";

const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".txt": "text/plain; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".woff2": "font/woff2",
  ".woff": "font/woff",
  ".webmanifest": "application/manifest+json",
};

function arg(name, fallback) {
  const index = process.argv.indexOf(`--${name}`);
  return index !== -1 && process.argv[index + 1] ? process.argv[index + 1] : fallback;
}

const root = resolve(process.cwd(), arg("dir", process.env.E2E_STATIC_DIR ?? "out"));
const port = Number(arg("port", process.env.PORT ?? "18301"));
const host = arg("host", "127.0.0.1");
const apiOrigin = new URL(process.env.AS_API_ORIGIN ?? "http://127.0.0.1:18300");

function fileIfExists(path) {
  try {
    const stat = statSync(path);
    return stat.isFile() ? path : null;
  } catch {
    return null;
  }
}

function resolveFile(urlPath) {
  // битый percent-encoding (например «%E0» без хвоста) бросает URIError и ронял весь процесс: отвечаем 404
  let decoded;
  try {
    decoded = decodeURIComponent(urlPath);
  } catch {
    return null;
  }
  const clean = normalize(decoded).replace(/^(\.\.[/\\])+/, "");
  const base = join(root, clean);
  if (!base.startsWith(root)) return null;
  return (
    fileIfExists(base) ?? fileIfExists(join(base, "index.html")) ?? fileIfExists(`${base.replace(/\/$/, "")}.html`)
  );
}

function proxy(req, res) {
  const upstream = http.request(
    {
      host: apiOrigin.hostname,
      port: apiOrigin.port || 80,
      method: req.method,
      path: req.url,
      headers: { ...req.headers, host: apiOrigin.host, "x-forwarded-proto": "http" },
    },
    (upstreamRes) => {
      res.writeHead(upstreamRes.statusCode ?? 502, upstreamRes.headers);
      upstreamRes.pipe(res);
    },
  );
  upstream.on("error", () => {
    if (!res.headersSent) res.writeHead(502, { "content-type": "application/json; charset=utf-8" });
    res.end(JSON.stringify({ error: { code: "bad_gateway", message: "API недоступен", details: {} } }));
  });
  req.pipe(upstream);
}

const server = http.createServer((req, res) => {
  let url;
  try {
    url = new URL(req.url ?? "/", "http://localhost");
  } catch {
    // «//», «//x:y» и подобное URL разбирает как адрес без хоста и бросает: раньше это роняло весь стенд
    res.writeHead(400, { "content-type": "text/plain; charset=utf-8" });
    return res.end("Bad request");
  }
  if (url.pathname.startsWith("/api/")) return proxy(req, res);

  const file = resolveFile(url.pathname);
  const headers = {
    "content-security-policy": CSP,
    "x-content-type-options": "nosniff",
    "referrer-policy": "same-origin",
  };
  if (!file) {
    const notFound = fileIfExists(join(root, "404.html"));
    res.writeHead(404, { ...headers, "content-type": TYPES[".html"] });
    if (notFound) createReadStream(notFound).pipe(res);
    else res.end("404");
    return;
  }
  const type = TYPES[extname(file).toLowerCase()] ?? "application/octet-stream";
  const cache = file.includes(`${join(root, "_next", "static")}`) ? "public, max-age=31536000, immutable" : "no-cache";
  res.writeHead(200, { ...headers, "content-type": type, "cache-control": cache });
  if (req.method === "HEAD") return res.end();
  createReadStream(file).pipe(res);
});

server.listen(port, host, () => {
  console.log(`serve-static: ${root} → http://${host}:${port} (API → ${apiOrigin.origin})`);
});

for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => server.close(() => process.exit(0)));
