/**
 * Геометрия баннера и безопасные зоны площадок для редактора (docs/ARCHITECTURE.md §7.3, решение
 * владельца от 26.09.2026: баннер — загруженный креатив).
 *
 * Модуль чистый (без React и DOM): его можно проверять отдельно. Правила повторяют сервер
 * один в один, чтобы рамка на холсте не прыгала, пока не пришла серверная проверка:
 * - `fitIntoSlot` / `attachedSlot` / `placeVariant` — `src/autoshorts/pipeline/banner_place.py`;
 * - `safeZoneOverflow` — `check_safe_zone`; `clampSlotIntoSafe` — `clamp_slot_into_safe_zone`;
 * - `computeWindows` — `pipeline/banner_overlay.compute_windows`.
 *
 * Источник истины всё равно сервер (`POST /banner-variants/validate` и «Кадр из рендера»): здесь только
 * мгновенный отклик на перетаскивание. Профили safe zones приходят из `GET /banner-safe-zones`
 * (config/safe_zones.json), координаты — пиксели канвы 1080×1920, края `x1,y1,x2,y2`.
 */
import type { Schemas } from "@/lib/api/client";

export type Rect = Schemas["Rect"];
export type BannerSlot = Schemas["BannerSlot"];
export type BannerLayer = Schemas["BannerLayer"];
export type BannerTiming = Schemas["BannerTiming"];
export type Anchor = Schemas["Anchor"];
export type Platform = Schemas["Platform"];
export type SafeZoneProfile = Schemas["BannerSafeZoneProfile"];
export type SafeZoneCatalog = Schemas["BannerSafeZoneCatalog"];

export const CANVAS_W = 1080;
export const CANVAS_H = 1920;

/** Прямоугольник «левый верхний угол + размер» — так хранится слот (`BannerSlot.x,y,w,h`). */
export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Доли свободного места слота по якорю (`Anchor.fx/fy` в domain/banner_schema.py). */
export const ANCHOR_FACTORS: Record<Anchor, readonly [number, number]> = {
  top_left: [0, 0],
  top_center: [0.5, 0],
  top_right: [1, 0],
  center_left: [0, 0.5],
  center: [0.5, 0.5],
  center_right: [1, 0.5],
  bottom_left: [0, 1],
  bottom_center: [0.5, 1],
  bottom_right: [1, 1],
};

/** Якоря в порядке сетки 3×3 (слева направо, сверху вниз). */
export const ANCHOR_GRID: readonly Anchor[] = [
  "top_left",
  "top_center",
  "top_right",
  "center_left",
  "center",
  "center_right",
  "bottom_left",
  "bottom_center",
  "bottom_right",
];

// Python round() — банковское округление; для пикселей редактора расхождение на .5 не критично,
// но floor(x + 0.5) сервер использует там, где важно, и здесь то же самое.
const half = (value: number) => Math.floor(value + 0.5);

export function slotBox(slot: Pick<BannerSlot, "x" | "y" | "w" | "h">): Box {
  return { x: slot.x, y: slot.y, w: slot.w, h: slot.h };
}

export function boxToRect(box: Box): Rect {
  return { x1: box.x, y1: box.y, x2: box.x + box.w, y2: box.y + box.h };
}

export function rectToBox(rect: Rect): Box {
  return { x: rect.x1, y: rect.y1, w: Math.max(0, rect.x2 - rect.x1), h: Math.max(0, rect.y2 - rect.y1) };
}

export function isEmptyBox(box: Box | null | undefined): boolean {
  return !box || box.w <= 0 || box.h <= 0;
}

export function intersectBoxes(a: Box, b: Box): Box | null {
  const x1 = Math.max(a.x, b.x);
  const y1 = Math.max(a.y, b.y);
  const x2 = Math.min(a.x + a.w, b.x + b.w);
  const y2 = Math.min(a.y + a.h, b.y + b.h);
  if (x2 <= x1 || y2 <= y1) return null;
  return { x: x1, y: y1, w: x2 - x1, h: y2 - y1 };
}

export function sameBox(a: Box | null, b: Box | null): boolean {
  if (!a || !b) return a === b;
  return a.x === b.x && a.y === b.y && a.w === b.w && a.h === b.h;
}

