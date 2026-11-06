/**
 * MSW-моки каталога M2 (поток M2-D2) по типам контракта `lib/api/schema.d.ts`:
 * продукты, кампании и варианты баннера, креативы (загрузка multipart), safe zones, шрифты,
 * проверка и «Кадр из рендера», шаблоны, каналы, ниши, кусты, правка провайдера.
 *
 * Поведение повторяет services/{products,campaigns,banner_creatives,banners,banner_preview,channels}.py
 * в том, что важно экранам:
 * - загрузка: 413 / 415 / 422 `creative_rejected` с `details.asset_error`; дедупликация по sha256;
 *   анимация (GIF/APNG/WebM/MP4) — `processing` 3 с, затем `ready`;
 * - вариант из креатива — слои по умолчанию (как `default_banner_variant`), статус draft;
 *   PATCH с `expected_version` → 409 `version_conflict`; включение с ошибками проверки → 422 + `details.warnings`;
 * - проверка — те же чистые правила, что у холста (`lib/safe-zones.ts`): нижняя мёртвая зона, safe zone,
 *   верхняя панель, край кадра, маркировка без erid, запасная плашка, зона субтитров;
 * - «Кадр из рендера» — PNG, собранный на OffscreenCanvas, заголовок `X-Preview-T-Sec`.
 *
 * Подключение: `handlers.ts` разворачивает `catalogHandlers` **до** `productionHandlers` и общего 501.
 * Каналы 7/21/22 и ниши taro/astro совпадают с моками Конвейера (M2-D1).
 */
import { http, HttpResponse } from "msw";

import {
  DEFAULT_TIMING,
  makeCodeLayer,
  makeCreativeLayer,
  makeEridLayer,
  makeFallbackPlateLayer,
  makeMicroBrandLayer,
} from "@/components/banner-editor/draft";
import type { components } from "@/lib/api/schema";
import {
  CANVAS_H,
  CANVAS_W,
  computeWindows,
  isVisibleAt,
  placeVariant,
  rectToBox,
  safeZoneOverflow,
  type Box,
  type CreativeSize,
} from "@/lib/safe-zones";

import { providers } from "../catalog-m1";
import { readSession, users } from "../db";

type S = components["schemas"];
type Role = S["Role"];

const API = "/api/v1";
const START = Date.now();
const DAY = 86_400_000;
const iso = (offsetMs: number) => new Date(START + offsetMs).toISOString();
const nowIso = () => new Date().toISOString();

// ---------------------------------------------------------------- доступ и ошибки

function error(status: number, code: string, message: string, details: Record<string, unknown> = {}) {
  return HttpResponse.json({ error: { code, message, details } } satisfies S["ErrorResponse"], { status });
}

function invalid(loc: Array<string | number>, msg: string, type = "value_error", extra: Record<string, unknown> = {}) {
  return error(422, "validation_error", msg, { errors: [{ loc: ["body", ...loc], msg, type }], ...extra });
}

const OPERATORS: Role[] = ["admin", "operator"];

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

function page<T>(items: T[], url: URL) {
  const limit = Math.min(200, Number(url.searchParams.get("limit") ?? 50));
  const offset = Number(url.searchParams.get("offset") ?? 0);
  return { items: items.slice(offset, offset + limit), total: items.length, limit, offset };
}

// ---------------------------------------------------------------- safe zones (config/safe_zones.json)

type ProfileSeed = Omit<S["BannerSafeZoneProfile"], "dead_zones" | "canvas">;

const PROFILE_SEEDS: ProfileSeed[] = [
  {
    id: "common_organic",
    label: "Общий органический (пересечение замеров)",
    status: "community",
    safe: { x1: 65, y1: 192, x2: 895, y2: 1380 },
    margins: { top: 192, bottom: 540, left: 65, right: 185 },
    offer_area: { x1: 65, y1: 1120, x2: 890, y2: 1370 },
    offer_max_height_px: 250,
    micro_brand_area: { x1: 65, y1: 195, x2: 895, y2: 260 },
    platforms: ["youtube", "vk", "instagram", "tiktok"],
    sources: [
      { url: null, note: "shorts-kit и reelsmith/safezone: пересечение замеров", status: "community", measured: null },
    ],
    notes: "research 2026-09-26 §7.1: официальных органических зон нет, проверять скриншотами",
  },
  {
    id: "youtube_shorts",
    label: "YouTube Shorts",
    status: "community",
    safe: { x1: 65, y1: 180, x2: 896, y2: 1382 },
    margins: { top: 180, bottom: 538, left: 65, right: 184 },
    offer_area: { x1: 65, y1: 1120, x2: 890, y2: 1370 },
    offer_max_height_px: 250,
    micro_brand_area: { x1: 65, y1: 195, x2: 896, y2: 260 },
    platforms: ["youtube"],
    sources: [
      { url: null, note: "консервативное пересечение двух замеров сообщества", status: "community", measured: null },
    ],
    notes: "консервативное пересечение двух замеров сообщества",
  },
  {
    id: "vk_clips",
    label: "VK Клипы",
    status: "unconfirmed",
    safe: { x1: 0, y1: 192, x2: 1080, y2: 1536 },
    margins: { top: 192, bottom: 384, left: null, right: null },
    offer_area: { x1: 65, y1: 1120, x2: 890, y2: 1370 },
    offer_max_height_px: 250,
    micro_brand_area: { x1: 65, y1: 195, x2: 895, y2: 260 },
    platforms: ["vk"],
    sources: [
      { url: null, note: "ТЗ §6: верхние 10% и нижние 20% заняты интерфейсом", status: "unconfirmed", measured: null },
    ],
    notes: "не подтверждено; по горизонтали проверяйте ещё и по common_organic",
  },
];

function deadZones(safe: S["Rect"]): S["DeadZone"][] {
  const zones: S["DeadZone"][] = [];
  if (safe.y1 > 0) zones.push({ id: "top_bar", x1: 0, y1: 0, x2: CANVAS_W, y2: safe.y1 });
  if (safe.y2 < CANVAS_H) zones.push({ id: "bottom_ui", x1: 0, y1: safe.y2, x2: CANVAS_W, y2: CANVAS_H });
  if (safe.x1 > 0) zones.push({ id: "left_margin", x1: 0, y1: safe.y1, x2: safe.x1, y2: safe.y2 });
  if (safe.x2 < CANVAS_W) zones.push({ id: "right_rail", x1: safe.x2, y1: safe.y1, x2: CANVAS_W, y2: safe.y2 });
  return zones;
}

const SAFE_ZONES: S["BannerSafeZoneCatalog"] = {
  version: 1,
  canvas: { w: CANVAS_W, h: CANVAS_H },
  default_profile: "common_organic",
  platform_profiles: [
    { platform: "youtube", profile_id: "youtube_shorts" },
    { platform: "vk", profile_id: "vk_clips" },
  ],
  items: PROFILE_SEEDS.map((seed) => ({
    ...seed,
    canvas: { w: CANVAS_W, h: CANVAS_H },
    dead_zones: deadZones(seed.safe),
  })),
};

const profileById = (id: string) => SAFE_ZONES.items.find((item) => item.id === id);

// ---------------------------------------------------------------- картинки (OffscreenCanvas)

async function drawPng(
  width: number,
  height: number,
  draw: (ctx: OffscreenCanvasRenderingContext2D) => void | Promise<void>,
) {
  const canvas = new OffscreenCanvas(width, height);
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("no 2d context");
  await draw(ctx);
  return canvas.convertToBlob({ type: "image/png" });
}

async function demoCreativeBlob(label: string, color: string): Promise<Blob> {
  return drawPng(800, 200, (ctx) => {
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.roundRect(0, 0, 800, 200, 28);
    ctx.fill();
    ctx.fillStyle = "#ffffff";
    ctx.font = "bold 64px sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(label, 400, 100);
  });
}

// ---------------------------------------------------------------- креативы

type CreativeRecord = S["BannerCreative"] & { deleted: boolean };

const creativeBlobs = new Map<string, Blob>();
const firstFrames = new Map<string, Blob>();

