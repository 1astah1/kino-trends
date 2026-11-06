/**
 * MSW-моки пакетов, публикаций, трекинг-ссылок и вебхука продукта (поток M3-C) по типам контракта
 * `lib/api/schema.d.ts`. Повторяют поведение services/{packages,publications,tracking,ingest}.py в том,
 * что важно экранам «Пакеты» и «Вебхук»:
 * - тексты: 422 `validation_error` с `errors[].type` (attribution_required, hashtag_format, длины),
 *   правка переводит `texts_mode` в operator; чек-лист `checklist_code_absent`; «залил, ссылку позже»;
 *   скачивание видео ставит downloaded и пункт file_downloaded;
 * - регистрация ссылки: разбор тем же `lib/platform-url.ts` (422 `unsupported_url` + details.reason),
 *   422 `platform_mismatch`, 409 `publication_exists`, 409 `package_not_ready`, `Idempotency-Key`;
 *   VK проверяется сразу по владельцу, YouTube — «проверяется», через 3 с — подтверждён;
 * - «Проблемы»: публикация с `channel_mismatch`, повторная проверка, удаление регистрации (admin);
 * - ссылки каналов: повторный запрос отдаёт существующую; вебхук: секрет один раз, тестовое событие.
 *
 * Подключение: `handlers.ts` разворачивает `packagesHandlers` **до** общего 501-обработчика.
 * Каналы taro_main / astro_main / taro_vk совпадают с моками каталога и Конвейера.
 */
import { http, HttpResponse } from "msw";

import type { components } from "@/lib/api/schema";
import { parseVideoUrl } from "@/lib/platform-url";

import { readSession, users } from "../db";

type S = components["schemas"];
type Role = S["Role"];

const API = "/api/v1";
const START = Date.now();
const MIN = 60_000;
const HOUR = 60 * MIN;
const iso = (offsetMs: number) => new Date(START + offsetMs).toISOString();
const nowIso = () => new Date().toISOString();
const OPERATORS: Role[] = ["admin", "operator"];
const HASHTAG_RE = /^#[^\s#]{1,100}$/u;

// ---------------------------------------------------------------- доступ и ошибки

function error(status: number, code: string, message: string, details: Record<string, unknown> = {}) {
  return HttpResponse.json({ error: { code, message, details } } satisfies S["ErrorResponse"], { status });
}

function invalid(field: string, msg: string, type = "value_error") {
  return error(422, "validation_error", msg, { errors: [{ loc: ["body", field], msg, type }] });
}

function guard(request: Request, roles?: Role[]): { userId: number; role: Role } | Response {
  const session = readSession();
  const user = session ? users.find((item) => item.id === session.userId && item.is_active) : undefined;
  if (!session || !user) return error(401, "not_authenticated", "Нужно войти");
  const unsafe = !["GET", "HEAD", "OPTIONS"].includes(request.method.toUpperCase());
  if (unsafe && request.headers.get("X-CSRF-Token") !== session.csrf) {
    return error(403, "csrf_failed", "Нет или неверный X-CSRF-Token");
  }
  if (roles && !roles.includes(user.role)) return error(403, "forbidden", "Недостаточно прав");
  return { userId: user.id, role: user.role };
}

// ---------------------------------------------------------------- каналы и продукты (минимум для пакетов)

interface MockChannel {
  id: string;
  title: string;
  platform: S["Platform"];
  handle: string;
  ref: string | null;
  product: string;
}

const CHANNELS: MockChannel[] = [
  {
    id: "taro_main",
    title: "Таро — выбери карту",
    platform: "youtube",
    handle: "@taro_karta",
    ref: "UCtaro000000000000000001",
    product: "umka",
  },
  {
    id: "astro_main",
    title: "Астро на каждый день",
    platform: "youtube",
    handle: "@astro_day",
    ref: "UCastro00000000000000001",
    product: "umka",
  },
  { id: "taro_vk", title: "Таро · VK Клипы", platform: "vk", handle: "club228001", ref: "-228001", product: "umka" },
];

const PRODUCT_HOSTS: Record<string, { host: string; bot: string | null; landing: string | null }> = {
  umka: { host: "u.umka.chat", bot: "umka_bot", landing: "umka.chat/" },
  vpn_bot: { host: "u.vpn.example", bot: "tgbpn_bot", landing: null },
};

function channelOf(id: string): MockChannel {
  return CHANNELS.find((channel) => channel.id === id) ?? CHANNELS[0];
}

function accountHint(channel: MockChannel): string {
  return channel.platform === "youtube"
    ? `Brand Account: ${channel.title} (${channel.handle})`
    : `Сообщество VK: ${channel.title} (${channel.handle})`;
}

function trackingOf(code: string, productId = "umka", platform: S["Platform"] = "youtube"): S["PackageTracking"] {
  const product = PRODUCT_HOSTS[productId] ?? PRODUCT_HOSTS.umka;
  return {
    code,
    redirect_url: `https://${product.host}/${code}`,
    telegram_url: product.bot ? `tg://resolve?domain=${product.bot}&start=${code}` : null,
    telegram_https_url: product.bot ? `https://telegram.me/${product.bot}?start=${code}` : null,
    site_url: product.landing
      ? `https://${product.landing}?utm_source=${platform === "youtube" ? "yt" : "vk"}&utm_medium=shorts&utm_campaign=${code}`
      : null,
  };
}

// ---------------------------------------------------------------- пакеты

const CHECK_BASE = ["file_downloaded", "right_account", "texts_pasted", "hashtags"] as const;

function checklist(
  platform: S["Platform"],
  { ai, pinned, done = [] }: { ai: boolean; pinned: boolean; done?: string[] },
) {
  const codes: Array<{ code: S["ChecklistCode"]; required: boolean }> = CHECK_BASE.map((code) => ({
    code,
    required: true,
  }));
  if (platform === "youtube") codes.push({ code: "related_video", required: false });
  codes.push({ code: "pinned_comment", required: pinned });
  codes.push({ code: "ai_disclosure", required: ai });
  codes.push({ code: "published", required: true });
  return codes.map(({ code, required }): S["ChecklistItem"] => ({
    code,
    required,
    done: done.includes(code),
    done_at: done.includes(code) ? iso(-30 * MIN) : null,
    done_by: done.includes(code) ? 1 : null,
  }));
}

const TEXT_LABELS: Record<S["PackageTextsMode"], string> = {
  llm: "Тексты написал LLM — проверьте перед заливкой",
  template: "Шаблон без LLM — проверьте тексты",
  operator: "Правка оператора",
};