const CANVAS_BOX: Box = { x: 0, y: 0, w: CANVAS_W, h: CANVAS_H };

export interface FitResult {
  /** Куда лёг бы креатив целиком (может выходить за слот и кадр). */
  full: Box;
  /** Видимая часть на канве (после обрезки слотом и кадром); `null` — не видно. */
  visible: Box | null;
  /** Пикселей канвы на пиксель нормализованного креатива. */
  scale: number;
  clipped: boolean;
  canvasClipped: boolean;
}

function scaledSize(srcW: number, srcH: number, s: number, widthLimited: boolean): [number, number] {
  if (widthLimited) {
    const w = Math.max(1, Math.round(srcW * s));
    return [w, Math.max(1, Math.round((w * srcH) / srcW))];
  }
  const h = Math.max(1, Math.round(srcH * s));
  return [Math.max(1, Math.round((h * srcW) / srcH)), h];
}

/** Вписать креатив `srcW×srcH` в слот: contain / cover / none × scale, якорь, сдвиг, обрезка. */
export function fitIntoSlot(srcW: number, srcH: number, slot: BannerSlot): FitResult {
  const kx = slot.w / srcW;
  const ky = slot.h / srcH;
  let base: number;
  let widthLimited: boolean;
  if (slot.fit === "contain") {
    base = Math.min(kx, ky);
    widthLimited = kx <= ky;
  } else if (slot.fit === "cover") {
    base = Math.max(kx, ky);
    widthLimited = kx >= ky;
  } else {
    base = 1;
    widthLimited = srcW >= srcH;
  }
  let [w, h] = scaledSize(srcW, srcH, base * slot.scale, widthLimited);
  if (slot.fit === "contain" && slot.scale <= 1 && (w > slot.w || h > slot.h)) {
    [w, h] = scaledSize(srcW, srcH, Math.min(slot.w / srcW, slot.h / srcH) * slot.scale * 0.999, widthLimited);
  }
  const [fx, fy] = ANCHOR_FACTORS[slot.anchor] ?? ANCHOR_FACTORS.center;
  const full: Box = {
    x: half(slot.x + (slot.w - w) * fx + slot.offset_x),
    y: half(slot.y + (slot.h - h) * fy + slot.offset_y),
    w,
    h,
  };
  let visible: Box | null = full;
  if (slot.clip_to_slot) visible = intersectBoxes(full, slotBox(slot));
  let canvasClipped = false;
  if (visible) {
    const inCanvas = intersectBoxes(visible, CANVAS_BOX);
    canvasClipped = !sameBox(inCanvas, visible);
    visible = inCanvas;
  }
  return { full, visible, scale: w / srcW, clipped: !sameBox(visible, full), canvasClipped };
}

/** Прямоугольник готового содержимого внутри слота по якорю (`anchor_in_slot`). */
export function anchorInSlot(contentW: number, contentH: number, slot: BannerSlot): Box {
  const [fx, fy] = ANCHOR_FACTORS[slot.anchor] ?? ANCHOR_FACTORS.center;
  return {
    x: half(slot.x + (slot.w - contentW) * fx + slot.offset_x),
    y: half(slot.y + (slot.h - contentH) * fy + slot.offset_y),
    w: contentW,
    h: contentH,
  };
}

/** Слот служебного текста относительно итогового прямоугольника креатива (`attached_slot`). */
export function attachedSlot(layer: BannerLayer, anchorBox: Box): BannerSlot {
  const slot = layer.slot;
  const gap = layer.attach_gap_px;
  const inside = layer.attach === "inside_bottom" || layer.attach === "inside_top";
  const w = inside ? Math.min(slot.w, anchorBox.w) : slot.w;
  const [fx] = ANCHOR_FACTORS[slot.anchor] ?? ANCHOR_FACTORS.center;
  const x = anchorBox.x + (anchorBox.w - w) * fx;
  let y: number;
  if (layer.attach === "below_creative") y = anchorBox.y + anchorBox.h + gap;
  else if (layer.attach === "above_creative") y = anchorBox.y - gap - slot.h;
  else if (layer.attach === "inside_bottom") y = anchorBox.y + anchorBox.h - gap - slot.h;
  else y = anchorBox.y + gap;
  return { ...slot, x: half(x), y: Math.trunc(y), w: Math.max(1, Math.trunc(w)) };
}