function creativeRecord(
  sha: string,
  name: string,
  opts: Partial<S["BannerCreative"]> & Pick<S["BannerCreative"], "width" | "height">,
): CreativeRecord {
  const base = `${API}/banner-creatives/${sha}/media`;
  return {
    sha256: sha,
    name,
    status: "ready",
    error_code: null,
    error_message: null,
    kind: "static",
    source_format: "png",
    source_bytes: 48_213,
    source_width: opts.width,
    source_height: opts.height,
    has_alpha: true,
    duration_sec: 0,
    fps: null,
    frame_count: 1,
    loop: null,
    normalized_mime: "image/png",
    normalized_bytes: 45_870,
    file_url: `${base}/file`,
    thumb_url: `${base}/thumb`,
    first_frame_url: `${base}/first_frame`,
    product_id: "umka",
    notes: null,
    used_by_variants: 0,
    task_id: null,
    created_by: 1,
    created_at: iso(-5 * DAY),
    updated_at: iso(-5 * DAY),
    deleted: false,
    ...opts,
  };
}

const DEMO_SHA_A = "a1".repeat(32);
const DEMO_SHA_B = "b2".repeat(32);
const DEMO_BLOBS: Record<string, [string, string]> = {
  [DEMO_SHA_A]: ["umka.chat · все нейросети", "#6d4dff"],
  [DEMO_SHA_B]: ["Карта дня → umka.chat", "#d9573a"],
};

const creatives: CreativeRecord[] = [
  creativeRecord(DEMO_SHA_A, "umka-autumn-800x200.png", { width: 800, height: 200 }),
  creativeRecord(DEMO_SHA_B, "karta-dnya.png", {
    width: 800,
    height: 200,
    created_at: iso(-3 * DAY),
    updated_at: iso(-3 * DAY),
  }),
];

async function blobFor(sha: string, kind: string): Promise<Blob | null> {
  if (!creativeBlobs.has(sha) && DEMO_BLOBS[sha]) creativeBlobs.set(sha, await demoCreativeBlob(...DEMO_BLOBS[sha]));
  const source = creativeBlobs.get(sha) ?? null;
  if (kind === "file") return source;
  return firstFrames.get(sha) ?? source;
}

function usedBy(sha: string): number[] {
  return variants.filter((v) => v.layers.some((layer) => layer.creative_sha256 === sha)).map((v) => v.id);
}

function publicCreative(record: CreativeRecord): S["BannerCreative"] {
  const { deleted: _deleted, ...rest } = record;
  void _deleted;
  return { ...rest, used_by_variants: usedBy(record.sha256).length };
}

function brief(record: CreativeRecord): S["BannerCreativeBrief"] {
  return {
    sha256: record.sha256,
    name: record.name,
    kind: record.kind,
    status: record.status,
    width: record.width,
    height: record.height,
    thumb_url: record.thumb_url,
  };
}

