#!/usr/bin/env node
/**
 * Гарантия «без чужих CDN» (docs/ARCHITECTURE.md §1.5, §7.7 п. 5).
 *
 * Сканирует статический экспорт (`out/` по умолчанию) и валит сборку, если в html/js/css/txt/json/svg
 * встречается внешний адрес вне allowlist. У оператора из РФ любой внешний ресурс = белый экран
 * или пустой шрифт, поэтому проверяем сами артефакты, а не исходники.
 *
 *   node scripts/check-no-external.mjs [каталог] [--allow host1,host2] [--verbose]
 *   AS_ALLOWED_HOSTS=umka.chat,u.umka.chat node scripts/check-no-external.mjs
 *
 * Правила:
 * 1. Любой `http(s)://…` в «загружающем» контексте (атрибуты src/href/srcset/poster/action/data,
 *    CSS `url()`/`@import`, JS `import()`/`fetch()`/`new Worker`/`importScripts`/`EventSource`/
 *    `new URL(…)` / `sendBeacon`) — нарушение, если хост не из своих доменов.
 * 2. Протокол-относительные `//host/…` в тех же контекстах — нарушение.
 * 3. Остальные вхождения `http(s)://` — нарушение, если не попали в allowlist:
 *    - свои домены (`--allow`, `AS_ALLOWED_HOSTS`);
 *    - идентификаторы-URI: пространства имён XML (w3.org) и `$schema` JSON Schema (json-schema.org)
 *      — по ним ничего не грузится;
 *    - ссылки на документацию и лицензии в текстах ошибок/метаданных библиотек (react.dev/errors,
 *      nextjs.org/docs, nuqs.dev, core-js): браузер по ним ничего не запрашивает. Такие строки
 *      в загружающем контексте всё равно считаются нарушением (правило 1);
 *    - «заглушки» без публичного хоста: хост без точки или с подстановкой шаблона (`https://a`,
 *      `http://${host}`, `http://[${ip}]`) — это проверки парсера URL внутри библиотек, во внешнюю сеть
 *      такой адрес уйти не может.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { extname, join, relative, resolve } from "node:path";

const SCANNED_EXTENSIONS = new Set([
  ".html",
  ".htm",
  ".js",
  ".mjs",
  ".cjs",
  ".css",
  ".txt",
  ".json",
  ".svg",
  ".xml",
  ".webmanifest",
]);

/** Идентификаторы-URI (пространства имён XML, `$schema` JSON Schema) — не сетевые ресурсы. */
const NAMESPACE_PREFIXES = [
  "http://www.w3.org/2000/svg",
  "http://www.w3.org/1999/xlink",
  "http://www.w3.org/1999/xhtml",
  "http://www.w3.org/1998/Math/MathML",
  "http://www.w3.org/XML/1998/namespace",
  "http://www.w3.org/2000/xmlns/",
  "https://json-schema.org/draft/",
  "http://json-schema.org/draft-",
];

/** Ссылки на документацию внутри текстов ошибок фреймворка (не загружаются). */
const INERT_DOC_PREFIXES = [
  "https://react.dev/errors/",
  "https://react.dev/link/",
  // recharts 3 тянет Redux Toolkit и Immer: в их минифицированных ошибках — ссылки на расшифровку кода
  "https://redux.js.org/Errors",
  "https://redux-toolkit.js.org/Errors",
  "https://bit.ly/3cXEKWf",
  "https://reactjs.org/docs/error-decoder.html",
  "https://nextjs.org/docs/",
  "https://nextjs.org/telemetry",
  "https://github.com/vercel/next.js/",
  "https://nuqs.dev/NUQS-",
  "https://github.com/zloirock/core-js",
];