export interface CreativeSize {
  width: number;
  height: number;
}

export interface LocalPlacement {
  layerId: string;
  kind: BannerLayer["kind"];
  show: BannerLayer["show"];
  /** Итоговый слот (у прикреплённого текста — пересчитанный от креатива). */
  slot: BannerSlot;
  /** Видимая часть (у текста до рендера — весь слот). */
  rect: Box | null;
  /** Креатив целиком до обрезки. */
  full: Box | null;
  creativeSha256: string | null;
  scale: number | null;
  z: number;
}

export interface LocalLayout {
  placements: LocalPlacement[];
  /** Слой в зоне оффера: первый включённый креатив с известным файлом, иначе запасная плашка. */
  offerLayerId: string | null;
  fallbackUsed: boolean;
  missingCreatives: string[];
}

/**
 * Раскладка слоёв без отрисовки текста (`place_variant` без `text_sizes`): как в серверной проверке.
 * `creatives` — размеры нормализованных креативов по sha256 (только готовые).
 */
export function placeVariant(layers: readonly BannerLayer[], creatives: Record<string, CreativeSize>): LocalLayout {
  const missing: string[] = [];
  let offer: BannerLayer | null = null;
  let fallbackUsed = false;
  const creativeLayers = layers.filter((layer) => layer.kind === "creative" && layer.enabled);
  for (const layer of creativeLayers) {
    if (layer.creative_sha256 && creatives[layer.creative_sha256]) {
      offer = layer;
      break;
    }
    if (layer.creative_sha256) missing.push(layer.creative_sha256);
  }
  if (!offer) {
    const plates = layers.filter(
      (layer) => layer.kind === "fallback_plate" && (creativeLayers.length > 0 || layer.enabled),
    );
    if (plates.length) {
      offer = plates[0];
      fallbackUsed = true;
    }
  }

  const placements: LocalPlacement[] = [];
  let offerBox: Box | null = null;
  if (offer) {
    if (offer.kind === "creative" && offer.creative_sha256) {
      const size = creatives[offer.creative_sha256];
      const fit = fitIntoSlot(size.width, size.height, offer.slot);
      offerBox = fit.visible;
      placements.push({
        layerId: offer.id,
        kind: offer.kind,
        show: offer.show,
        slot: offer.slot,
        rect: fit.visible,
        full: fit.full,
        creativeSha256: offer.creative_sha256,
        scale: fit.scale,
        z: offer.z,
      });
    } else {
      offerBox = slotBox(offer.slot);
      placements.push({
        layerId: offer.id,
        kind: offer.kind,
        show: offer.show,
        slot: offer.slot,
        rect: offerBox,
        full: null,
        creativeSha256: null,
        scale: null,
        z: offer.z,
      });
    }
  }

  for (const layer of layers) {
    if (!layer.enabled || layer.kind === "creative" || layer.kind === "fallback_plate") continue;
    if (layer.kind === "micro_brand" && layer.creative_sha256) {
      const size = creatives[layer.creative_sha256];
      if (size) {
        const fit = fitIntoSlot(size.width, size.height, layer.slot);
        placements.push({
          layerId: layer.id,
          kind: layer.kind,
          show: layer.show,
          slot: layer.slot,
          rect: fit.visible,
          full: fit.full,
          creativeSha256: layer.creative_sha256,
          scale: fit.scale,
          z: layer.z,
        });
        continue;
      }
      missing.push(layer.creative_sha256);
      if (!layer.text) continue;
    }
    const slot =
      layer.attach !== "none" && offerBox && !isEmptyBox(offerBox) ? attachedSlot(layer, offerBox) : layer.slot;
    placements.push({
      layerId: layer.id,
      kind: layer.kind,
      show: layer.show,
      slot,
      rect: slotBox(slot),
      full: null,
      creativeSha256: null,
      scale: null,
      z: layer.z,
    });
  }
  placements.sort((a, b) => a.z - b.z);
  return { placements, offerLayerId: offer?.id ?? null, fallbackUsed, missingCreatives: missing };
}