async function sha256Hex(buffer: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

function sniff(bytes: Uint8Array): { format: S["CreativeSourceFormat"]; kind: S["CreativeKind"]; mime: string } | null {
  const head = (n: number) => Array.from(bytes.slice(0, n));
  const ascii = (from: number, to: number) => String.fromCharCode(...bytes.slice(from, to));
  if (head(4).join() === [0x89, 0x50, 0x4e, 0x47].join()) {
    const text = new TextDecoder("latin1").decode(bytes.slice(0, Math.min(bytes.length, 4096)));
    return text.includes("acTL")
      ? { format: "apng", kind: "animated", mime: "image/apng" }
      : { format: "png", kind: "static", mime: "image/png" };
  }
  if (head(3).join() === [0xff, 0xd8, 0xff].join()) return { format: "jpeg", kind: "static", mime: "image/jpeg" };
  if (ascii(0, 3) === "GIF") return { format: "gif", kind: "animated", mime: "image/gif" };
  if (ascii(0, 4) === "RIFF" && ascii(8, 12) === "WEBP") return { format: "webp", kind: "static", mime: "image/webp" };
  if (head(4).join() === [0x1a, 0x45, 0xdf, 0xa3].join())
    return { format: "webm", kind: "animated", mime: "video/webm" };
  if (ascii(4, 8) === "ftyp")
    return { format: ascii(8, 10) === "qt" ? "mov" : "mp4", kind: "animated", mime: "video/mp4" };
  return null;
}

const REJECT_MESSAGES: Record<S["CreativeRejectCode"], string> = {
  empty_file: "Файл пустой",
  corrupt_file: "Файл повреждён или не читается",
  unsupported_format: "Формат не поддерживается",
  file_too_large: "Файл больше 50 МБ",
  dimensions_too_large: "Слишком большое разрешение",
  dimensions_too_small: "Слишком маленькое разрешение",
  animated_too_long: "Анимация слишком длинная",
  too_many_frames: "Слишком много кадров",
  normalize_failed: "Не удалось перекодировать",
};

function reject(status: number, code: S["CreativeRejectCode"]) {
  const errCode =
    status === 413 ? "payload_too_large" : status === 415 ? "unsupported_media_type" : "creative_rejected";
  return error(status, errCode, REJECT_MESSAGES[code], { asset_error: code });
}

/** Нормализованный размер: вписать в канву 1080×1920 без увеличения. */
function normalizedSize(w: number, h: number): [number, number] {
  const k = Math.min(1, CANVAS_W / w, CANVAS_H / h);
  return [Math.max(1, Math.round(w * k)), Math.max(1, Math.round(h * k))];
}

let nextTaskId = 9100;

async function ingest(file: File, name: string | null, productId: string | null, userId: number) {
  const MAX = 50 * 1024 * 1024;
  if (file.size > MAX) return reject(413, "file_too_large");
  if (file.size === 0) return reject(422, "empty_file");
  const buffer = await file.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  const kind = sniff(bytes);
  if (!kind) return reject(415, "unsupported_format");
  const sha = await sha256Hex(buffer);
  const existing = creatives.find((item) => item.sha256 === sha);
  if (existing) {
    existing.deleted = false;
    return HttpResponse.json(
      { creative: publicCreative(existing), deduplicated: true } satisfies S["BannerCreativeUploadResult"],
      {
        status: 201,
      },
    );
  }
  let width = 600;
  let height = 200;
  let hasAlpha = kind.format !== "jpeg";
  if (kind.mime.startsWith("image/")) {
    try {
      const bitmap = await createImageBitmap(new Blob([buffer], { type: kind.mime }));
      width = bitmap.width;
      height = bitmap.height;
      if (kind.kind === "animated") {
        firstFrames.set(
          sha,
          await drawPng(bitmap.width, bitmap.height, (ctx) => {
            ctx.drawImage(bitmap, 0, 0);
          }),
        );
      }
      bitmap.close();
    } catch {
      return reject(422, "corrupt_file");
    }
  } else {
    hasAlpha = kind.format === "webm";
    firstFrames.set(
      sha,
      await drawPng(width, height, (ctx) => {
        ctx.fillStyle = "#2c7a7b";
        ctx.fillRect(0, 0, width, height);
        ctx.fillStyle = "#fff";
        ctx.font = "bold 40px sans-serif";
        ctx.fillText("VIDEO", 24, 110);
      }),
    );
  }
  if (width < 64 || height < 32) return reject(422, "dimensions_too_small");
  if (width > 4096 || height > 4096) return reject(422, "dimensions_too_large");
  const [nw, nh] = normalizedSize(width, height);
  creativeBlobs.set(sha, new Blob([buffer], { type: kind.mime }));
  const animated = kind.kind === "animated";
  const record = creativeRecord(sha, (name || file.name || "creative").slice(0, 200), {
    width: animated ? null : nw,
    height: animated ? null : nh,
    status: animated ? "processing" : "ready",
    kind: kind.kind,
    source_format: kind.format,
    source_bytes: file.size,
    source_width: width,
    source_height: height,
    has_alpha: animated ? null : hasAlpha,
    duration_sec: animated ? null : 0,
    frame_count: animated ? null : 1,
    normalized_mime: animated ? null : "image/png",
    normalized_bytes: animated ? null : file.size,
    file_url: animated ? null : `${API}/banner-creatives/${sha}/media/file`,
    thumb_url: animated ? null : `${API}/banner-creatives/${sha}/media/thumb`,
    first_frame_url: animated ? null : `${API}/banner-creatives/${sha}/media/first_frame`,
    product_id: productId,
    task_id: animated ? nextTaskId++ : null,
    created_by: userId,
    created_at: nowIso(),
    updated_at: nowIso(),
  });
  creatives.unshift(record);
  if (animated) {
    // «Тяжёлая задача нормализации» — готово через 3 с.
    setTimeout(() => {
      const base = `${API}/banner-creatives/${sha}/media`;
      Object.assign(record, {
        status: "ready",
        width: nw,
        height: nh,
        has_alpha: hasAlpha,
        duration_sec: 2.4,
        fps: 25,
        frame_count: 60,
        loop: true,
        normalized_mime: "video/webm",
        normalized_bytes: Math.round(file.size * 0.6),
        file_url: `${base}/file`,
        thumb_url: `${base}/thumb`,
        first_frame_url: `${base}/first_frame`,
        updated_at: nowIso(),
      } satisfies Partial<S["BannerCreative"]>);
    }, 3000);
  }
  return HttpResponse.json(
    { creative: publicCreative(record), deduplicated: false } satisfies S["BannerCreativeUploadResult"],
    {
      status: 201,
    },
  );
}

// ---------------------------------------------------------------- продукты

const products: S["Product"][] = [
  {
    id: "umka",
    name: "Умка",
    kind: "web_service",
    status: "active",
    landing_url: "https://umka.chat/",
    redirect_host: "u.umka.chat",
    redirect_mode: "landing_split",
    telegram_bot_username: "umka_bot",
    tma_short_name: null,
    telegram_link_style: "tg_scheme_with_fallback",
    utm_defaults: {},
    brand: { display_domain: "umka.chat", primary_color: "#6D4DFF", logo_asset_id: null },
    landing: {
      title: "Бесплатный расклад ждёт тебя",
      subtitle: "Выбери, как удобнее продолжить",
      button_telegram: "Открыть в Telegram",
      button_site: "Продолжить на сайте",
    },
    webhook_secret_set: false,
    stats_7d: { clicks: 0, unique_visitors: 0, bot_starts: 0, first_payments: 0, revenue_minor: 0, currency: "RUB" },
    created_at: iso(-60 * DAY),
    updated_at: iso(-2 * DAY),
  },
  {
    id: "vpn_bot",
    name: "VPN-бот",
    kind: "telegram_bot",
    status: "active",
    landing_url: null,
    redirect_host: "u.vpn.example",
    redirect_mode: "direct_telegram",
    telegram_bot_username: "tgbpn_bot",
    tma_short_name: null,
    telegram_link_style: "tg_scheme_with_fallback",
    utm_defaults: {},
    brand: { display_domain: null, primary_color: null, logo_asset_id: null },
    landing: {},
    webhook_secret_set: false,
    stats_7d: null,
    created_at: iso(-20 * DAY),
    updated_at: iso(-20 * DAY),
  },
];

function productOr404(id: string) {
  return products.find((item) => item.id === id);
}

// ---------------------------------------------------------------- кампании и варианты

const campaigns: S["Campaign"][] = [
  {
    id: 7,
    product_id: "umka",
    name: "Umka · осень",
    status: "active",
    banner_template_id: null,
    allocation: "weighted",
    priority: 100,
    targeting: { niche_ids: ["taro", "astro"], platforms: [], kinds: [], channel_ids: [] },
    starts_at: null,
    ends_at: null,
    notes: null,
    variants_count: 2,
    created_at: iso(-10 * DAY),
    updated_at: iso(-1 * DAY),
  },
];

function defaultLayers(product: S["Product"] | undefined, sha: string | null): S["BannerLayer"][] {
  const layers: S["BannerLayer"][] = [makeMicroBrandLayer(product?.brand.display_domain ?? "umka.chat")];
  if (sha) layers.push(makeCreativeLayer(sha));
  layers.push({ ...makeFallbackPlateLayer(), enabled: !sha });
  layers.push(makeCodeLayer());
  layers.push({ ...makeEridLayer(), enabled: false });
  return layers;
}

function makeVariant(
  id: number,
  campaign: S["Campaign"],
  code: string,
  name: string,
  sha: string | null,
  extra: Partial<S["BannerVariant"]> = {},
): S["BannerVariant"] {
  const product = productOr404(campaign.product_id);
  return {
    id,
    campaign_id: campaign.id,
    product_id: campaign.product_id,
    code,
    name,
    status: "draft",
    weight: 50,
    layers: defaultLayers(product, sha),
    timing: structuredClone(DEFAULT_TIMING),
    ad_marking: { required: false, erid: null, advertiser_text: null },
    code_url_template: null,
    effective_code_url_template: `${product?.redirect_host ?? "u.umka.chat"}/{code}`,
    voice_cta_text: null,
    pinned_comment_template: null,
    description_cta_template: null,
    notes: null,
    creatives: [],
    version: 1,
    created_at: nowIso(),
    updated_at: nowIso(),
    ...extra,
  };
}

const variants: S["BannerVariant"][] = [
  makeVariant(21, campaigns[0], "A", "Домен + расклад", DEMO_SHA_A, { status: "active", created_at: iso(-10 * DAY) }),
  makeVariant(22, campaigns[0], "B", "Креатив «карта дня»", DEMO_SHA_B, {
    status: "active",
    created_at: iso(-9 * DAY),
  }),
];
let nextVariantId = 40;
let nextCampaignId = 20;

function withCreatives(variant: S["BannerVariant"]): S["BannerVariant"] {
  const shas = variant.layers.map((layer) => layer.creative_sha256).filter((sha): sha is string => !!sha);
  const list = shas.map((sha) => creatives.find((item) => item.sha256 === sha)).filter((c): c is CreativeRecord => !!c);
  return { ...variant, creatives: list.map(brief) };
}

function campaignOut(campaign: S["Campaign"]): S["Campaign"] {
  return { ...campaign, variants_count: variants.filter((v) => v.campaign_id === campaign.id).length };
}

// ---------------------------------------------------------------- шаблоны

const templates: S["BannerTemplate"][] = [
  {
    id: 1,
    name: "default",
    status: "active",
    layout: {
      code: "A",
      name: "default",
      layers: defaultLayers(products[0], null),
      timing: structuredClone(DEFAULT_TIMING),
      ad_marking: { required: false, erid: null, advertiser_text: null },
      code_url_template: null,
    },
    version: 1,
    created_at: iso(-30 * DAY),
    updated_at: iso(-30 * DAY),
  },
];

// ---------------------------------------------------------------- проверка (те же правила, что у холста)

function sizesOf(layers: readonly S["BannerLayer"][]): Record<string, CreativeSize> {
  const out: Record<string, CreativeSize> = {};
  for (const layer of layers) {
    const sha = layer.creative_sha256;
    const record = sha ? creatives.find((item) => item.sha256 === sha && !item.deleted) : undefined;
    if (sha && record?.status === "ready" && record.width && record.height)
      out[sha] = { width: record.width, height: record.height };
  }
  return out;
}

function captionsZone(blocked: Box[], profile: S["BannerSafeZoneProfile"]): S["CaptionsZoneInfo"] {
  const safe = rectToBox(profile.safe);
  const gap = 24;
  const spans = blocked
    .filter((r) => r.w > 0 && r.h > 0 && r.x < safe.x + safe.w && r.x + r.w > safe.x)
    .map((r) => [Math.max(safe.y, r.y - gap), Math.min(safe.y + safe.h, r.y + r.h + gap)] as [number, number])
    .sort((a, b) => a[0] - b[0]);
  const bands: Array<[number, number]> = [];
  let cursor = safe.y;
  for (const [y1, y2] of spans) {
    if (y1 > cursor) bands.push([cursor, y1]);
    cursor = Math.max(cursor, y2);
  }
  if (cursor < safe.y + safe.h) bands.push([cursor, safe.y + safe.h]);
  const fitting = bands.filter(([a, b]) => b - a >= 220);
  const pick = (fitting.length ? fitting : bands).reduce<[number, number] | null>((best, band) => {
    if (!best) return band;
    if (!fitting.length) return band[1] - band[0] > best[1] - best[0] ? band : best;
    return Math.abs(Math.min(band[1], 1100) - 1100) < Math.abs(Math.min(best[1], 1100) - 1100) ? band : best;
  }, null) ?? [safe.y, safe.y];
  const y2 = Math.min(pick[1], Math.max(pick[0] + 360, Math.min(pick[1], 1100)));
  const y1 = Math.max(pick[0], y2 - 360);
  return { rect: { x1: safe.x, y1, x2: safe.x + safe.w, y2 }, fits: y2 - y1 >= 220 };
}

function validateDraft(
  draft: Pick<S["BannerVariantDraft"], "layers" | "timing" | "ad_marking">,
  platform: S["Platform"],
  profileIds: string[] | undefined,
  duration: number,
): S["BannerValidateResponse"] {
  const ids = profileIds?.length ? profileIds : [platform === "vk" ? "vk_clips" : "youtube_shorts", "common_organic"];
  const profiles = ids.map(profileById).filter((p): p is S["BannerSafeZoneProfile"] => !!p);
  const layout = placeVariant(draft.layers, sizesOf(draft.layers));
  const warnings: S["BannerWarning"][] = [];
  const canvas: Box = { x: 0, y: 0, w: CANVAS_W, h: CANVAS_H };
  for (const pl of layout.placements) {
    const rect = pl.rect;
    if (!rect || rect.w <= 0 || rect.h <= 0) continue;
    const full = pl.full ?? rect;
    if (full.x < canvas.x || full.y < canvas.y || full.x + full.w > CANVAS_W || full.y + full.h > CANVAS_H) {
      warnings.push({
        code: "layer_outside_canvas",
        severity: "warn",
        layer_id: pl.layerId,
        profile_id: null,
        message: "Слой выходит за край кадра и будет обрезан",
        details: {},
      });
    }
    for (const profile of profiles) {
      const overflow = safeZoneOverflow(rect, profile);
      const sides = (Object.keys(overflow) as Array<keyof typeof overflow>).filter((side) => overflow[side] > 0);
      if (!sides.length) continue;
      if (pl.kind === "micro_brand" && overflow.top > 0) {
        warnings.push({
          code: "micro_brand_in_top_bar",
          severity: "warn",
          layer_id: pl.layerId,
          profile_id: profile.id,
          message: `Микро-бренд заходит в верхнюю панель площадки на ${overflow.top} px`,
          details: { overflow_px: overflow.top },
        });
      }
      if (pl.kind !== "micro_brand" && overflow.bottom > 0) {
        warnings.push({
          code: "creative_in_bottom_dead_zone",
          severity: "error",
          layer_id: pl.layerId,
          profile_id: profile.id,
          message: `Нижний край на ${overflow.bottom} px в зоне подписи и кнопок площадки`,
          details: { overflow_px: overflow.bottom },
        });
      }
      const rest = sides.filter(
        (side) => !(side === "bottom" && pl.kind !== "micro_brand") && !(side === "top" && pl.kind === "micro_brand"),
      );
      if (rest.length) {
        warnings.push({
          code: "creative_outside_safe_zone",
          severity: "warn",
          layer_id: pl.layerId,
          profile_id: profile.id,
          message: `Слой выходит за безопасную зону: ${rest.map((side) => `${side} ${overflow[side]} px`).join(", ")}`,
          details: {},
        });
      }
    }
  }
  const erid = draft.layers.find((layer) => layer.kind === "text_erid");
  if (draft.ad_marking?.required && (!draft.ad_marking.erid || !erid?.enabled)) {
    warnings.push({
      code: "erid_missing_when_required",
      severity: "error",
      layer_id: erid?.id ?? null,
      profile_id: null,
      message: "Маркировка обязательна, а erid или слоя «Реклама · erid» нет",
      details: {},
    });
  }
  if (layout.fallbackUsed) {
    warnings.push({
      code: "fallback_plate_used",
      severity: "info",
      layer_id: layout.offerLayerId,
      profile_id: null,
      message: "Креатива нет — показывается запасная плашка",
      details: {},
    });
  }
  for (const sha of layout.missingCreatives) {
    warnings.push({
      code: "creative_missing",
      severity: "error",
      layer_id: null,
      profile_id: null,
      message: `Креатив ${sha.slice(0, 8)}… не найден или ещё не готов`,
      details: { sha256: sha },
    });
  }
  const timing = (draft.timing ?? DEFAULT_TIMING) as S["BannerTiming"];
  const { windows, stretched } = computeWindows(timing, duration);
  if (stretched) {
    warnings.push({
      code: "impulse_window_stretched",
      severity: "info",
      layer_id: null,
      profile_id: null,
      message: "Ролик короткий: окно импульса растянуто",
      details: {},
    });
  }
  const main = profiles[0] ?? SAFE_ZONES.items[0];
  const zone = captionsZone(
    layout.placements.map((p) => p.rect).filter((r): r is Box => !!r),
    main,
  );
  if (!zone.fits)
    warnings.push({
      code: "captions_zone_too_small",
      severity: "warn",
      layer_id: null,
      profile_id: main.id,
      message: "Под субтитры остаётся мало места",
      details: {},
    });
  const rank = { error: 0, warn: 1, info: 2 } as const;
  warnings.sort((a, b) => rank[a.severity] - rank[b.severity]);
  return {
    ok: !warnings.some((w) => w.severity === "error"),
    profile_ids: profiles.map((p) => p.id),
    duration_sec: duration,
    windows: [
      ...(draft.layers.some((layer) => layer.enabled && layer.show === "persistent")
        ? [{ group: "persistent" as const, start_sec: 0, end_sec: duration }]
        : []),
      ...windows.map((w) => ({ group: "with_creative" as const, ...w })),
    ],
    placements: layout.placements.map((p) => ({
      layer_id: p.layerId,
      kind: p.kind,
      show: p.show,
      creative_sha256: p.creativeSha256,
      slot: { x1: p.slot.x, y1: p.slot.y, x2: p.slot.x + p.slot.w, y2: p.slot.y + p.slot.h },
      rect: p.rect
        ? { x1: p.rect.x, y1: p.rect.y, x2: p.rect.x + p.rect.w, y2: p.rect.y + p.rect.h }
        : { x1: 0, y1: 0, x2: 0, y2: 0 },
      full_rect: p.full ? { x1: p.full.x, y1: p.full.y, x2: p.full.x + p.full.w, y2: p.full.y + p.full.h } : null,
      scale: p.scale,
      clipped: false,
      z: p.z,
    })),
    captions_zone: zone,
    fallback_used: layout.fallbackUsed,
    missing_creatives: layout.missingCreatives,
    warnings,
  };
}

function draftOfRequest(body: { variant_id?: number | null; draft?: S["BannerVariantDraft"] | null }) {
  if (body.draft) return body.draft;
  const variant = body.variant_id ? variants.find((v) => v.id === body.variant_id) : undefined;
  return variant ?? null;
}

// ---------------------------------------------------------------- «Кадр из рендера»

async function renderPreview(body: S["BannerPreviewRequest"], draft: S["BannerVariantDraft"]) {
  const duration = body.duration_sec ?? 30;
  const timing = (draft.timing ?? DEFAULT_TIMING) as S["BannerTiming"];
  const { windows } = computeWindows(timing, duration);
  // Дефолты схемы (moment=impulse, background=sample, size=half) — как у сервера, если поле не передано.
  const moment = body.moment ?? "impulse";
  let t = body.t_sec ?? 1;
  if (moment === "impulse" && windows[0]) t = (windows[0].start_sec + windows[0].end_sec) / 2;
  if (moment === "final_repeat" && windows.length) t = (windows[windows.length - 1].start_sec + duration) / 2;
  const full = body.size === "full";
  const [w, h] = full ? [CANVAS_W, CANVAS_H] : [540, 960];
  const k = w / CANVAS_W;
  const layout = placeVariant(draft.layers, sizesOf(draft.layers));
  const blob = await drawPng(w, h, async (ctx) => {
    if (body.background === "black") {
      ctx.fillStyle = "#000";
      ctx.fillRect(0, 0, w, h);
    } else if (body.background === "checker") {
      for (let y = 0; y < h; y += 20)
        for (let x = 0; x < w; x += 20) {
          ctx.fillStyle = (x / 20 + y / 20) % 2 ? "#3a3d42" : "#2b2d31";
          ctx.fillRect(x, y, 20, 20);
        }
    } else {
      const gradient = ctx.createLinearGradient(0, 0, w * 0.35, h);
      gradient.addColorStop(0, "#1d3b4a");
      gradient.addColorStop(0.55, "#2c2446");
      gradient.addColorStop(1, "#141418");
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, w, h);
    }
    ctx.scale(k, k);
    for (const pl of layout.placements) {
      if (!isVisibleAt(pl.show, windows, t) || !pl.rect) continue;
      const layer = draft.layers.find((item) => item.id === pl.layerId);
      if (!layer) continue;
      ctx.save();
      ctx.globalAlpha = layer.slot.opacity;
      if (pl.creativeSha256 && pl.full) {
        const source = await blobFor(pl.creativeSha256, layer.kind === "creative" ? "first_frame" : "thumb");
        if (source) {
          const bitmap = await createImageBitmap(source);
          ctx.beginPath();
          ctx.rect(pl.rect.x, pl.rect.y, pl.rect.w, pl.rect.h);
          ctx.clip();
          ctx.drawImage(bitmap, pl.full.x, pl.full.y, pl.full.w, pl.full.h);
          bitmap.close();
        }
      } else if (layer.text) {
        const style = layer.style;
        const text = layer.text
          .replaceAll("{code_url}", "u.umka.chat/k7m2q")
          .replaceAll("{code}", body.code ?? "k7m2q")
          .replaceAll("{erid}", draft.ad_marking?.erid ?? "—")
          .replaceAll("{advertiser}", draft.ad_marking?.advertiser_text ?? "");
        const px = Math.min(style?.max_font_px ?? 36, pl.rect.h * 0.7);
        ctx.font = `bold ${px}px sans-serif`;
        const tw = Math.min(ctx.measureText(text).width + 2 * (style?.padding_x ?? 16), pl.rect.w);
        const th = Math.min(px * 1.3 + 2 * (style?.padding_y ?? 8), pl.rect.h);
        const x = pl.rect.x + (pl.rect.w - tw) / 2;
        const y = pl.rect.y + (pl.rect.h - th) / 2;
        ctx.fillStyle = `rgba(0,0,0,${style?.bg_opacity ?? 0.55})`;
        ctx.beginPath();
        ctx.roundRect(x, y, tw, th, style?.corner_radius_px ?? 10);
        ctx.fill();
        ctx.fillStyle = style?.color ?? "#FFFFFF";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(text, x + tw / 2, y + th / 2, tw);
      }
      ctx.restore();
    }
  });
  return HttpResponse.arrayBuffer(await blob.arrayBuffer(), {
    headers: { "Content-Type": "image/png", "X-Preview-T-Sec": t.toFixed(3), "Cache-Control": "no-store" },
  });
}