const URL_RE = /https?:\/\/[^\s"'`()<>\\{}|^]+/gi;
const LOADING_CONTEXT_RE =
  /(?:\b(?:src|href|srcset|poster|action|data|formaction|background|xlink:href)\s*=\s*["']?\s*$|url\(\s*["']?\s*$|@import\s+(?:url\(\s*)?["']?\s*$|\b(?:import|fetch|importScripts|EventSource|WebSocket|Worker|SharedWorker|sendBeacon|URL)\s*\(\s*["'`]?\s*$)/i;
const PROTOCOL_RELATIVE_RE =
  /(?:\b(?:src|href|srcset|poster|action|data)\s*=\s*["']?|url\(\s*["']?|@import\s+["']|\b(?:import|fetch)\s*\(\s*["'`])(\/\/[a-z0-9.-]+\.[a-z]{2,}[^\s"'`)]*)/gi;

function parseArgs(argv) {
  const args = { dir: "out", allow: [], verbose: false };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--allow") args.allow.push(...String(argv[++i] ?? "").split(","));
    else if (arg.startsWith("--allow=")) args.allow.push(...arg.slice("--allow=".length).split(","));
    else if (arg === "--verbose" || arg === "-v") args.verbose = true;
    else if (!arg.startsWith("-")) args.dir = arg;
  }
  args.allow.push(...String(process.env.AS_ALLOWED_HOSTS ?? "").split(","));
  args.allow = args.allow.map((host) => host.trim().toLowerCase()).filter(Boolean);
  return args;
}

function* walk(dir) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    const stat = statSync(full);
    if (stat.isDirectory()) yield* walk(full);
    else if (SCANNED_EXTENSIONS.has(extname(entry).toLowerCase())) yield full;
  }
}

function hostOf(url) {
  try {
    return new URL(url.replace(/[.,;:]+$/, "")).hostname.toLowerCase();
  } catch {
    return "";
  }
}

/** Хост, который не может быть внешним ресурсом: без точки (a, x, n, тест) или шаблонная подстановка. */
function isPlaceholder(url, host) {
  const authority = url.replace(/^https?:\/\//i, "").split(/[/?#]/, 1)[0];
  if (/[$[{`]/.test(authority)) return true;
  if (!host) return true;
  return !host.includes(".") && host !== "localhost";
}

function isOwnHost(host, allow) {
  return allow.some((own) => host === own || host.endsWith(`.${own}`));
}

function lineOf(text, index) {
  let line = 1;
  for (let i = 0; i < index && i < text.length; i += 1) if (text.charCodeAt(i) === 10) line += 1;
  return line;
}

function snippet(text, index, length) {
  const start = Math.max(0, index - 60);
  const end = Math.min(text.length, index + length + 30);
  return text.slice(start, end).replace(/\s+/g, " ");
}

export function scanText(text, allow = []) {
  const findings = [];
  for (const match of text.matchAll(URL_RE)) {
    const url = match[0];
    const index = match.index ?? 0;
    const before = text.slice(Math.max(0, index - 40), index);
    const host = hostOf(url);
    const own = host && isOwnHost(host, allow);
    const loading = LOADING_CONTEXT_RE.test(before);
    let category;
    if (own) category = "own";
    else if (isPlaceholder(url, host)) category = "placeholder";
    else if (loading) category = "violation";
    else if (NAMESPACE_PREFIXES.some((prefix) => url.startsWith(prefix))) category = "namespace";
    else if (INERT_DOC_PREFIXES.some((prefix) => url.startsWith(prefix))) category = "inert-doc";
    else category = "violation";
    findings.push({ url, index, category, loading });
  }
  for (const match of text.matchAll(PROTOCOL_RELATIVE_RE)) {
    const url = match[1];
    const host = hostOf(`https:${url}`);
    if (host && isOwnHost(host, allow)) continue;
    findings.push({ url, index: (match.index ?? 0) + match[0].indexOf(url), category: "violation", loading: true });
  }
  return findings;
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const root = resolve(process.cwd(), args.dir);
  let stat;
  try {
    stat = statSync(root);
  } catch {
    console.error(`check-no-external: каталог ${args.dir} не найден. Сначала: NEXT_EXPORT=1 npm run build`);
    process.exit(2);
  }
  if (!stat.isDirectory()) {
    console.error(`check-no-external: ${args.dir} — не каталог`);
    process.exit(2);
  }

  const counts = { files: 0, violation: 0, namespace: 0, "inert-doc": 0, placeholder: 0, own: 0 };
  const inertSeen = new Map();
  const violations = [];

  for (const file of walk(root)) {
    counts.files += 1;
    const text = readFileSync(file, "utf8");
    for (const finding of scanText(text, args.allow)) {
      counts[finding.category] += 1;
      if (finding.category === "violation") {
        violations.push({
          file: relative(root, file),
          line: lineOf(text, finding.index),
          ...finding,
          context: snippet(text, finding.index, finding.url.length),
        });
      } else if (finding.category === "inert-doc" || (args.verbose && finding.category === "placeholder")) {
        const key = finding.url.replace(/[?#].*$/, "").replace(/\/[^/]*$/, "/");
        inertSeen.set(key, (inertSeen.get(key) ?? 0) + 1);
      }
    }
  }

  console.log(
    `check-no-external: ${args.dir}: файлов ${counts.files}, внешних URL (нарушений) ${counts.violation}; ` +
      `пропущено: идентификаторы-URI ${counts.namespace}, ссылки на документацию в текстах ошибок ${counts["inert-doc"]}, ` +
      `адреса-заглушки без публичного хоста ${counts.placeholder}` +
      (args.allow.length ? `, свои домены ${counts.own}` : ""),
  );
  if (args.verbose && inertSeen.size) {
    for (const [prefix, n] of inertSeen) console.log(`  пропущено ${prefix} ×${n}`);
  }
  if (violations.length) {
    console.error("\nВнешние адреса в артефактах (в РФ это белый экран):");
    for (const v of violations.slice(0, 50)) {
      console.error(
        `  ${v.file}:${v.line}  ${v.url}${v.loading ? "  [загружающий контекст]" : ""}\n      …${v.context}…`,
      );
    }
    if (violations.length > 50) console.error(`  … и ещё ${violations.length - 50}`);
    process.exit(1);
  }
}

// Запуск как скрипта (а не импорт scanText из тестов). Без import.meta: тесты грузят модуль через CJS-обёртку.
if (process.argv[1] && /check-no-external\.mjs$/.test(process.argv[1])) main();