interface PackageSeed {
  id: number;
  jobId: number;
  uid: string;
  kind: S["JobKind"];
  status: S["PackageStatus"];
  channelId: string;
  code: string;
  title: string;
  description: string;
  hashtags: string[];
  pinned: string | null;
  mode: S["PackageTextsMode"];
  ai: boolean;
  attribution?: string | null;
  originality?: S["PackageOriginality"] | null;
  audioNote?: string | null;
  createdAgoMin: number;
  downloaded?: boolean;
  markedUploadedAgoMin?: number;
  done?: string[];
}

function makePackage(seed: PackageSeed): S["UploadPackage"] {
  const channel = channelOf(seed.channelId);
  const date = new Date(START - seed.createdAgoMin * MIN).toISOString().slice(0, 10).replaceAll("-", "");
  return {
    id: seed.id,
    job_id: seed.jobId,
    video_uid: seed.uid,
    kind: seed.kind,
    status: seed.status,
    channel: {
      id: channel.id,
      display_name: channel.title,
      platform: channel.platform,
      account_hint: accountHint(channel),
      platform_channel_ref: channel.ref,
    },
    texts: {
      title: seed.title,
      title_max: 100,
      description: seed.description,
      description_max: 5000,
      hashtags: seed.hashtags,
      pinned_comment: seed.pinned,
      related_video_hint:
        channel.platform === "youtube"
          ? "Related video: поставьте длинный ролик той же темы (Studio → ролик → Related video)"
          : null,
      audio_note: seed.audioNote ?? "Звук уже сведён: трек из библиотеки площадки не добавляйте.",
      attribution_text: seed.attribution ?? null,
      texts_mode: seed.mode,
      texts_label: TEXT_LABELS[seed.mode],
    },
    checklist: checklist(channel.platform, { ai: seed.ai, pinned: !!seed.pinned, done: seed.done }),
    tracking: trackingOf(seed.code, channel.product, channel.platform),
    file: {
      download_url: `${API}/packages/${seed.id}/video`,
      filename: `${channel.id}_${date}_${seed.code}.mp4`,
      size_bytes: 14_680_064 + seed.id * 1024,
      duration_sec: seed.kind === "clip" ? 41.6 : 27.2,
      text_url: `${API}/packages/${seed.id}/text`,
      zip_url: `${API}/packages/${seed.id}/zip`,
    },
    ai_disclosure_required: seed.ai,
    originality: seed.originality ?? null,
    publication: null,
    downloaded_at: seed.downloaded ? iso(-(seed.markedUploadedAgoMin ?? 20) * MIN - 5 * MIN) : null,
    marked_uploaded_at: seed.markedUploadedAgoMin ? iso(-seed.markedUploadedAgoMin * MIN) : null,
    created_at: iso(-seed.createdAgoMin * MIN),
    updated_at: iso(-seed.createdAgoMin * MIN),
  };
}

const packages: S["UploadPackage"][] = [
  makePackage({
    id: 501,
    jobId: 1301,
    uid: "tr-0927-a1",
    kind: "generate",
    status: "ready",
    channelId: "taro_main",
    code: "k7m2q",
    title: "Карта дня: что скрывает Башня",
    description: "Вытяни карту и узнай, что ждёт тебя сегодня.\n\nПодробнее: u.umka.chat/k7m2q (ссылка в шапке канала)",
    hashtags: ["#таро", "#картадня", "#shorts"],
    pinned: "Какая карта выпала тебе? Полный расклад — по ссылке в шапке канала",
    mode: "llm",
    ai: true,
    createdAgoMin: 95,
  }),
  makePackage({
    id: 502,
    jobId: 1302,
    uid: "cl-0927-b4",
    kind: "clip",
    status: "ready",
    channelId: "astro_main",
    code: "p4x9d",
    title: "Почему Меркурий «ретроградит»: объяснение за 40 секунд",
    description:
      "Коротко о ретроградном Меркурии — с моим разбором.\n\nИсточник: «Лекция о планетах», А. Шалёпа, CC BY 4.0\nИзменения: вырезан фрагмент 12:40–13:21, добавлены авторские вставки и субтитры.\n\nПодробнее: u.umka.chat/p4x9d (ссылка в шапке канала)",
    hashtags: ["#астрология", "#меркурий"],
    pinned: null,
    mode: "template",
    ai: true,
    attribution: "Источник: «Лекция о планетах», А. Шалёпа, CC BY 4.0",
    originality: {
      verdict: "pass",
      author_share: 0.34,
      gate_applies: true,
      gate_passed: true,
      suggestions: ["Можно усилить вывод: своё мнение в последних 3 секундах держит досмотр"],
    },
    audioNote: "Звук из источника (CC BY): трек из библиотеки площадки не добавляйте.",
    createdAgoMin: 60,
  }),
  makePackage({
    id: 503,
    jobId: 1303,
    uid: "tr-0927-c2",
    kind: "generate",
    status: "downloaded",
    channelId: "taro_vk",
    code: "w3r8t",
    title: "Три карты на неделю",
    description:
      "Расклад на неделю — выбери одну из трёх.\n\nРеклама. ООО «Умка». erid: 2VtzqwXYZ12\nПодробнее: u.umka.chat/w3r8t",
    hashtags: ["#таро", "#расклад"],
    pinned: null,
    mode: "operator",
    ai: true,
    createdAgoMin: 180,
    downloaded: true,
    markedUploadedAgoMin: 25,
    done: ["file_downloaded", "right_account", "texts_pasted"],
  }),
  makePackage({
    id: 504,
    jobId: 1290,
    uid: "tr-0926-z9",
    kind: "generate",
    status: "published",
    channelId: "taro_main",
    code: "m8n3c",
    title: "Шут: начало пути",
    description: "Шут — карта новых начал.\n\nПодробнее: u.umka.chat/m8n3c (ссылка в шапке канала)",
    hashtags: ["#таро"],
    pinned: null,
    mode: "llm",
    ai: true,
    createdAgoMin: 26 * 60,
    downloaded: true,
    done: ["file_downloaded", "right_account", "texts_pasted", "hashtags", "ai_disclosure", "published"],
  }),
  makePackage({
    id: 505,
    jobId: 1291,
    uid: "as-0926-y3",
    kind: "generate",
    status: "published",
    channelId: "astro_main",
    code: "h2j5v",
    title: "Луна в Скорпионе: чего избегать",
    description: "Три вещи, которые лучше отложить.\n\nПодробнее: u.umka.chat/h2j5v",
    hashtags: ["#астрология"],
    pinned: null,
    mode: "llm",
    ai: true,
    createdAgoMin: 30 * 60,
    downloaded: true,
    done: ["file_downloaded", "right_account", "texts_pasted", "hashtags", "ai_disclosure", "published"],
  }),
];

// ---------------------------------------------------------------- публикации

interface MockPublication {
  pub: S["Publication"];
  packageId: number;
  deleted: boolean;
}