// ---------------------------------------------------------------- ниши, кусты, каналы

const niches: S["NicheRow"][] = [
  {
    id: "taro",
    label: "Таро",
    status: "active",
    current_version: 3,
    used_by_channels: ["taro_main", "taro_vk", "taro_second"],
    updated_at: iso(-3 * DAY),
  },
  {
    id: "astro",
    label: "Астрология",
    status: "active",
    current_version: 2,
    used_by_channels: ["astro_main"],
    updated_at: iso(-6 * DAY),
  },
];

const clusters: S["Cluster"][] = [
  {
    id: "taro",
    title: "Таро",
    notes: null,
    youtube_api_key_secret: "YT_KEY_TARO",
    youtube_oauth_client_secret: null,
    channels_count: 3,
    created_at: iso(-40 * DAY),
  },
  {
    id: "astro",
    title: "Астрология",
    notes: null,
    youtube_api_key_secret: null,
    youtube_oauth_client_secret: null,
    channels_count: 1,
    created_at: iso(-40 * DAY),
  },
];

function channelSeed(
  id: string,
  platform: S["Platform"],
  nicheId: string,
  title: string,
  status: S["ChannelStatus"],
  profile: S["ChannelProfile"],
): S["Channel"] {
  return {
    id,
    platform,
    niche_id: nicheId,
    cluster: nicheId,
    title,
    platform_channel_ref: platform === "youtube" ? `UC${id.padEnd(22, "x").slice(0, 22)}` : "-218000111",
    url: null,
    status,
    replaced_by: null,
    default_product_id: "umka",
    profile,
    publishing_plan: { videos_per_day: platform === "youtube" ? 3 : 2 },
    advanced_features: platform === "youtube",
    subscribers: platform === "youtube" ? 1240 : 310,
    notes: null,
    profile_duplicate_of: [],
    created_at: iso(-40 * DAY),
    updated_at: iso(-2 * DAY),
  };
}

