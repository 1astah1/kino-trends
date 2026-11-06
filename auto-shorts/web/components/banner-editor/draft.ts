/**
 * Черновик варианта в редакторе (`BannerVariantDraft` один в один) и чистые операции над ним.
 *
 * openapi-typescript 7 делает поля с дефолтом обязательными в TS, поэтому новые слои строятся со
 * всеми полями явно — значения повторяют `domain/banner_schema.default_banner_variant` сервера.
 * Как правило, слои приходят от сервера целиком (вариант создаётся из креатива на сервере).
 */
import type { Schemas } from "@/lib/api/client";
import type { ApiVariant } from "@/lib/queries/campaigns";
import { normalizeTiming } from "@/lib/queries/campaigns";
import { type Box, CANVAS_H, CANVAS_W, clamp, type CreativeSize, fitIntoSlot } from "@/lib/safe-zones";

export type BannerLayer = Schemas["BannerLayer"];
export type BannerSlot = Schemas["BannerSlot"];
export type BannerTiming = Schemas["BannerTiming"];
export type AdMarking = Schemas["AdMarking"];
export type TextStyle = Schemas["TextStyle"];
export type LayerKind = Schemas["BannerLayerKind"];

/** Черновик: как `BannerVariantDraft`, но тайминг и маркировка всегда заданы. */
export interface EditorDraft {
  code: string;
  name: string;
  layers: BannerLayer[];
  timing: BannerTiming;
  ad_marking: AdMarking;
  code_url_template: string | null;
}

export type EditorDocKind = "variant" | "template";

/** Что редактируется: вариант кампании или шаблон (пресет варианта). */
export interface EditorDoc {
  kind: EditorDocKind;
  id: number;
  version: number;
  campaignId: number | null;
  productId: string | null;
  status: string;
  weight: number | null;
  /** Как печатается код ролика по шаблону продукта: «u.umka.chat/{code}». */
  effectiveCodeUrlTemplate: string;
}

export const DEFAULT_TIMING: BannerTiming = {
  mode: "impulse",
  hook_silence_sec: 2,
  impulse_window_pct: [0.4, 0.65],
  impulse_duration_sec: 4,
  impulse_min_sec: 3,
  fade_in_ms: 200,
  fade_out_ms: 200,
  final_repeat: true,
  final_repeat_sec: 2.5,
  merge_gap_sec: 1,
  persistent_fade_in_ms: 0,
  windows: [],
};

export const DEFAULT_AD_MARKING: AdMarking = { required: false, erid: null, advertiser_text: null };

export const DEFAULT_TEXT_STYLE: TextStyle = {
  font_file: "Montserrat-Bold.ttf",
  max_font_px: 44,
  min_font_px: 24,
  color: "#FFFFFF",
  text_opacity: 1,
  bg_color: "#000000",
  bg_opacity: 0.55,
  padding_x: 16,
  padding_y: 8,
  corner_radius_px: 10,
  align: "center",
  max_lines: 1,
  line_spacing: 1.12,
  stroke_px: 0,
  stroke_color: "#000000",
  hug_text: true,
};

/** Стиль по виду слоя, если у слоя `style = null` (`DEFAULT_TEXT_STYLES` в banner_overlay.py). */
export function effectiveTextStyle(layer: BannerLayer): TextStyle {
  if (layer.style) return layer.style;
  switch (layer.kind) {
    case "text_erid":
      return {
        ...DEFAULT_TEXT_STYLE,
        font_file: "Montserrat-SemiBold.ttf",
        max_font_px: 24,
        min_font_px: 18,
        bg_opacity: 0.45,
      };
    case "micro_brand":
      return {
        ...DEFAULT_TEXT_STYLE,
        font_file: "Montserrat-SemiBold.ttf",
        max_font_px: 44,
        min_font_px: 36,
        bg_opacity: 0.35,
      };
    case "fallback_plate":
      return {
        ...DEFAULT_TEXT_STYLE,
        max_font_px: 66,
        min_font_px: 44,
        bg_opacity: 0.7,
        max_lines: 2,
        corner_radius_px: 14,
        hug_text: false,
      };
    default:
      return { ...DEFAULT_TEXT_STYLE, max_font_px: 36, min_font_px: 24 };
  }
}