function makePublication(
  id: number,
  pkg: S["UploadPackage"],
  parsed: {
    platform: S["Platform"];
    videoId: string;
    kind: S["PlatformKind"];
    url: string;
    canonical: string;
    alias: string;
  },
  verify: S["VerifyStatus"],
  publishedAt: string,
): S["Publication"] {
  return {
    id,
    job_id: pkg.job_id,
    package_id: pkg.id,
    video_uid: pkg.video_uid,
    kind: pkg.kind,
    platform: parsed.platform,
    platform_video_id: parsed.videoId,
    platform_kind: parsed.kind,
    url: parsed.url,
    canonical_url: parsed.canonical,
    status: "live",
    status_reason: null,
    verify_status: verify,
    verify_detail:
      verify === "channel_mismatch"
        ? "oEmbed: автор ролика — другой канал (@someone_else), ожидался канал пакета"
        : null,
    verified_at: verify === "ok" || verify === "channel_mismatch" ? nowIso() : null,
    has_claim: false,
    channel: { id: pkg.channel.id, title: pkg.channel.display_name, platform: pkg.channel.platform },
    niche_id: pkg.channel.id.startsWith("astro") ? "astro" : "taro",
    product_id: "umka",
    campaign_id: 7,
    banner_variant: null,
    tracking: { codes: [pkg.tracking.code, parsed.alias] },
    latest: {
      captured_at: null,
      views: null,
      likes: null,
      comments: null,
      engaged_views: null,
      retention_3s: null,
      avg_pct_viewed: null,
    },
    age_views: { h24: null, h72: null, d7: null },
    verdict: null,
    traffic: {
      landing_views: 0,
      unique_visitors: 0,
      to_telegram: 0,
      to_site: 0,
      bot_starts: 0,
      signups: 0,
      first_payments: 0,
      revenue_minor: 0,
      currency: "RUB",
    },
    unit_economics: { job_cost_usd: 0.041, ctr_per_1k_views: null, cost_per_visitor_usd: null },
    originality_verdict: pkg.originality?.verdict ?? null,
    next_poll_at: iso(HOUR),
    published_at: publishedAt,
    created_at: nowIso(),
  };
}

const publications: MockPublication[] = [];
let nextPublicationId = 901;

function seedPublication(pkgId: number, videoId: string, verify: S["VerifyStatus"], agoMin: number) {
  const pkg = packages.find((item) => item.id === pkgId);
  if (!pkg) return;
  const id = nextPublicationId++;
  const url = `https://youtube.com/shorts/${videoId}`;
  const pub = makePublication(
    id,
    pkg,
    {
      platform: "youtube",
      videoId,
      kind: "short",
      url,
      canonical: `https://www.youtube.com/shorts/${videoId}`,
      alias: `yt_${videoId}`,
    },
    verify,
    iso(-agoMin * MIN),
  );
  pub.created_at = iso(-agoMin * MIN);
  publications.push({ pub, packageId: pkgId, deleted: false });
  pkg.publication = { id, url, status: "live", verify_status: verify, published_at: pub.published_at };
}

seedPublication(504, "Qm3hX9aK2pL", "ok", 25 * 60);
seedPublication(505, "Zx8vB1nM4cQ", "channel_mismatch", 29 * 60);

function syncPackageRef(entry: MockPublication) {
  const pkg = packages.find((item) => item.id === entry.packageId);
  if (!pkg) return;
  pkg.publication = entry.deleted
    ? null
    : {
        id: entry.pub.id,
        url: entry.pub.url,
        status: entry.pub.status,
        verify_status: entry.pub.verify_status,
        published_at: entry.pub.published_at,
      };
}

const idempotency = new Map<string, S["PublicationCreated"]>();

// ---------------------------------------------------------------- трекинг-ссылки

const trackingLinks: S["TrackingLink"][] = [];
let nextLinkId = 3001;

function makeLink(fields: Partial<S["TrackingLink"]> & { code: string; level: S["TrackingLevel"] }): S["TrackingLink"] {
  const product = fields.product_id ?? "umka";
  const tracking = trackingOf(fields.code, product);
  return {
    id: nextLinkId++,
    product_id: product,
    job_id: null,
    publication_id: null,
    channel_id: null,
    campaign_id: null,
    banner_variant_id: null,
    platform: null,
    label: null,
    target_override_url: null,
    is_active: true,
    redirect_url: tracking.redirect_url,
    telegram_url: tracking.telegram_url,
    telegram_https_url: tracking.telegram_https_url,
    tma_url: null,
    clicks_7d: 0,
    created_by: 1,
    created_at: iso(-3 * 24 * HOUR),
    updated_at: iso(-3 * 24 * HOUR),
    ...fields,
  };
}

trackingLinks.push(
  makeLink({
    code: "taro_sub",
    level: "channel",
    channel_id: "taro_main",
    platform: "youtube",
    label: "Кнопка «Подписаться»",
    clicks_7d: 184,
  }),
);

function channelCode(channelId: string): string {
  const base = channelId.replace(/[^A-Za-z0-9]/g, "").slice(0, 10) || "ch";
  let code = `${base}_c`;
  let n = 2;
  while (trackingLinks.some((link) => link.code.toLowerCase() === code.toLowerCase())) code = `${base}_c${n++}`;
  return code;
}

// ---------------------------------------------------------------- вебхук продукта

interface IntegrationState {
  secretSet: boolean;
  secret: string | null;
  rotatedAt: string | null;
  prevValidUntil: string | null;
  requests: S["IngestRequestLogItem"][];
}

const integrations = new Map<string, IntegrationState>();
let nextIngestId = 1;

function integrationState(productId: string): IntegrationState {
  let state = integrations.get(productId);
  if (!state) {
    state = { secretSet: false, secret: null, rotatedAt: null, prevValidUntil: null, requests: [] };
    integrations.set(productId, state);
  }
  return state;
}