const SAME_PROFILE: S["ChannelProfile"] = {
  tts_provider: "edge_tts",
  tts_voice: "ru-RU-SvetlanaNeural",
  banner_template_id: 1,
  visual_style_suffix: "мистический, тёмный фон",
  seed_family: "taro",
};

const channels: S["Channel"][] = [
  channelSeed("taro_main", "youtube", "taro", "Таро — выбери карту", "active", SAME_PROFILE),
  channelSeed("astro_main", "youtube", "astro", "Астро на каждый день", "active", {
    tts_provider: "edge_tts",
    tts_voice: "ru-RU-DmitryNeural",
    banner_template_id: 1,
    visual_style_suffix: "звёздное небо",
    seed_family: "astro",
  }),
  channelSeed("taro_vk", "vk", "taro", "Таро · VK Клипы", "warming", {
    ...SAME_PROFILE,
    tts_voice: "ru-RU-DariyaNeural",
  }),
  channelSeed("taro_second", "youtube", "taro", "Таро · второй канал", "warming", SAME_PROFILE),
];

function profileKey(channel: S["Channel"]) {
  return JSON.stringify([channel.niche_id, channel.platform, channel.profile]);
}

function channelOut(channel: S["Channel"]): S["Channel"] {
  const key = profileKey(channel);
  return {
    ...channel,
    profile_duplicate_of: channels
      .filter((other) => other.id !== channel.id && profileKey(other) === key)
      .map((other) => other.id),
  };
}

const incidents: S["ChannelIncident"][] = [
  {
    id: 1,
    channel_id: "taro_main",
    kind: "reach_drop",
    occurred_at: iso(-4 * DAY),
    details: {},
    note: "Просмотры −60% после серии одинаковых хуков",
    created_by: 1,
  },
  {
    id: 2,
    channel_id: "taro_main",
    kind: "note",
    occurred_at: iso(-3 * DAY),
    details: {},
    note: "Сменили шаблон хуков, ждём 72 ч",
    created_by: 1,
  },
];
let nextIncidentId = 10;

const SLUG_RE = /^[a-z0-9][a-z0-9_-]{1,39}$/;

// ---------------------------------------------------------------- обработчики