// ---------------------------------------------------------------- safe zones

export type Side = "top" | "bottom" | "left" | "right";

/** На сколько пикселей прямоугольник выходит за безопасную зону профиля (`check_safe_zone`). */
export function safeZoneOverflow(box: Box, profile: Pick<SafeZoneProfile, "safe">): Record<Side, number> {
  const safe = profile.safe;
  return {
    top: Math.max(0, safe.y1 - box.y),
    bottom: Math.max(0, box.y + box.h - safe.y2),
    left: Math.max(0, safe.x1 - box.x),
    right: Math.max(0, box.x + box.w - safe.x2),
  };
}

export function isInsideSafe(box: Box, profile: Pick<SafeZoneProfile, "safe">): boolean {
  const overflow = safeZoneOverflow(box, profile);
  return Object.values(overflow).every((value) => value === 0);
}

/** Слот целиком внутрь safe zone: сдвиг, а если не влезает — пропорциональное уменьшение. */
/**
 * Пересечение безопасных зон нескольких профилей: «Вписать в безопасную зону» должно
 * удовлетворить все проверяемые профили сразу (профиль площадки и общий common_organic).
 */
export function intersectSafe(
  profiles: ReadonlyArray<Pick<SafeZoneProfile, "safe"> | undefined>,
): Pick<SafeZoneProfile, "safe"> | undefined {
  const list = profiles.filter((profile): profile is Pick<SafeZoneProfile, "safe"> => !!profile);
  if (!list.length) return undefined;
  const safe = list.reduce<Rect>(
    (acc, profile) => ({
      x1: Math.max(acc.x1, profile.safe.x1),
      y1: Math.max(acc.y1, profile.safe.y1),
      x2: Math.min(acc.x2, profile.safe.x2),
      y2: Math.min(acc.y2, profile.safe.y2),
    }),
    { x1: 0, y1: 0, x2: CANVAS_W, y2: CANVAS_H },
  );
  return safe.x2 > safe.x1 && safe.y2 > safe.y1 ? { safe } : list[0];
}

export function clampSlotIntoSafe<S extends Pick<BannerSlot, "x" | "y" | "w" | "h">>(
  slot: S,
  profile: Pick<SafeZoneProfile, "safe">,
): S {
  const safe = rectToBox(profile.safe);
  let { w, h } = slot;
  if (w > safe.w || h > safe.h) {
    const s = Math.min(safe.w / w, safe.h / h);
    w = Math.max(1, Math.floor(w * s));
    h = Math.max(1, Math.floor(h * s));
  }
  const x = Math.min(Math.max(slot.x, safe.x), safe.x + safe.w - w);
  const y = Math.min(Math.max(slot.y, safe.y), safe.y + safe.h - h);
  if (x === slot.x && y === slot.y && w === slot.w && h === slot.h) return slot;
  return { ...slot, x, y, w, h };
}

/** Площадки редактора: YouTube Shorts — основная, VK Клипы — второй контур, «Общий» — пересечение. */
export type PlatformView = "youtube" | "vk" | "common";

export const PLATFORM_VIEWS: ReadonlyArray<{ id: PlatformView; label: string; short: string; platform: Platform }> = [
  { id: "youtube", label: "YouTube Shorts", short: "YouTube", platform: "youtube" },
  { id: "vk", label: "VK Клипы", short: "VK", platform: "vk" },
  { id: "common", label: "Общий профиль", short: "Общий", platform: "youtube" },
];

export function isPlatformView(value: string | null | undefined): value is PlatformView {
  return value === "youtube" || value === "vk" || value === "common";
}

/** Основной профиль для оверлея: площадки из каталога (`platform_profiles`), для «Общего» — `default_profile`. */
export function primaryProfileId(view: PlatformView, catalog: SafeZoneCatalog | undefined): string {
  const fallback = view === "vk" ? "vk_clips" : view === "youtube" ? "youtube_shorts" : "common_organic";
  if (!catalog) return fallback;
  if (view === "common") return catalog.default_profile;
  const platform = PLATFORM_VIEWS.find((item) => item.id === view)?.platform;
  return catalog.platform_profiles.find((item) => item.platform === platform)?.profile_id ?? fallback;
}