export function makeSlot(partial: Partial<BannerSlot> & Pick<BannerSlot, "x" | "y" | "w" | "h">): BannerSlot {
  return {
    anchor: "center",
    fit: "contain",
    scale: 1,
    offset_x: 0,
    offset_y: 0,
    opacity: 1,
    corner_radius_px: 0,
    shadow: null,
    clip_to_slot: true,
    ...partial,
  };
}

/** Зона оффера по умолчанию (research §7.2): x 65–890, y 1120–1310, снизу по центру. */
export const DEFAULT_OFFER_SLOT: BannerSlot = makeSlot({ x: 65, y: 1120, w: 825, h: 190, anchor: "bottom_center" });

function baseLayer(id: string, kind: LayerKind, slot: BannerSlot): BannerLayer {
  return {
    id,
    kind,
    enabled: true,
    slot,
    show: "with_creative",
    creative_sha256: null,
    text: null,
    style: null,
    attach: "none",
    attach_gap_px: 6,
    loop_override: null,
    z: 0,
  };
}

export function makeCreativeLayer(sha: string, slot: BannerSlot = DEFAULT_OFFER_SLOT, id = "creative"): BannerLayer {
  return { ...baseLayer(id, "creative", slot), creative_sha256: sha, z: 10 };
}

export function makeCodeLayer(): BannerLayer {
  return {
    ...baseLayer("code", "text_code", makeSlot({ x: 65, y: 1316, w: 825, h: 50, anchor: "top_center" })),
    text: "{code_url}",
    attach: "below_creative",
    style: { ...DEFAULT_TEXT_STYLE, max_font_px: 36, min_font_px: 24, bg_opacity: 0.6 },
    z: 20,
  };
}

export function makeEridLayer(): BannerLayer {
  return {
    ...baseLayer("erid", "text_erid", makeSlot({ x: 65, y: 1078, w: 825, h: 38, anchor: "bottom_center" })),
    text: "Реклама · erid {erid}",
    attach: "above_creative",
    style: {
      ...DEFAULT_TEXT_STYLE,
      font_file: "Montserrat-SemiBold.ttf",
      max_font_px: 24,
      min_font_px: 18,
      bg_opacity: 0.45,
      padding_x: 12,
      padding_y: 5,
      corner_radius_px: 8,
      text_opacity: 0.92,
    },
    z: 20,
  };
}

export function makeMicroBrandLayer(text: string): BannerLayer {
  return {
    ...baseLayer(
      "micro_brand",
      "micro_brand",
      makeSlot({ x: 65, y: 200, w: 440, h: 58, anchor: "top_left", opacity: 0.75 }),
    ),
    show: "persistent",
    text,
    style: {
      ...DEFAULT_TEXT_STYLE,
      font_file: "Montserrat-SemiBold.ttf",
      max_font_px: 44,
      min_font_px: 36,
      bg_opacity: 0.35,
      padding_x: 14,
      padding_y: 6,
      align: "left",
    },
  };
}

export function makeFallbackPlateLayer(text = "umka.chat — все нейросети в одном месте"): BannerLayer {
  return {
    ...baseLayer("fallback_plate", "fallback_plate", DEFAULT_OFFER_SLOT),
    text,
    style: {
      ...DEFAULT_TEXT_STYLE,
      max_font_px: 66,
      min_font_px: 44,
      bg_opacity: 0.7,
      padding_x: 28,
      padding_y: 18,
      corner_radius_px: 14,
      max_lines: 2,
      hug_text: false,
    },
    z: 10,
  };
}

type TimingLike = Omit<BannerTiming, "impulse_window_pct"> & { impulse_window_pct: number[] };
type DraftLike = {
  code: string;
  name: string;
  layers: BannerLayer[];
  timing?: TimingLike;
  ad_marking?: AdMarking;
  code_url_template?: string | null;
};

/** Ответ сервера (вариант или `layout` шаблона) → черновик редактора (копия, без общих ссылок). */
export function draftFrom(source: DraftLike): EditorDraft {
  const copy = structuredClone(source);
  return {
    code: copy.code,
    name: copy.name,
    layers: copy.layers,
    timing: copy.timing ? normalizeTiming(copy.timing) : structuredClone(DEFAULT_TIMING),
    ad_marking: copy.ad_marking ?? { ...DEFAULT_AD_MARKING },
    code_url_template: copy.code_url_template ?? null,
  };
}