export const catalogHandlers = [
  // ---------- продукты
  http.get(`${API}/products`, ({ request }) => {
    const who = guard(request);
    if (who instanceof Response) return who;
    const url = new URL(request.url);
    const status = url.searchParams.get("status");
    const items = products.filter((p) => !status || p.status === status);
    return HttpResponse.json(page(items, url) satisfies S["ProductPage"]);
  }),
  http.post(`${API}/products`, async ({ request }) => {
    const who = guard(request, OPERATORS);
    if (who instanceof Response) return who;
    const body = (await request.json()) as S["ProductCreate"];
    if (!SLUG_RE.test(body.id)) return invalid(["id"], "Недопустимый id", "string_pattern_mismatch");
    if (productOr404(body.id)) return error(409, "product_exists", "Продукт с таким id уже есть");
    const product: S["Product"] = {
      id: body.id,
      name: body.name,
      kind: body.kind,
      status: "active",
      landing_url: body.landing_url ?? null,
      redirect_host: body.redirect_host ?? null,
      redirect_mode: body.redirect_mode ?? "landing_split",
      telegram_bot_username: body.telegram_bot_username ?? null,
      tma_short_name: body.tma_short_name ?? null,
      telegram_link_style: body.telegram_link_style ?? "tg_scheme_with_fallback",
      utm_defaults: body.utm_defaults ?? {},
      brand: body.brand ?? {},
      landing: body.landing ?? {},
      webhook_secret_set: false,
      stats_7d: null,
      created_at: nowIso(),
      updated_at: nowIso(),
    };
    products.push(product);
    return HttpResponse.json(product, { status: 201 });
  }),
  http.get(`${API}/products/:productId`, ({ request, params }) => {
    const who = guard(request);
    if (who instanceof Response) return who;
    const product = productOr404(String(params.productId));
    return product
      ? HttpResponse.json({ ...product, stats_7d: null })
      : error(404, "product_not_found", "Продукт не найден");
  }),
  http.patch(`${API}/products/:productId`, async ({ request, params }) => {
    const who = guard(request, OPERATORS);
    if (who instanceof Response) return who;
    const product = productOr404(String(params.productId));
    if (!product) return error(404, "product_not_found", "Продукт не найден");
    const body = (await request.json()) as S["ProductPatch"];
    for (const [key, value] of Object.entries(body)) {
      if (value !== undefined) (product as Record<string, unknown>)[key] = value;
    }
    product.updated_at = nowIso();
    return HttpResponse.json(product);
  }),
  http.post(`${API}/products/:productId/archive`, ({ request, params }) => {
    const who = guard(request, OPERATORS);
    if (who instanceof Response) return who;
    const product = productOr404(String(params.productId));
    if (!product) return error(404, "product_not_found", "Продукт не найден");
    product.status = "archived";
    product.updated_at = nowIso();
    return HttpResponse.json(product);
  }),

  // ---------- кампании
  http.get(`${API}/campaigns`, ({ request }) => {
    const who = guard(request);
    if (who instanceof Response) return who;
    const url = new URL(request.url);
    const productId = url.searchParams.get("product_id");
    const statuses = url.searchParams.getAll("status");
    const items = campaigns
      .filter((c) => (!productId || c.product_id === productId) && (!statuses.length || statuses.includes(c.status)))
      .map(campaignOut);
    return HttpResponse.json(page(items, url) satisfies S["CampaignPage"]);
  }),
  http.post(`${API}/campaigns`, async ({ request }) => {
    const who = guard(request, OPERATORS);
    if (who instanceof Response) return who;
    const body = (await request.json()) as S["CampaignCreate"];
    if (!productOr404(body.product_id)) return invalid(["product_id"], "Продукт не найден", "not_found");
    if (!body.name?.trim()) return invalid(["name"], "Укажите название", "missing");
    const campaign: S["Campaign"] = {
      id: nextCampaignId++,
      product_id: body.product_id,
      name: body.name,
      status: "draft",
      banner_template_id: body.banner_template_id ?? null,
      allocation: body.allocation ?? "weighted",
      priority: body.priority ?? 100,
      targeting: body.targeting ?? {},
      starts_at: body.starts_at ?? null,
      ends_at: body.ends_at ?? null,
      notes: body.notes ?? null,
      variants_count: 0,
      created_at: nowIso(),
      updated_at: nowIso(),
    };
    campaigns.push(campaign);
    return HttpResponse.json(campaign, { status: 201 });
  }),
  http.get(`${API}/campaigns/:campaignId`, ({ request, params }) => {
    const who = guard(request);
    if (who instanceof Response) return who;
    const campaign = campaigns.find((c) => c.id === Number(params.campaignId));
    return campaign
      ? HttpResponse.json(campaignOut(campaign))
      : error(404, "campaign_not_found", "Кампания не найдена");
  }),
  http.patch(`${API}/campaigns/:campaignId`, async ({ request, params }) => {
    const who = guard(request, OPERATORS);
    if (who instanceof Response) return who;
    const campaign = campaigns.find((c) => c.id === Number(params.campaignId));
    if (!campaign) return error(404, "campaign_not_found", "Кампания не найдена");
    const body = (await request.json()) as S["CampaignPatch"];
    for (const [key, value] of Object.entries(body)) {
      if (value !== undefined) (campaign as Record<string, unknown>)[key] = value;
    }
    campaign.updated_at = nowIso();
    return HttpResponse.json(campaignOut(campaign));
  }),
  http.post(`${API}/campaigns/:campaignId/status`, async ({ request, params }) => {
    const who = guard(request, OPERATORS);
    if (who instanceof Response) return who;
    const campaign = campaigns.find((c) => c.id === Number(params.campaignId));
    if (!campaign) return error(404, "campaign_not_found", "Кампания не найдена");
    campaign.status = ((await request.json()) as S["CampaignStatusChange"]).status;
    campaign.updated_at = nowIso();
    return HttpResponse.json(campaignOut(campaign));
  }),
  http.get(`${API}/campaigns/:campaignId/variants`, ({ request, params }) => {
    const who = guard(request);
    if (who instanceof Response) return who;
    const id = Number(params.campaignId);
    if (!campaigns.some((c) => c.id === id)) return error(404, "campaign_not_found", "Кампания не найдена");
    const items = variants.filter((v) => v.campaign_id === id).map(withCreatives);
    return HttpResponse.json({ items } satisfies S["BannerVariantList"]);
  }),
  http.post(`${API}/campaigns/:campaignId/variants`, async ({ request, params }) => {
    const who = guard(request, OPERATORS);
    if (who instanceof Response) return who;
    const campaign = campaigns.find((c) => c.id === Number(params.campaignId));
    if (!campaign) return error(404, "campaign_not_found", "Кампания не найдена");
    const body = (await request.json()) as S["BannerVariantCreate"];
    if (!/^[A-Za-z0-9_-]{1,16}$/.test(body.code))
      return invalid(["code"], "Код: латиница и цифры, до 16", "string_pattern_mismatch");
    if (variants.some((v) => v.campaign_id === campaign.id && v.code === body.code)) {
      return invalid(["code"], "Такой код уже есть в кампании", "unique");
    }
    const sha = body.creative_sha256 ?? null;
    if (sha && !creatives.some((c) => c.sha256 === sha && !c.deleted)) {
      return invalid(["creative_sha256"], "Креатив не найден", "not_found");
    }
    const variant = makeVariant(nextVariantId++, campaign, body.code, body.name, body.layers ? null : sha, {
      status: body.status ?? "draft",
      weight: body.weight ?? 50,
      ...(body.layers ? { layers: body.layers } : {}),
      ...(body.timing ? { timing: body.timing as S["BannerTiming"] } : {}),
      ...(body.ad_marking ? { ad_marking: body.ad_marking } : {}),
      code_url_template: body.code_url_template ?? null,
    });
    if (body.ad_marking?.required) {
      variant.layers = variant.layers.map((layer) =>
        layer.kind === "text_erid" ? { ...layer, enabled: true } : layer,
      );
    }
    variants.push(variant);
    return HttpResponse.json(withCreatives(variant), { status: 201 });
  }),

  // ---------- редактор: креативы, safe zones, шрифты, проверка, кадр
  http.get(`${API}/banner-creatives`, ({ request }) => {
    const who = guard(request);
    if (who instanceof Response) return who;
    const url = new URL(request.url);
    const productId = url.searchParams.get("product_id");
    const kind = url.searchParams.get("kind");
    const q = (url.searchParams.get("q") ?? "").toLowerCase();
    const items = creatives
      .filter((c) => !c.deleted && (!productId || c.product_id === productId) && (!kind || c.kind === kind))
      .filter((c) => !q || c.name.toLowerCase().includes(q))
      .map(publicCreative);
    return HttpResponse.json(page(items, url) satisfies S["BannerCreativePage"]);
  }),
  http.post(`${API}/banner-creatives`, async ({ request }) => {
    const who = guard(request, OPERATORS);
    if (who instanceof Response) return who;
    let form: FormData;
    try {
      form = await request.formData();
    } catch {
      return invalid(["file"], "Нужен multipart с полем file", "missing");
    }
    const file = form.get("file");
    if (!(file instanceof File)) return invalid(["file"], "Нужен файл", "missing");
    const name = form.get("name");
    const productId = form.get("product_id");
    return ingest(
      file,
      typeof name === "string" ? name : null,
      typeof productId === "string" ? productId : null,
      who.userId,
    );
  }),
  http.get(`${API}/banner-creatives/:sha/media/:kind`, async ({ request, params }) => {
    const who = guard(request);
    if (who instanceof Response) return who;
    const sha = String(params.sha);
    const record = creatives.find((c) => c.sha256 === sha);
    if (!record || record.status !== "ready") return error(404, "creative_media_not_found", "Креатив ещё не готов");
    const blob = await blobFor(sha, String(params.kind));
    if (!blob) return error(404, "creative_media_not_found", "Файл креатива не найден");
    return HttpResponse.arrayBuffer(await blob.arrayBuffer(), {
      headers: { "Content-Type": blob.type || "image/png", "Cache-Control": "private, max-age=604800, immutable" },
    });
  }),
  http.get(`${API}/banner-creatives/:sha`, ({ request, params }) => {
    const who = guard(request);
    if (who instanceof Response) return who;
    const record = creatives.find((c) => c.sha256 === String(params.sha));
    return record ? HttpResponse.json(publicCreative(record)) : error(404, "creative_not_found", "Креатив не найден");
  }),
  http.patch(`${API}/banner-creatives/:sha`, async ({ request, params }) => {
    const who = guard(request, OPERATORS);
    if (who instanceof Response) return who;
    const record = creatives.find((c) => c.sha256 === String(params.sha) && !c.deleted);
    if (!record) return error(404, "creative_not_found", "Креатив не найден");
    const body = (await request.json()) as S["BannerCreativePatch"];
    if (body.name !== undefined && body.name !== null) record.name = body.name;
    if (body.notes !== undefined) record.notes = body.notes;
    if (body.product_id !== undefined) record.product_id = body.product_id;
    record.updated_at = nowIso();
    return HttpResponse.json(publicCreative(record));
  }),
  http.delete(`${API}/banner-creatives/:sha`, ({ request, params }) => {
    const who = guard(request, OPERATORS);
    if (who instanceof Response) return who;
    const record = creatives.find((c) => c.sha256 === String(params.sha) && !c.deleted);
    if (!record) return error(404, "creative_not_found", "Креатив не найден");
    const ids = usedBy(record.sha256);
    if (ids.length) return error(409, "creative_in_use", "Креатив стоит в вариантах", { variant_ids: ids });
    record.deleted = true;
    return new HttpResponse(null, { status: 204 });
  }),
  http.get(`${API}/banner-safe-zones`, ({ request }) => {
    const who = guard(request);
    if (who instanceof Response) return who;
    return HttpResponse.json(SAFE_ZONES);
  }),
  http.get(`${API}/banner-fonts`, ({ request }) => {
    const who = guard(request);
    if (who instanceof Response) return who;
    return HttpResponse.json({
      items: ["Montserrat-Bold.ttf", "Montserrat-SemiBold.ttf"].map((name) => ({
        name,
        family: "Montserrat",
        style: name.includes("Semi") ? "SemiBold" : "Bold",
        cyrillic_ok: true,
        file_url: `${API}/banner-fonts/${name}`,
      })),
    } satisfies S["BannerFontList"]);
  }),
  http.get(`${API}/banner-fonts/:name`, () => error(404, "font_not_found", "В моках файлов шрифтов нет")),
  http.post(`${API}/banner-variants/validate`, async ({ request }) => {
    const who = guard(request, OPERATORS);
    if (who instanceof Response) return who;
    const body = (await request.json()) as S["BannerValidateRequest"];
    const draft = draftOfRequest(body);
    if (!draft) return invalid(["draft"], "Нужен draft или variant_id", "missing");
    const unknown = (body.profile_ids ?? []).findIndex((id) => !profileById(id));
    if (unknown >= 0)
      return invalid(["profile_ids", unknown], `Нет профиля safe zone ${body.profile_ids?.[unknown]}`, "not_found");
    return HttpResponse.json(
      validateDraft(draft, body.platform ?? "youtube", body.profile_ids, body.duration_sec ?? 30),
    );
  }),
  http.post(`${API}/banner-variants/preview`, async ({ request }) => {
    const who = guard(request, OPERATORS);
    if (who instanceof Response) return who;
    const body = (await request.json()) as S["BannerPreviewRequest"];
    const draft = draftOfRequest(body);
    if (!draft) return invalid(["draft"], "Нужен draft или variant_id", "missing");
    return renderPreview(body, draft);
  }),
  http.get(`${API}/banner-variants/:variantId`, ({ request, params }) => {
    const who = guard(request);
    if (who instanceof Response) return who;
    const variant = variants.find((v) => v.id === Number(params.variantId));
    return variant
      ? HttpResponse.json(withCreatives(variant))
      : error(404, "banner_variant_not_found", "Вариант не найден");
  }),
  http.patch(`${API}/banner-variants/:variantId`, async ({ request, params }) => {
    const who = guard(request, OPERATORS);
    if (who instanceof Response) return who;
    const variant = variants.find((v) => v.id === Number(params.variantId));
    if (!variant) return error(404, "banner_variant_not_found", "Вариант не найден");
    const body = (await request.json()) as S["BannerVariantPatch"];
    if (
      body.expected_version !== undefined &&
      body.expected_version !== null &&
      body.expected_version !== variant.version
    ) {
      return error(409, "version_conflict", "Вариант уже изменили", { current_version: variant.version });
    }
    if (body.layers) {
      const bad = body.layers.findIndex(
        (layer) => layer.creative_sha256 && !creatives.some((c) => c.sha256 === layer.creative_sha256 && !c.deleted),
      );
      if (bad >= 0) return invalid(["layers", bad, "creative_sha256"], "Креатив не найден", "not_found");
    }
    const next = { ...variant };
    for (const [key, value] of Object.entries(body)) {
      if (key === "expected_version" || value === undefined) continue;
      (next as Record<string, unknown>)[key] = value;
    }
    if (next.status === "active") {
      const result = validateDraft(next, "youtube", [], 30);
      const errors = result.warnings.filter((w) => w.severity === "error" && w.code === "erid_missing_when_required");
      if (errors.length)
        return error(422, "validation_error", "У активного варианта не может быть ошибок маркировки", {
          warnings: errors,
        });
    }
    Object.assign(variant, next, { version: variant.version + 1, updated_at: nowIso() });
    return HttpResponse.json(withCreatives(variant));
  }),
  http.post(`${API}/banner-variants/:variantId/status`, async ({ request, params }) => {
    const who = guard(request, OPERATORS);
    if (who instanceof Response) return who;
    const variant = variants.find((v) => v.id === Number(params.variantId));
    if (!variant) return error(404, "banner_variant_not_found", "Вариант не найден");
    const { status } = (await request.json()) as S["VariantStatusChange"];
    if (status === "active") {
      const result = validateDraft(variant, "youtube", [], 30);
      const errors = result.warnings.filter((w) => w.severity === "error");
      if (errors.length) return error(422, "validation_error", "Вариант не прошёл проверку", { warnings: errors });
    }
    variant.status = status;
    variant.version += 1;
    variant.updated_at = nowIso();
    return HttpResponse.json(withCreatives(variant));
  }),
  http.post(`${API}/banner-variants/:variantId/duplicate`, async ({ request, params }) => {
    const who = guard(request, OPERATORS);
    if (who instanceof Response) return who;
    const source = variants.find((v) => v.id === Number(params.variantId));
    if (!source) return error(404, "banner_variant_not_found", "Вариант не найден");
    const body = (await request.json()) as S["BannerVariantDuplicate"];
    const campaignId = body.campaign_id ?? source.campaign_id;
    if (variants.some((v) => v.campaign_id === campaignId && v.code === body.code)) {
      return invalid(["code"], "Такой код уже есть в кампании", "unique");
    }
    const copy: S["BannerVariant"] = {
      ...structuredClone(source),
      id: nextVariantId++,
      campaign_id: campaignId,
      code: body.code,
      name: body.name || `${source.name} (копия)`,
      status: "draft",
      version: 1,
      created_at: nowIso(),
      updated_at: nowIso(),
    };
    variants.push(copy);
    return HttpResponse.json(withCreatives(copy), { status: 201 });
  }),

  // ---------- шаблоны
  http.get(`${API}/banner-templates`, ({ request }) => {
    const who = guard(request);
    if (who instanceof Response) return who;
    return HttpResponse.json(page(templates, new URL(request.url)) satisfies S["BannerTemplatePage"]);
  }),
  http.get(`${API}/banner-templates/:templateId`, ({ request, params }) => {
    const who = guard(request);
    if (who instanceof Response) return who;
    const template = templates.find((t) => t.id === Number(params.templateId));
    return template ? HttpResponse.json(template) : error(404, "banner_template_not_found", "Шаблон не найден");
  }),
  http.patch(`${API}/banner-templates/:templateId`, async ({ request, params }) => {
    const who = guard(request, OPERATORS);
    if (who instanceof Response) return who;
    const template = templates.find((t) => t.id === Number(params.templateId));
    if (!template) return error(404, "banner_template_not_found", "Шаблон не найден");
    const body = (await request.json()) as S["BannerTemplatePatch"];
    if (body.expected_version !== template.version) {
      return error(409, "version_conflict", "Шаблон уже изменили", { current_version: template.version });
    }
    if (body.layout) template.layout = body.layout;
    if (body.name) template.name = body.name;
    if (body.status) template.status = body.status;
    template.version += 1;
    template.updated_at = nowIso();
    return HttpResponse.json(template);
  }),

  // ---------- ниши (список для форм), кусты
  http.get(`${API}/niches`, ({ request }) => {
    const who = guard(request);
    if (who instanceof Response) return who;
    return HttpResponse.json(page(niches, new URL(request.url)) satisfies S["NichePage"]);
  }),
  http.get(`${API}/clusters`, ({ request }) => {
    const who = guard(request);
    if (who instanceof Response) return who;
    const items = clusters.map((c) => ({ ...c, channels_count: channels.filter((ch) => ch.cluster === c.id).length }));
    return HttpResponse.json({ items } satisfies S["ClusterList"]);
  }),
  http.post(`${API}/clusters`, async ({ request }) => {
    const who = guard(request, OPERATORS);
    if (who instanceof Response) return who;
    const body = (await request.json()) as S["ClusterCreate"];
    if (!SLUG_RE.test(body.id)) return invalid(["id"], "Недопустимый id куста", "string_pattern_mismatch");
    if (clusters.some((c) => c.id === body.id)) return error(409, "cluster_exists", "Такой куст уже есть");
    const cluster: S["Cluster"] = {
      id: body.id,
      title: body.title,
      notes: body.notes ?? null,
      youtube_api_key_secret: null,
      youtube_oauth_client_secret: null,
      channels_count: 0,
      created_at: nowIso(),
    };
    clusters.push(cluster);
    return HttpResponse.json(cluster, { status: 201 });
  }),

  // ---------- каналы
  http.get(`${API}/channels`, ({ request }) => {
    const who = guard(request);
    if (who instanceof Response) return who;
    const url = new URL(request.url);
    const platform = url.searchParams.get("platform");
    const statuses = url.searchParams.getAll("status");
    const nicheId = url.searchParams.get("niche_id");
    const clusterId = url.searchParams.get("cluster_id");
    const items = channels
      .filter(
        (c) =>
          (!platform || c.platform === platform) &&
          (!statuses.length || statuses.includes(c.status)) &&
          (!nicheId || c.niche_id === nicheId) &&
          (!clusterId || c.cluster === clusterId),
      )
      .map(channelOut);
    return HttpResponse.json(page(items, url) satisfies S["ChannelPage"]);
  }),
  http.post(`${API}/channels`, async ({ request }) => {
    const who = guard(request, OPERATORS);
    if (who instanceof Response) return who;
    const body = (await request.json()) as S["ChannelCreate"];
    if (!SLUG_RE.test(body.id)) return invalid(["id"], "Недопустимый id канала", "string_pattern_mismatch");
    if (channels.some((c) => c.id === body.id)) return error(409, "channel_exists", "Такой канал уже есть");
    if (!niches.some((n) => n.id === body.niche_id)) return invalid(["niche_id"], "Ниша не найдена", "not_found");
    if (!clusters.some((c) => c.id === body.cluster)) return invalid(["cluster"], "Куст не найден", "not_found");
    const channel: S["Channel"] = {
      id: body.id,
      platform: body.platform,
      niche_id: body.niche_id,
      cluster: body.cluster,
      title: body.title ?? null,
      platform_channel_ref: body.platform_channel_ref ?? null,
      url: body.url ?? null,
      status: body.status ?? "active",
      replaced_by: null,
      default_product_id: body.default_product_id ?? null,
      profile: body.profile ?? {},
      publishing_plan: body.publishing_plan ?? {},
      advanced_features: body.advanced_features ?? false,
      subscribers: null,
      notes: body.notes ?? null,
      profile_duplicate_of: [],
      created_at: nowIso(),
      updated_at: nowIso(),
    };
    channels.push(channel);
    return HttpResponse.json(channelOut(channel), { status: 201 });
  }),
  http.get(`${API}/channels/:channelId/health`, ({ request, params }) => {
    const who = guard(request);
    if (who instanceof Response) return who;
    const channel = channels.find((c) => c.id === String(params.channelId));
    if (!channel) return error(404, "channel_not_found", "Канал не найден");
    return HttpResponse.json({
      status: channel.status,
      last_publications: [],
      removed_count_30d: 0,
      sanction_signals: channel.id === "taro_main" ? ["reach_drop"] : [],
      views_trend_7d: channel.id === "taro_main" ? 0.42 : null,
      below_p25_streak: channel.id === "taro_main" ? 4 : 0,
      shorts_feed_share: null,
    } satisfies S["ChannelHealth"]);
  }),
  http.get(`${API}/channels/:channelId/incidents`, ({ request, params }) => {
    const who = guard(request);
    if (who instanceof Response) return who;
    const id = String(params.channelId);
    const items = incidents
      .filter((i) => i.channel_id === id)
      .sort((a, b) => b.occurred_at.localeCompare(a.occurred_at));
    return HttpResponse.json({ items } satisfies S["ChannelIncidentList"]);
  }),
  http.post(`${API}/channels/:channelId/incidents`, async ({ request, params }) => {
    const who = guard(request, OPERATORS);
    if (who instanceof Response) return who;
    const id = String(params.channelId);
    if (!channels.some((c) => c.id === id)) return error(404, "channel_not_found", "Канал не найден");
    const body = (await request.json()) as S["ChannelIncidentCreate"];
    const incident: S["ChannelIncident"] = {
      id: nextIncidentId++,
      channel_id: id,
      kind: body.kind,
      occurred_at: body.occurred_at ?? nowIso(),
      details: body.details ?? {},
      note: body.note ?? null,
      created_by: who.userId,
    };
    incidents.push(incident);
    return HttpResponse.json(incident, { status: 201 });
  }),
  http.get(`${API}/channels/:channelId`, ({ request, params }) => {
    const who = guard(request);
    if (who instanceof Response) return who;
    const channel = channels.find((c) => c.id === String(params.channelId));
    return channel ? HttpResponse.json(channelOut(channel)) : error(404, "channel_not_found", "Канал не найден");
  }),
  http.patch(`${API}/channels/:channelId`, async ({ request, params }) => {
    const who = guard(request, OPERATORS);
    if (who instanceof Response) return who;
    const channel = channels.find((c) => c.id === String(params.channelId));
    if (!channel) return error(404, "channel_not_found", "Канал не найден");
    const body = (await request.json()) as S["ChannelPatch"];
    if (body.cluster && !clusters.some((c) => c.id === body.cluster))
      return invalid(["cluster"], "Куст не найден", "not_found");
    for (const [key, value] of Object.entries(body)) {
      if (value !== undefined) (channel as Record<string, unknown>)[key] = value;
    }
    channel.updated_at = nowIso();
    return HttpResponse.json(channelOut(channel));
  }),

  // ---------- провайдеры: правка конфига (список, проверка и секреты — моки M1)
  http.put(`${API}/providers/:providerKey`, async ({ request, params }) => {
    const who = guard(request, ["admin"]);
    if (who instanceof Response) return who;
    const provider = providers.find((item) => item.key === String(params.providerKey));
    if (!provider) return error(404, "provider_not_found", "Провайдер не найден");
    const body = (await request.json()) as S["ProviderUpdate"];
    if (body.is_default) {
      for (const other of providers) if (other.kind === provider.kind) other.is_default = false;
    }
    Object.assign(provider, {
      enabled: body.enabled,
      is_default: body.is_default,
      params: body.params ?? provider.params,
      secret_name: body.secret_name ?? null,
    });
    return HttpResponse.json(provider);
  }),
];