/**
 * `profile_ids` для проверки: пусто — сервер сам берёт профиль площадки и `common_organic`.
 * Для «Общего» проверяем только общий профиль.
 */
export function validationProfileIds(view: PlatformView, catalog: SafeZoneCatalog | undefined): string[] {
  return view === "common" ? [catalog?.default_profile ?? "common_organic"] : [];
}

export function findProfile(catalog: SafeZoneCatalog | undefined, id: string): SafeZoneProfile | undefined {
  return catalog?.items.find((item) => item.id === id);
}

/** Подписи мёртвых зон (`DeadZone.id`, считает сервер вокруг `safe`). */
export const DEAD_ZONE_LABELS: Record<string, string> = {
  top_bar: "Верхняя панель",
  bottom_ui: "Подпись, кнопки и прогресс",
  left_margin: "Левое поле",
  right_rail: "Правая колонка: лайки, комментарии",
};

export function deadZoneLabel(id: string): string {
  return DEAD_ZONE_LABELS[id] ?? id;
}

// ---------------------------------------------------------------- окна показа

export interface TimeSpan {
  start_sec: number;
  end_sec: number;
}

const MIN_WINDOW_SEC = 0.05;

function mergeSpans(spans: Array<[number, number]>, gap: number): Array<[number, number]> {
  const out: Array<[number, number]> = [];
  for (const [s, e] of [...spans].sort((a, b) => a[0] - b[0] || a[1] - b[1])) {
    const last = out[out.length - 1];
    if (last && s - last[1] <= gap) last[1] = Math.max(last[1], e);
    else out.push([s, e]);
  }
  return out;
}

const round3 = (value: number) => Math.round(value * 1000) / 1000;

/** Окна показа группы `with_creative` (импульс + финальный повтор) для ролика длиной `duration`. */
export function computeWindows(timing: BannerTiming, duration: number): { windows: TimeSpan[]; stretched: boolean } {
  const d = Number(duration);
  let stretched = false;
  if (!(d > MIN_WINDOW_SEC)) return { windows: [], stretched };
  if (timing.mode === "persistent") return { windows: [{ start_sec: 0, end_sec: round3(d) }], stretched };
  const raw: Array<[number, number]> = [];
  if (timing.mode === "windows") {
    for (const w of timing.windows ?? []) {
      const s = Math.max(0, w.start_sec);
      const e = Math.min(d, w.end_sec);
      if (e - s >= MIN_WINDOW_SEC) raw.push([s, e]);
    }
  } else {
    const [lo, hi] = timing.impulse_window_pct;
    let start = Math.max(timing.hook_silence_sec, d * lo);
    if (start >= d - MIN_WINDOW_SEC) {
      start = Math.max(0, d - timing.impulse_min_sec);
      stretched = true;
    }
    let end = Math.min(start + timing.impulse_duration_sec, d * hi);
    if (end - start < timing.impulse_min_sec) {
      end = Math.min(start + timing.impulse_min_sec, d);
      stretched = true;
    }
    if (end - start >= MIN_WINDOW_SEC) raw.push([start, end]);
    if (timing.final_repeat) {
      const finalStart = Math.max(d - timing.final_repeat_sec, Math.min(timing.hook_silence_sec, start), 0);
      if (d - finalStart >= MIN_WINDOW_SEC) raw.push([finalStart, d]);
    }
  }
  const merged = mergeSpans(raw, timing.mode === "impulse" ? timing.merge_gap_sec : 0);
  return { windows: merged.map(([s, e]) => ({ start_sec: round3(s), end_sec: round3(e) })), stretched };
}

/** Виден ли слой группы `show` в момент `t`. */
export function isVisibleAt(show: BannerLayer["show"], windows: readonly TimeSpan[], t: number): boolean {
  if (show === "persistent") return true;
  return windows.some((w) => t >= w.start_sec && t <= w.end_sec);
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}