export function docFromVariant(variant: ApiVariant): EditorDoc {
  return {
    kind: "variant",
    id: variant.id,
    version: variant.version,
    campaignId: variant.campaign_id,
    productId: variant.product_id,
    status: variant.status,
    weight: variant.weight,
    effectiveCodeUrlTemplate: variant.effective_code_url_template,
  };
}

/** Стабильный ключ черновика: сравнение «есть несохранённые изменения» и ключ проверки. */
export function draftKey(draft: EditorDraft | null): string {
  return draft ? JSON.stringify(draft) : "";
}

/** Тело `BannerVariantDraft` для validate/preview. */
export function toApiDraft(draft: EditorDraft): Schemas["BannerVariantDraft"] {
  return {
    code: draft.code,
    name: draft.name,
    layers: draft.layers,
    timing: draft.timing,
    ad_marking: draft.ad_marking,
    code_url_template: draft.code_url_template,
  };
}

// ---------------------------------------------------------------- операции

export function findLayer(draft: EditorDraft, id: string | null | undefined): BannerLayer | undefined {
  return id ? draft.layers.find((layer) => layer.id === id) : undefined;
}

export function firstLayerOf(draft: EditorDraft, kind: LayerKind): BannerLayer | undefined {
  return draft.layers.find((layer) => layer.kind === kind);
}

export function patchLayer(draft: EditorDraft, id: string, patch: Partial<BannerLayer>): EditorDraft {
  return { ...draft, layers: draft.layers.map((layer) => (layer.id === id ? { ...layer, ...patch } : layer)) };
}

export function patchSlot(draft: EditorDraft, id: string, patch: Partial<BannerSlot>): EditorDraft {
  return {
    ...draft,
    layers: draft.layers.map((layer) =>
      layer.id === id ? { ...layer, slot: normalizeSlot({ ...layer.slot, ...patch }) } : layer,
    ),
  };
}

export function patchStyle(draft: EditorDraft, id: string, patch: Partial<TextStyle>): EditorDraft {
  return {
    ...draft,
    layers: draft.layers.map((layer) =>
      layer.id === id ? { ...layer, style: { ...effectiveTextStyle(layer), ...patch } } : layer,
    ),
  };
}

/** Масштаб поверх вписывания: пределы `BannerSlot.scale` схемы (0,05–4). */
export const SLOT_SCALE_MIN = 0.05;
export const SLOT_SCALE_MAX = 4;

/** Целые пиксели и пределы схемы `BannerSlot` (иначе сервер ответит 422). */
export function normalizeSlot(slot: BannerSlot): BannerSlot {
  return {
    ...slot,
    x: Math.round(clamp(slot.x, -CANVAS_W, CANVAS_W * 2)),
    y: Math.round(clamp(slot.y, -CANVAS_H, CANVAS_H * 2)),
    w: Math.round(clamp(slot.w, 1, CANVAS_W * 2)),
    h: Math.round(clamp(slot.h, 1, CANVAS_H * 2)),
    scale: Math.round(clamp(slot.scale, SLOT_SCALE_MIN, SLOT_SCALE_MAX) * 1000) / 1000,
    opacity: Math.round(clamp(slot.opacity, 0, 1) * 100) / 100,
    offset_x: Math.round(clamp(slot.offset_x, -CANVAS_W, CANVAS_W)),
    offset_y: Math.round(clamp(slot.offset_y, -CANVAS_H, CANVAS_H)),
    corner_radius_px: Math.round(clamp(slot.corner_radius_px, 0, 400)),
  };
}

/**
 * Поставить креатив в вариант: заменить sha у креативного слоя (позиция сохраняется) или добавить
 * креативный слой в зону оффера (на место запасной плашки) и выключить плашку.
 */