function randomSecret(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

const EVENT_TYPES: S["ConversionEventType"][] = [
  "bot_start",
  "tma_open",
  "site_visit",
  "signup",
  "first_payment",
  "payment",
  "refund",
];

function integrationOf(productId: string): S["ProductIntegration"] {
  const state = integrationState(productId);
  const product = PRODUCT_HOSTS[productId] ?? PRODUCT_HOSTS.umka;
  const header = productId.toUpperCase().replace(/-/g, "_");
  return {
    product_id: productId,
    ingest_url: `https://${product.host}/ingest/v1/events`,
    headers: [
      { name: "Content-Type", value: "application/json", description: "Тело — JSON в UTF-8" },
      { name: "X-AS-Product", value: productId, description: "ID продукта" },
      { name: "X-AS-Timestamp", value: "<unix>", description: "Время отправки в секундах, окно ±300 с" },
      { name: "X-AS-Signature", value: "sha256=<hex>", description: "HMAC-SHA256 секрета по «ts.тело»" },
    ],
    signature_spec: 'HMAC_SHA256(secret, f"{ts}." + raw_body), hex, префикс sha256=',
    max_skew_sec: 300,
    max_events: 100,
    max_body_bytes: 256 * 1024,
    rate_limit_per_sec: 20,
    event_types: EVENT_TYPES,
    example_payload: {
      events: [
        {
          event_id: `${productId}-pay-99812`,
          type: "first_payment",
          occurred_at: "2026-09-26T10:00:00Z",
          tag: "k7m2q_l",
          user_ref: "tg:123456",
          amount_minor: 49000,
          currency: "RUB",
          meta: {},
        },
      ],
    },
    deeplink_examples: [
      {
        kind: "bot_start",
        url: `tg://resolve?domain=${product.bot ?? "bot"}&start=k7m2q`,
        description: "Код из start-параметра /start — шлите bot_start с tag=k7m2q",
      },
      {
        kind: "bot_start_landing",
        url: `tg://resolve?domain=${product.bot ?? "bot"}&start=k7m2q_l`,
        description: "Суффикс _l — пришёл с лендинга; шлите tag как есть, сервер снимет _l",
      },
      {
        kind: "site_utm",
        url: `https://${product.landing ?? "example.org/"}?utm_campaign=k7m2q`,
        description: "Сайт кладёт utm_campaign в localStorage и шлёт его с signup и first_payment",
      },
    ],
    emitter_snippets: {
      python: `import hashlib, hmac, json, os, time, urllib.request\n\nSECRET = os.environ["PRODUCT_WEBHOOK_${header}"]\n\ndef emit(events):\n    body = json.dumps({"events": events}).encode()\n    ts = str(int(time.time()))\n    sig = hmac.new(SECRET.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()\n    req = urllib.request.Request("https://${product.host}/ingest/v1/events", data=body, method="POST")\n    req.add_header("Content-Type", "application/json")\n    req.add_header("X-AS-Product", "${productId}")\n    req.add_header("X-AS-Timestamp", ts)\n    req.add_header("X-AS-Signature", "sha256=" + sig)\n    return urllib.request.urlopen(req, timeout=5).read()\n`,
      node: `import { createHmac } from "node:crypto";\n\nconst SECRET = process.env.PRODUCT_WEBHOOK_${header};\n\nexport async function emit(events) {\n  const body = JSON.stringify({ events });\n  const ts = String(Math.floor(Date.now() / 1000));\n  const sig = createHmac("sha256", SECRET).update(ts + "." + body).digest("hex");\n  const res = await fetch("https://${product.host}/ingest/v1/events", {\n    method: "POST",\n    headers: { "Content-Type": "application/json", "X-AS-Product": "${productId}", "X-AS-Timestamp": ts, "X-AS-Signature": "sha256=" + sig },\n    body,\n  });\n  return res.json();\n}\n`,
    },
    secret_set: state.secretSet,
    secret_rotated_at: state.rotatedAt,
    previous_secret_valid_until: state.prevValidUntil,
    last_event_at: null,
    recent_requests: state.requests.slice(0, 20),
  };
}

// ---------------------------------------------------------------- обработчики

function packageOr404(id: number) {
  return packages.find((item) => item.id === id);
}

function live(pkg: S["UploadPackage"]) {
  return pkg.status === "ready" || pkg.status === "downloaded";
}

function notLive(pkg: S["UploadPackage"]) {
  return error(409, "package_not_ready", "Пакет уже опубликован или заменён новым", {
    package_id: pkg.id,
    status: pkg.status,
    reason: "package_not_live",
  });
}

function tick(pkg: S["UploadPackage"], code: string, done = true) {
  pkg.checklist = pkg.checklist.map((item) =>
    item.code === code ? { ...item, done, done_at: done ? nowIso() : null, done_by: done ? 1 : null } : item,
  );
}

function markDownloaded(pkg: S["UploadPackage"]) {
  if (pkg.status === "ready") pkg.status = "downloaded";
  pkg.downloaded_at = pkg.downloaded_at ?? nowIso();
  tick(pkg, "file_downloaded");
  pkg.updated_at = nowIso();
}

function textFile(pkg: S["UploadPackage"]): string {
  return [
    `Пакет #${pkg.id} · ролик ${pkg.video_uid} · ${pkg.channel.display_name} (${pkg.channel.platform})`,
    `Аккаунт: ${pkg.channel.account_hint}`,
    "",
    "ЗАГОЛОВОК",
    pkg.texts.title,
    "",
    "ОПИСАНИЕ",
    pkg.texts.description,
    "",
    "ХЭШТЕГИ",
    pkg.texts.hashtags.join(" ") || "—",
    "",
    "ЗАКРЕПЛЁННЫЙ КОММЕНТАРИЙ",
    pkg.texts.pinned_comment ?? "—",
    "",
    `Код ролика: ${pkg.tracking.code}`,
    `Редирект: ${pkg.tracking.redirect_url}`,
  ].join("\n");
}

// ---------------------------------------------------------------- автозалив через YouTube API (0019)
// Повторяет services/autopublish.py: гейт (409 `autopublish_blocked` + details.problems), ближайший слот плана
// канала, «сейчас» → заливка 3 с → публикация зарегистрирована, снятие с очереди. Подключение канала и план
// берутся из мока handlers/autopublish.ts запросом `GET /channels/{id}/oauth/youtube`.
// Сиды: 502 (astro_main не подключён) — ошибка автозалива, 504 — залит автоматически.

const MSK_OFFSET_MS = 3 * HOUR;
const MIN_LEAD_MS = 10 * MIN;

function seedAutopublish(pkgId: number, value: S["PackageAutopublish"]) {
  const pkg = packages.find((item) => item.id === pkgId);
  if (pkg) pkg.autopublish = value;
}

seedAutopublish(502, {
  state: "failed",
  at: iso(-30 * MIN),
  privacy: "public",
  attempts: 1,
  error: "Канал не подключён к YouTube (карточка канала → «Подключить YouTube»)",
  video_id: null,
  video_url: null,
});
seedAutopublish(504, {
  state: "done",
  at: iso(-25 * HOUR),
  privacy: "public",
  attempts: 1,
  error: null,
  video_id: "Qm3hX9aK2pL",
  video_url: "https://www.youtube.com/shorts/Qm3hX9aK2pL",
});

async function connectionOf(channelId: string): Promise<S["YoutubeConnection"] | null> {
  const response = await fetch(`${API}/channels/${encodeURIComponent(channelId)}/oauth/youtube`, {
    headers: { Accept: "application/json" },
  });
  return response.ok ? ((await response.json()) as S["YoutubeConnection"]) : null;
}

function autopublishProblems(pkg: S["UploadPackage"], connection: S["YoutubeConnection"] | null) {
  const problems: Array<{ code: string; message: string }> = [];
  if (pkg.channel.platform !== "youtube")
    problems.push({ code: "platform_unsupported", message: "Автозалив пока только для YouTube" });
  if (!live(pkg)) problems.push({ code: "package_not_live", message: "Пакет уже опубликован или заменён" });
  if (pkg.marked_uploaded_at)
    problems.push({ code: "marked_uploaded", message: "Оператор отметил, что залил этот ролик вручную" });
  if (!connection?.can_upload)
    problems.push({
      code: "channel_not_connected",
      message: "Канал не подключён к YouTube (карточка канала → «Подключить YouTube»)",
    });
  return problems;
}

/** Ближайший свободный слот плана (мок считает пояс МСК): не раньше чем через 10 минут, с лимитом в день. */
function nextSlotAt(pkg: S["UploadPackage"], plan: S["YoutubeConnection"]): string | null {
  const now = Date.now();
  const taken = packages
    .filter((item) => item.id !== pkg.id && item.channel.id === pkg.channel.id && item.autopublish?.at)
    .filter((item) => item.autopublish?.state === "scheduled" || item.autopublish?.state === "uploading")
    .map((item) => Date.parse(item.autopublish?.at ?? ""));
  const mskDay = (ms: number) => new Date(ms + MSK_OFFSET_MS).toISOString().slice(0, 10);
  const today = Date.parse(`${mskDay(now)}T00:00:00+03:00`);
  for (let day = 0; day < 60; day += 1) {
    const dayStart = today + day * 24 * HOUR;
    const perDay = taken.filter((t) => mskDay(t) === mskDay(dayStart)).length;
    if (plan.videos_per_day !== null && perDay >= plan.videos_per_day) continue;
    for (const slot of [...plan.slots].sort()) {
      const [hh, mm] = slot.split(":").map(Number);
      const at = dayStart + hh * HOUR + mm * MIN;
      if (at < now + MIN_LEAD_MS || taken.some((t) => Math.abs(t - at) < MIN)) continue;
      return new Date(at).toISOString();
    }
  }
  return null;
}

function randomVideoId(): string {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-";
  let id = "";
  for (let i = 0; i < 11; i += 1) id += alphabet[Math.floor(Math.random() * alphabet.length)];
  return id;
}

/** «Сейчас»: 3 с заливки, затем ролик залит и публикация зарегистрирована от имени системы. */
function finishUploadLater(pkg: S["UploadPackage"]) {
  setTimeout(() => {
    if (pkg.autopublish?.state !== "uploading") return;
    const videoId = randomVideoId();
    const url = `https://www.youtube.com/shorts/${videoId}`;
    const id = nextPublicationId++;
    const pub = makePublication(
      id,
      pkg,
      { platform: "youtube", videoId, kind: "short", url, canonical: url, alias: `yt_${videoId}` },
      "ok",
      nowIso(),
    );
    const entry: MockPublication = { pub, packageId: pkg.id, deleted: false };
    publications.push(entry);
    pkg.status = "published";
    tick(pkg, "published");
    syncPackageRef(entry);
    pkg.autopublish = {
      ...pkg.autopublish,
      state: "done",
      at: nowIso(),
      error: null,
      video_id: videoId,
      video_url: url,
    };
    pkg.updated_at = nowIso();
  }, 3000);
}

const AUTOPUBLISH_ORDER: Record<S["AutopublishState"], number> = {
  uploading: 0,
  failed: 1,
  scheduled: 2,
  done: 3,
  cancelled: 3,
};

export const packagesHandlers = [
  // ---------- пакеты ----------
  http.get(`${API}/packages`, ({ request }) => {
    const g = guard(request);
    if (g instanceof Response) return g;
    const url = new URL(request.url);
    const statuses = url.searchParams.getAll("status");
    const channel = url.searchParams.get("channel_id");
    const platform = url.searchParams.get("platform");
    const limit = Math.min(200, Number(url.searchParams.get("limit") ?? 50));
    const offset = Number(url.searchParams.get("offset") ?? 0);
    const filtered = packages.filter(
      (pkg) =>
        (!statuses.length || statuses.includes(pkg.status)) &&
        (!channel || pkg.channel.id === channel) &&
        (!platform || pkg.channel.platform === platform),
    );
    return HttpResponse.json({
      items: filtered.slice(offset, offset + limit),
      total: filtered.length,
      limit,
      offset,
    } satisfies S["UploadPackagePage"]);
  }),
  http.get(`${API}/packages/:packageId`, ({ request, params }) => {
    const g = guard(request);
    if (g instanceof Response) return g;
    const pkg = packageOr404(Number(params.packageId));
    return pkg ? HttpResponse.json(pkg) : error(404, "package_not_found", "Пакет не найден");
  }),
  http.patch(`${API}/packages/:packageId`, async ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const pkg = packageOr404(Number(params.packageId));
    if (!pkg) return error(404, "package_not_found", "Пакет не найден");
    if (!live(pkg)) return notLive(pkg);
    const body = (await request.json()) as S["PackagePatch"];
    if (body.title !== undefined && body.title !== null) {
      const title = body.title.trim();
      if (!title) return invalid("title", "Пустой заголовок", "string_too_short");
      if ([...title].length > 100) return invalid("title", "Заголовок длиннее 100 символов", "string_too_long");
      pkg.texts.title = title;
    }
    if (body.description !== undefined && body.description !== null) {
      if ([...body.description].length > 5000)
        return invalid("description", "Описание слишком длинное", "string_too_long");
      const attribution = (pkg.texts.attribution_text ?? "").trim();
      if (attribution && !body.description.includes(attribution)) {
        return invalid(
          "description",
          "Атрибуция источника обязана остаться в описании (чужие права, CC BY)",
          "attribution_required",
        );
      }
      pkg.texts.description = body.description;
    }
    if (body.hashtags !== undefined && body.hashtags !== null) {
      const bad = body.hashtags.filter((tag) => !HASHTAG_RE.test(tag));
      if (bad.length) return invalid("hashtags", `Хэштег — «#слово» без пробелов: ${bad.join(" ")}`, "hashtag_format");
      if (body.hashtags.length > 15) return invalid("hashtags", "Не больше 15 хэштегов", "too_long");
      pkg.texts.hashtags = [...new Set(body.hashtags)];
    }
    if (body.pinned_comment !== undefined) pkg.texts.pinned_comment = body.pinned_comment || null;
    pkg.texts.texts_mode = "operator";
    pkg.texts.texts_label = TEXT_LABELS.operator;
    pkg.updated_at = nowIso();
    return HttpResponse.json(pkg);
  }),
  http.patch(`${API}/packages/:packageId/checklist`, async ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const pkg = packageOr404(Number(params.packageId));
    if (!pkg) return error(404, "package_not_found", "Пакет не найден");
    if (pkg.status === "superseded") return notLive(pkg);
    const body = (await request.json()) as S["ChecklistPatch"];
    if (!pkg.checklist.some((item) => item.code === body.code)) {
      return invalid("code", "Такого пункта нет в чек-листе этого пакета", "checklist_code_absent");
    }
    tick(pkg, body.code, body.done);
    pkg.updated_at = nowIso();
    return HttpResponse.json(pkg);
  }),
  http.post(`${API}/packages/:packageId/mark-uploaded`, ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const pkg = packageOr404(Number(params.packageId));
    if (!pkg) return error(404, "package_not_found", "Пакет не найден");
    if (!live(pkg)) return notLive(pkg);
    pkg.status = "downloaded";
    pkg.marked_uploaded_at = nowIso();
    pkg.downloaded_at = pkg.downloaded_at ?? nowIso();
    pkg.updated_at = nowIso();
    return HttpResponse.json(pkg);
  }),
  http.get(`${API}/packages/:packageId/video`, ({ request, params }) => {
    const g = guard(request);
    if (g instanceof Response) return g;
    const pkg = packageOr404(Number(params.packageId));
    if (!pkg) return error(404, "package_not_found", "Пакет не найден");
    markDownloaded(pkg);
    // Настоящего видео в моках нет: пустой mp4-контейнер ради заголовков и Web Share.
    return new HttpResponse(new Uint8Array([0, 0, 0, 24, 102, 116, 121, 112, 109, 112, 52, 50]), {
      headers: {
        "Content-Type": "video/mp4",
        "Content-Disposition": `attachment; filename="${pkg.file.filename}"`,
      },
    });
  }),
  http.get(`${API}/packages/:packageId/text`, ({ request, params }) => {
    const g = guard(request);
    if (g instanceof Response) return g;
    const pkg = packageOr404(Number(params.packageId));
    if (!pkg) return error(404, "package_not_found", "Пакет не найден");
    return new HttpResponse(textFile(pkg), {
      headers: {
        "Content-Type": "text/plain; charset=utf-8",
        "Content-Disposition": `attachment; filename="${pkg.file.filename.replace(/\.mp4$/, ".txt")}"`,
      },
    });
  }),
  http.get(`${API}/packages/:packageId/zip`, ({ request, params }) => {
    const g = guard(request);
    if (g instanceof Response) return g;
    const pkg = packageOr404(Number(params.packageId));
    if (!pkg) return error(404, "package_not_found", "Пакет не найден");
    markDownloaded(pkg);
    return new HttpResponse(new Uint8Array([80, 75, 5, 6, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]), {
      headers: {
        "Content-Type": "application/zip",
        "Content-Disposition": `attachment; filename="${pkg.file.filename.replace(/\.mp4$/, ".zip")}"`,
      },
    });
  }),
  http.get(`${API}/jobs/:jobId/package`, ({ request, params }) => {
    const g = guard(request);
    if (g instanceof Response) return g;
    const pkg = [...packages].reverse().find((item) => item.job_id === Number(params.jobId));
    return pkg ? HttpResponse.json(pkg) : error(404, "package_not_found", "У ролика ещё нет пакета");
  }),
  http.post(`${API}/jobs/:jobId/package`, async ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const pkg = [...packages].reverse().find((item) => item.job_id === Number(params.jobId));
    if (!pkg) {
      return error(409, "package_not_ready", "Пакет собирается только для одобренного ролика", {
        reason: "job_not_approved",
        job_id: Number(params.jobId),
      });
    }
    if (!live(pkg)) return notLive(pkg);
    const body = (await request.json().catch(() => ({}))) as Partial<S["PackageRebuild"]>;
    if (body.regenerate_texts) {
      pkg.texts.texts_mode = "template";
      pkg.texts.texts_label = TEXT_LABELS.template;
      pkg.texts.title = `${pkg.texts.title.replace(/ \(шаблон\)$/, "")} (шаблон)`.slice(0, 100);
    }
    pkg.updated_at = nowIso();
    return HttpResponse.json(pkg);
  }),

  // ---------- публикации ----------
  http.post(`${API}/publications/parse-url`, async ({ request }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const body = (await request.json()) as S["ParseUrlRequest"];
    const parsed = parseVideoUrl(body.url);
    if (!parsed.ok) return error(422, "unsupported_url", parsed.message, { reason: parsed.reason });
    return HttpResponse.json(parsed.value satisfies S["ParsedUrl"]);
  }),
  http.post(`${API}/publications`, async ({ request }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const key = request.headers.get("Idempotency-Key");
    const cacheKey = key ? `${g.userId}:${key}` : null;
    if (cacheKey && idempotency.has(cacheKey)) return HttpResponse.json(idempotency.get(cacheKey), { status: 201 });
    const body = (await request.json()) as S["PublicationCreate"];
    const pkg = packageOr404(body.package_id);
    if (!pkg) return error(404, "package_not_found", "Пакет не найден");
    const parsed = parseVideoUrl(body.url);
    if (!parsed.ok) return error(422, "unsupported_url", parsed.message, { reason: parsed.reason });
    const value = parsed.value;
    // Порядок проверок — как в services/publications.register.
    if (value.platform !== pkg.channel.platform) {
      return error(422, "platform_mismatch", `Ссылка на ${value.platform}, а канал пакета — ${pkg.channel.platform}`, {
        url_platform: value.platform,
        channel_platform: pkg.channel.platform,
        channel_id: pkg.channel.id,
      });
    }
    const existing = publications.find(
      (entry) =>
        !entry.deleted &&
        entry.pub.platform === value.platform &&
        entry.pub.platform_video_id === value.platform_video_id,
    );
    if (existing) {
      return error(409, "publication_exists", "Этот ролик площадки уже зарегистрирован", {
        publication_id: existing.pub.id,
        job_id: existing.pub.job_id,
      });
    }
    if (pkg.status === "superseded") {
      return error(409, "package_not_ready", "Пакет заменён более новым — регистрируйте по текущему пакету", {
        reason: "package_superseded",
        package_id: pkg.id,
      });
    }
    if (pkg.status === "published") {
      return error(409, "publication_exists", "Ролик уже зарегистрирован на этой площадке", {
        publication_id: pkg.publication?.id ?? null,
        job_id: pkg.job_id,
      });
    }
    const verify: S["VerifyStatus"] =
      value.platform === "vk"
        ? value.owner_ref === pkg.channel.platform_channel_ref
          ? "ok"
          : "channel_mismatch"
        : "pending";
    const id = nextPublicationId++;
    const pub = makePublication(
      id,
      pkg,
      {
        platform: value.platform,
        videoId: value.platform_video_id,
        kind: value.platform_kind,
        url: body.url.trim(),
        canonical: value.canonical_url,
        alias: value.alias_code,
      },
      verify,
      body.published_at ?? nowIso(),
    );
    const entry: MockPublication = { pub, packageId: pkg.id, deleted: false };
    publications.push(entry);
    pkg.status = "published";
    tick(pkg, "published");
    pkg.updated_at = nowIso();
    syncPackageRef(entry);
    if (verify === "pending") {
      setTimeout(() => {
        entry.pub.verify_status = "ok";
        entry.pub.verified_at = nowIso();
        syncPackageRef(entry);
      }, 3000);
    }
    const packageLink = makeLink({
      code: pkg.tracking.code,
      level: "package",
      job_id: pkg.job_id,
      publication_id: id,
      channel_id: pkg.channel.id,
      platform: pkg.channel.platform,
    });
    const aliasLink = makeLink({
      code: value.alias_code,
      level: "publication",
      job_id: pkg.job_id,
      publication_id: id,
      channel_id: pkg.channel.id,
      platform: value.platform,
    });
    trackingLinks.push(packageLink, aliasLink);
    const created: S["PublicationCreated"] = { publication: pub, tracking_links: [packageLink, aliasLink] };
    if (cacheKey) idempotency.set(cacheKey, created);
    return HttpResponse.json(created, { status: 201 });
  }),
  http.get(`${API}/publications`, ({ request }) => {
    const g = guard(request);
    if (g instanceof Response) return g;
    const url = new URL(request.url);
    const verify = url.searchParams.getAll("verify_status");
    const statuses = url.searchParams.getAll("status");
    const channel = url.searchParams.get("channel_id");
    const limit = Math.min(200, Number(url.searchParams.get("limit") ?? 50));
    const offset = Number(url.searchParams.get("offset") ?? 0);
    const items = publications
      .filter((entry) => !entry.deleted)
      .map((entry) => entry.pub)
      .filter(
        (pub) =>
          (!verify.length || verify.includes(pub.verify_status)) &&
          (!statuses.length || statuses.includes(pub.status)) &&
          (!channel || pub.channel.id === channel),
      )
      .sort((a, b) => (b.published_at ?? "").localeCompare(a.published_at ?? ""));
    return HttpResponse.json({
      items: items.slice(offset, offset + limit),
      total: items.length,
      limit,
      offset,
    } satisfies S["PublicationPage"]);
  }),
  http.get(`${API}/publications/:publicationId`, ({ request, params }) => {
    const g = guard(request);
    if (g instanceof Response) return g;
    const entry = publications.find((item) => item.pub.id === Number(params.publicationId) && !item.deleted);
    return entry ? HttpResponse.json(entry.pub) : error(404, "publication_not_found", "Публикация не найдена");
  }),
  http.post(`${API}/publications/:publicationId/reverify`, ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const entry = publications.find((item) => item.pub.id === Number(params.publicationId) && !item.deleted);
    if (!entry) return error(404, "publication_not_found", "Публикация не найдена");
    entry.pub.verify_status = "pending";
    syncPackageRef(entry);
    setTimeout(() => {
      entry.pub.verify_status = entry.pub.verify_detail ? "channel_mismatch" : "ok";
      entry.pub.verified_at = nowIso();
      syncPackageRef(entry);
    }, 2000);
    return HttpResponse.json({ task_id: 7000 + entry.pub.id } satisfies S["TaskAccepted"], { status: 202 });
  }),
  http.delete(`${API}/publications/:publicationId`, ({ request, params }) => {
    const g = guard(request, ["admin"]);
    if (g instanceof Response) return g;
    const entry = publications.find((item) => item.pub.id === Number(params.publicationId) && !item.deleted);
    if (!entry) return error(404, "publication_not_found", "Публикация не найдена");
    entry.deleted = true;
    const pkg = packageOr404(entry.packageId);
    if (pkg) {
      pkg.status = "ready";
      tick(pkg, "published", false);
      pkg.updated_at = nowIso();
    }
    for (const link of trackingLinks) {
      if (link.level === "publication" && link.publication_id === entry.pub.id) link.is_active = false;
    }
    syncPackageRef(entry);
    return new HttpResponse(null, { status: 204 });
  }),

  // ---------- трекинг-ссылки ----------
  http.get(`${API}/tracking-links`, ({ request }) => {
    const g = guard(request);
    if (g instanceof Response) return g;
    const url = new URL(request.url);
    const levels = url.searchParams.getAll("level");
    const channel = url.searchParams.get("channel_id");
    const product = url.searchParams.get("product_id");
    const active = url.searchParams.get("is_active");
    const items = trackingLinks.filter(
      (link) =>
        (!levels.length || levels.includes(link.level)) &&
        (!channel || link.channel_id === channel) &&
        (!product || link.product_id === product) &&
        (active === null || String(link.is_active) === active),
    );
    return HttpResponse.json({ items, total: items.length, limit: 200, offset: 0 } satisfies S["TrackingLinkPage"]);
  }),
  http.post(`${API}/channels/:channelId/tracking-link`, async ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const channelId = String(params.channelId);
    const channel = CHANNELS.find((item) => item.id === channelId);
    const body = (await request.json().catch(() => ({}))) as S["ChannelTrackingLinkCreate"];
    const productId = body.product_id || channel?.product || "umka";
    const existing = trackingLinks.find(
      (link) =>
        link.level === "channel" && link.channel_id === channelId && link.product_id === productId && link.is_active,
    );
    if (existing) return HttpResponse.json(existing);
    const link = makeLink({
      code: channelCode(channelId),
      level: "channel",
      channel_id: channelId,
      product_id: productId,
      platform: channel?.platform ?? null,
      label: body.label ?? null,
      created_at: nowIso(),
      updated_at: nowIso(),
    });
    trackingLinks.push(link);
    return HttpResponse.json(link);
  }),
  http.patch(`${API}/tracking-links/:linkId`, async ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const link = trackingLinks.find((item) => item.id === Number(params.linkId));
    if (!link) return error(404, "tracking_link_not_found", "Ссылка не найдена");
    const body = (await request.json()) as S["TrackingLinkPatch"];
    if (body.label !== undefined) link.label = body.label;
    if (body.is_active !== undefined && body.is_active !== null) link.is_active = body.is_active;
    if (body.target_override_url !== undefined) link.target_override_url = body.target_override_url;
    link.updated_at = nowIso();
    return HttpResponse.json(link);
  }),

  // ---------- вебхук продукта ----------
  http.get(`${API}/products/:productId/integration`, ({ request, params }) => {
    const g = guard(request);
    if (g instanceof Response) return g;
    return HttpResponse.json(integrationOf(String(params.productId)));
  }),
  http.post(`${API}/products/:productId/webhook-secret/rotate`, async ({ request, params }) => {
    const g = guard(request, ["admin"]);
    if (g instanceof Response) return g;
    const productId = String(params.productId);
    const body = (await request.json().catch(() => ({}))) as Partial<S["WebhookSecretRotate"]>;
    const grace = body.grace_hours ?? 24;
    const state = integrationState(productId);
    state.prevValidUntil = state.secretSet && grace > 0 ? new Date(Date.now() + grace * HOUR).toISOString() : null;
    state.secret = randomSecret();
    state.secretSet = true;
    state.rotatedAt = nowIso();
    return HttpResponse.json({
      product_id: productId,
      secret: state.secret,
      secret_name: `PRODUCT_WEBHOOK_${productId.toUpperCase().replace(/-/g, "_")}`,
      rotated_at: state.rotatedAt,
      previous_valid_until: state.prevValidUntil,
    } satisfies S["WebhookSecretRotated"]);
  }),
  http.post(`${API}/products/:productId/test-conversion`, async ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const productId = String(params.productId);
    const body = (await request.json().catch(() => ({}))) as Partial<S["ConversionTestRequest"]>;
    const state = integrationState(productId);
    const eventId = `test-${Date.now().toString(36)}`;
    const ok = state.secretSet;
    const knownTag = !body.tag || trackingLinks.some((link) => link.code === body.tag?.replace(/_l$/, ""));
    const status: S["IngestEventStatus"] = knownTag ? "accepted" : "unknown_tag";
    state.requests.unshift({
      id: nextIngestId++,
      received_at: nowIso(),
      outcome: ok ? "accepted" : "invalid_signature",
      http_status: ok ? 200 : 401,
      events_total: ok ? 1 : 0,
      accepted: ok && knownTag ? 1 : 0,
      duplicates: 0,
      unknown_tag: ok && !knownTag ? 1 : 0,
      invalid: 0,
      is_test: true,
      detail: ok ? `${body.event_type ?? "bot_start"} · тест` : "секрет не задан",
    });
    return HttpResponse.json({
      ok,
      http_status: ok ? 200 : 401,
      event_id: eventId,
      error_code: ok ? null : "invalid_signature",
      response: ok
        ? {
            accepted: knownTag ? 1 : 0,
            duplicates: 0,
            unknown_tag: knownTag ? 0 : 1,
            invalid: 0,
            results: [{ event_id: eventId, status }],
          }
        : null,
      request_id: `req-${Date.now().toString(36)}`,
    } satisfies S["ConversionTestResult"]);
  }),
  // ---------- автозалив (0019) ----------
  http.post(`${API}/packages/:packageId/autopublish`, async ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const pkg = packageOr404(Number(params.packageId));
    if (!pkg) return error(404, "package_not_found", "Пакет не найден");
    const body = (await request.json().catch(() => ({}))) as Partial<S["AutopublishRequest"]>;
    const state = pkg.autopublish?.state;
    if (state === "uploading") return error(409, "autopublish_in_progress", "Ролик уже заливается");
    if (state === "done") return error(409, "autopublish_done", "Ролик уже залит автоматически");
    const connection = await connectionOf(pkg.channel.id);
    const problems = autopublishProblems(pkg, connection);
    if (problems.length || !connection) {
      return error(409, "autopublish_blocked", `Автозалив невозможен: ${problems.map((p) => p.message).join("; ")}`, {
        problems,
        package_id: pkg.id,
      });
    }
    let at: string | null;
    if (body.now) at = nowIso();
    else if (body.at) at = new Date(Math.max(Date.parse(body.at), Date.now())).toISOString();
    else at = nextSlotAt(pkg, connection);
    if (!at) {
      return error(
        409,
        "autopublish_no_slot",
        "Нет свободного слота на 60 дней вперёд: увеличьте videos_per_day или добавьте слоты",
      );
    }
    pkg.autopublish = {
      state: body.now ? "uploading" : "scheduled",
      at,
      privacy: body.privacy ?? connection.privacy,
      attempts: body.now ? 1 : 0,
      error: null,
      video_id: null,
      video_url: null,
    };
    pkg.updated_at = nowIso();
    if (body.now) finishUploadLater(pkg);
    return HttpResponse.json(pkg);
  }),
  http.delete(`${API}/packages/:packageId/autopublish`, ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const pkg = packageOr404(Number(params.packageId));
    if (!pkg) return error(404, "package_not_found", "Пакет не найден");
    const state = pkg.autopublish?.state;
    if (state === "uploading") return error(409, "autopublish_in_progress", "Ролик уже заливается — отменить нельзя");
    if (!pkg.autopublish || (state !== "scheduled" && state !== "failed")) {
      return error(409, "autopublish_not_scheduled", "Пакет не стоит в очереди автозалива");
    }
    pkg.autopublish = { ...pkg.autopublish, state: "cancelled" };
    pkg.updated_at = nowIso();
    return HttpResponse.json(pkg);
  }),
  http.get(`${API}/autopublish/queue`, ({ request }) => {
    const g = guard(request);
    if (g instanceof Response) return g;
    const url = new URL(request.url);
    const channel = url.searchParams.get("channel_id");
    const states = url.searchParams.getAll("state");
    const limit = Math.min(500, Number(url.searchParams.get("limit") ?? 100));
    const items = packages
      .filter((pkg) => pkg.autopublish)
      .filter(
        (pkg) =>
          (!channel || pkg.channel.id === channel) && (!states.length || states.includes(pkg.autopublish?.state ?? "")),
      )
      .sort((a, b) => {
        const sa = a.autopublish?.state ?? "cancelled";
        const sb = b.autopublish?.state ?? "cancelled";
        const active = (s: string) => s === "scheduled" || s === "uploading";
        const at = (p: S["UploadPackage"]) => Date.parse(p.autopublish?.at ?? "") || 0;
        return (
          AUTOPUBLISH_ORDER[sa] - AUTOPUBLISH_ORDER[sb] ||
          (active(sa) && active(sb) ? at(a) - at(b) : at(b) - at(a)) ||
          b.id - a.id
        );
      })
      .slice(0, limit)
      .map((pkg): S["AutopublishQueueItem"] => ({
        package_id: pkg.id,
        job_id: pkg.job_id,
        video_uid: pkg.video_uid,
        tracking_code: pkg.tracking.code,
        channel_id: pkg.channel.id,
        channel_title: pkg.channel.display_name,
        title: pkg.texts.title,
        state: pkg.autopublish?.state ?? "cancelled",
        at: pkg.autopublish?.at ?? null,
        privacy: pkg.autopublish?.privacy ?? null,
        attempts: pkg.autopublish?.attempts ?? 0,
        error: pkg.autopublish?.error ?? null,
        video_id: pkg.autopublish?.video_id ?? null,
        video_url: pkg.autopublish?.video_url ?? null,
      }));
    return HttpResponse.json({ items, generated_at: nowIso() } satisfies S["AutopublishQueue"]);
  }),
];