export function placeCreative(draft: EditorDraft, sha: string): { draft: EditorDraft; layerId: string } {
  const existing = firstLayerOf(draft, "creative");
  if (existing) {
    return { draft: patchLayer(draft, existing.id, { creative_sha256: sha, enabled: true }), layerId: existing.id };
  }
  const plate = firstLayerOf(draft, "fallback_plate");
  const id = uniqueLayerId(draft, "creative");
  const layer = makeCreativeLayer(sha, plate ? { ...plate.slot } : DEFAULT_OFFER_SLOT, id);
  const layers = draft.layers.map((item) => (item.kind === "fallback_plate" ? { ...item, enabled: false } : item));
  return { draft: { ...draft, layers: [...layers, layer] }, layerId: id };
}

/** Служебный слой вида `kind`: есть — вернуть, нет — добавить с настройками по умолчанию. */
export function ensureServiceLayer(
  draft: EditorDraft,
  kind: "text_code" | "text_erid" | "micro_brand" | "fallback_plate",
  microBrandText = "umka.chat",
): { draft: EditorDraft; layer: BannerLayer } {
  const existing = firstLayerOf(draft, kind);
  if (existing) return { draft, layer: existing };
  let layer: BannerLayer;
  if (kind === "text_code") layer = makeCodeLayer();
  else if (kind === "text_erid") layer = makeEridLayer();
  else if (kind === "micro_brand") layer = makeMicroBrandLayer(microBrandText);
  else layer = { ...makeFallbackPlateLayer(), enabled: false };
  layer = { ...layer, id: uniqueLayerId(draft, layer.id) };
  return { draft: { ...draft, layers: [...draft.layers, layer] }, layer };
}

export function uniqueLayerId(draft: EditorDraft, base: string): string {
  const taken = new Set(draft.layers.map((layer) => layer.id));
  if (!taken.has(base)) return base;
  for (let i = 2; i < 100; i += 1) if (!taken.has(`${base}_${i}`)) return `${base}_${i}`;
  return `${base}_${Date.now()}`;
}

/**
 * Масштаб «родной размер» креатива в слоте: при каком `scale` креатив ляжет 1:1 (без увеличения).
 * Кнопка «1:1» инспектора; для contain — `min(src/slot)`.
 */
export function nativeScale(slot: BannerSlot, size: CreativeSize): number {
  const fit = fitIntoSlot(size.width, size.height, { ...slot, scale: 1 });
  return fit.scale > 0 ? Math.round((1 / fit.scale) * 1000) / 1000 : 1;
}

/** Подогнать слот под пропорции креатива (слот = видимая часть креатива при scale 1). */
export function slotHugCreative(slot: BannerSlot, size: CreativeSize): BannerSlot {
  const fit = fitIntoSlot(size.width, size.height, { ...slot, scale: 1, offset_x: 0, offset_y: 0 });
  const box: Box = fit.full;
  return normalizeSlot({ ...slot, x: box.x, y: box.y, w: box.w, h: box.h, scale: 1, offset_x: 0, offset_y: 0 });
}

// ---------------------------------------------------------------- текст

export const SAMPLE_VIDEO_CODE = "k7m2q";

export interface TextContext {
  codeUrlTemplate: string;
  erid: string | null | undefined;
  advertiser: string | null | undefined;
  code?: string;
}

/** Подстановка `{code_url}`, `{code}`, `{erid}`, `{advertiser}` — как увидит зритель. */
export function renderLayerText(template: string | null | undefined, ctx: TextContext): string {
  if (!template) return "";
  const code = ctx.code ?? SAMPLE_VIDEO_CODE;
  const codeUrl = ctx.codeUrlTemplate.replaceAll("{code}", code);
  return template
    .replaceAll("{code_url}", codeUrl)
    .replaceAll("{code}", code)
    .replaceAll("{erid}", ctx.erid || "—")
    .replaceAll("{advertiser}", ctx.advertiser || "");
}

/** Какой шаблон кода печатается: свой у варианта или продукта. */
export function effectiveCodeUrl(draft: EditorDraft, doc: EditorDoc | null): string {
  return draft.code_url_template || doc?.effectiveCodeUrlTemplate || "u.umka.chat/{code}";
}

/** Латиница, цифры и `-_`: ERID от ОРД. */
export const ERID_PATTERN = /^[A-Za-z0-9_-]{4,64}$/;
