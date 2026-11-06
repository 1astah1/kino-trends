/**
 * Чистая геометрия редактора раскладки (канва 1080×1920): черновик переопределения → прямоугольники
 * и пересечения. Зеркало `clipping/layout_override.py`: блок баннера масштабируется вокруг своего
 * центра, сдвигается и вписывается в safe zone; субтитры ставятся низом, заголовок и тезисы — верхом.
 * После сохранения экран показывает расчёт сервера (`effective`), черновик — только предпросмотр.
 */
import type { JobLayout, LayoutConflict, LayoutOverride, LayoutRect, LayoutSnapshot, Rect } from "@/lib/queries/layout";

export const CANVAS_W = 1080;
export const CANVAS_H = 1920;
export const SCALE_MIN = 0.5;
export const SCALE_MAX = 1.5;
export const SCALE_STEP = 0.05;
/** Зазор между блоком баннера и соседней зоной в пресетах. */
const PRESET_GAP = 24;

export type ZoneId = "captions" | "title" | "onscreen";
export type Selection = "banner" | ZoneId;
export const ZONE_IDS: readonly ZoneId[] = ["captions", "title", "onscreen"];
export const SELECTION_LABELS: Record<Selection, string> = {
  banner: "Баннер",
  captions: "Субтитры",
  title: "Заголовок",
  onscreen: "Тезисы",
};

/** Черновик переопределения: все поля заданы, чтобы сервер считал ровно то, что видит оператор. */
export interface Draft {
  mode: "auto" | "manual";
  dx: number;
  dy: number;
  scale: number;
  captions_bottom: number;
  title_top: number;
  onscreen_top: number;
}

export interface DraftLayout {
  rects: LayoutRect[];
  block: Rect | null;
  captions: Rect;
  title: Rect;
  onscreen: Rect;
  conflicts: LayoutConflict[];
}

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
const round = (v: number) => Math.floor(v + 0.5);
export const rectW = (r: Rect) => r.x2 - r.x1;
export const rectH = (r: Rect) => r.y2 - r.y1;

export function sameRect(a: Rect | null | undefined, b: Rect | null | undefined): boolean {
  if (!a || !b) return a === b;
  return a.x1 === b.x1 && a.y1 === b.y1 && a.x2 === b.x2 && a.y2 === b.y2;
}

export function intersectRects(a: Rect, b: Rect): Rect | null {
  const x1 = Math.max(a.x1, b.x1);
  const y1 = Math.max(a.y1, b.y1);
  const x2 = Math.min(a.x2, b.x2);
  const y2 = Math.min(a.y2, b.y2);
  if (x2 <= x1 || y2 <= y1) return null;
  return { x1, y1, x2, y2 };
}

export function unionRects(rects: readonly Rect[]): Rect | null {
  const live = rects.filter((r) => rectW(r) > 0 && rectH(r) > 0);
  if (!live.length) return null;
  return live.reduce((acc, r) => ({
    x1: Math.min(acc.x1, r.x1),
    y1: Math.min(acc.y1, r.y1),
    x2: Math.max(acc.x2, r.x2),
    y2: Math.max(acc.y2, r.y2),
  }));
}

/** Черновик из сохранённого переопределения: недостающие поля — из текущей раскладки сервера. */
export function draftFromLayout(layout: JobLayout): Draft {
  const o: LayoutOverride = layout.override;
  const eff = layout.effective;
  return {
    mode: o.mode ?? "auto",
    dx: o.banner?.dx ?? 0,
    dy: o.banner?.dy ?? 0,
    scale: o.banner?.scale ?? 1,
    captions_bottom: o.captions_bottom ?? eff.captions.y2,
    title_top: o.title_top ?? eff.title.y1,
    onscreen_top: o.onscreen_top ?? eff.onscreen.y1,
  };
}

/** Черновик → тело запроса. В авто-режиме позиции не отправляются. */
export function overrideFromDraft(draft: Draft): LayoutOverride {
  if (draft.mode === "auto")
    return { mode: "auto", banner: null, captions_bottom: null, title_top: null, onscreen_top: null };
  return {
    mode: "manual",
    banner: { dx: round(draft.dx), dy: round(draft.dy), scale: Number(draft.scale.toFixed(3)) },
    captions_bottom: round(draft.captions_bottom),
    title_top: round(draft.title_top),
    onscreen_top: round(draft.onscreen_top),
  };
}

export function sameDraft(a: Draft, b: Draft): boolean {
  if (a.mode !== b.mode) return false;
  if (a.mode === "auto") return true;
  return (
    a.dx === b.dx &&
    a.dy === b.dy &&
    Math.abs(a.scale - b.scale) < 1e-6 &&
    a.captions_bottom === b.captions_bottom &&
    a.title_top === b.title_top &&
    a.onscreen_top === b.onscreen_top
  );
}

function scaleAbout(r: Rect, s: number, cx: number, cy: number): Rect {
  return {
    x1: round(cx + (r.x1 - cx) * s),
    y1: round(cy + (r.y1 - cy) * s),
    x2: round(cx + (r.x2 - cx) * s),
    y2: round(cy + (r.y2 - cy) * s),
  };
}

function shiftRect(r: Rect, dx: number, dy: number): Rect {
  return { x1: r.x1 + dx, y1: r.y1 + dy, x2: r.x2 + dx, y2: r.y2 + dy };
}

/** Блок баннера после сдвига и масштаба, вписанный в safe zone (как `shift_variant` на сервере). */
export function moveBanner(
  base: readonly LayoutRect[],
  baseBlock: Rect | null,
  safe: Rect,
  draft: Pick<Draft, "dx" | "dy" | "scale">,
): { rects: LayoutRect[]; block: Rect | null } {
  const movable = base.filter((r) => r.kind === "banner" && r.movable);
  if (!baseBlock || movable.length === 0) return { rects: [...base], block: baseBlock };
  const s = clamp(draft.scale, 0.25, 2);
  const cx = (baseBlock.x1 + baseBlock.x2) / 2;
  const cy = (baseBlock.y1 + baseBlock.y2) / 2;
  let moved = movable.map((r) => ({ ...r, ...shiftRect(scaleAbout(r, s, cx, cy), round(draft.dx), round(draft.dy)) }));
  let block = unionRects(moved) ?? baseBlock;
  const safeW = rectW(safe);
  const safeH = rectH(safe);
  if (rectW(block) > safeW || rectH(block) > safeH) {
    const k = Math.min(safeW / rectW(block), safeH / rectH(block)) * 0.995;
    const bcx = (block.x1 + block.x2) / 2;
    const bcy = (block.y1 + block.y2) / 2;
    moved = moved.map((r) => ({ ...r, ...scaleAbout(r, k, bcx, bcy) }));
    block = unionRects(moved) ?? block;
  }
  const ddx = clamp(block.x1, safe.x1, safe.x2 - rectW(block)) - block.x1;
  const ddy = clamp(block.y1, safe.y1, safe.y2 - rectH(block)) - block.y1;
  if (ddx !== 0 || ddy !== 0) {
    moved = moved.map((r) => ({ ...r, ...shiftRect(r, ddx, ddy) }));
    block = shiftRect(block, ddx, ddy);
  }
  const byId = new Map(moved.map((r) => [r.id, r]));
  return { rects: base.map((r) => byId.get(r.id) ?? r), block };
}

/** Полоса низом в `bottom` (в пределах safe zone), высота сохраняется. */
export function placeBottom(zone: Rect, bottom: number, safe: Rect): Rect {
  const h = rectH(zone);
  const y2 = h <= rectH(safe) ? clamp(round(bottom), safe.y1 + h, safe.y2) : safe.y2;
  return { x1: zone.x1, y1: y2 - h, x2: zone.x2, y2 };
}

/** Полоса верхом в `top` (в пределах safe zone), высота сохраняется. */
export function placeTop(zone: Rect, top: number, safe: Rect): Rect {
  const h = rectH(zone);
  const y1 = clamp(round(top), safe.y1, Math.max(safe.y1, safe.y2 - h));
  return { x1: zone.x1, y1, x2: zone.x2, y2: y1 + h };
}

/** Пересечения зон текста с баннером и между собой (`layout_conflicts`): глубокие — первыми. */
export function findConflicts(rects: readonly LayoutRect[], skip: ReadonlySet<string> = new Set()): LayoutConflict[] {
  const live = rects.filter((r) => rectW(r) > 0 && rectH(r) > 0 && !skip.has(r.id));
  const out: LayoutConflict[] = [];
  for (let i = 0; i < live.length; i += 1) {
    for (let j = i + 1; j < live.length; j += 1) {
      const a = live[i];
      const b = live[j];
      if (a.kind === "banner" && b.kind === "banner") continue;
      const inter = intersectRects(a, b);
      if (!inter) continue;
      out.push({ a: a.id, b: b.id, overlap_px: Math.min(rectW(inter), rectH(inter)), rect: inter });
    }
  }
  return out.sort((p, q) => q.overlap_px - p.overlap_px || p.a.localeCompare(q.a) || p.b.localeCompare(q.b));
}

function zoneRect(rects: readonly LayoutRect[], id: ZoneId, fallback: Rect): Rect {
  return rects.find((r) => r.id === id) ?? fallback;
}

/** Зоны без текста: в кадре их нет, в пересечения не идут. */
export function emptyZones(layout: JobLayout): Set<string> {
  const out = new Set<string>();
  if (!layout.texts.title) out.add("title");
  if (!layout.texts.onscreen.length) out.add("onscreen");
  return out;
}

/** Предпросмотр черновика от авто-раскладки сервера. */
export function applyDraft(layout: JobLayout, draft: Draft): DraftLayout {
  const base: LayoutSnapshot = layout.auto;
  const skip = emptyZones(layout);
  if (draft.mode === "auto") {
    return {
      rects: base.rects,
      block: base.block,
      captions: base.captions,
      title: base.title,
      onscreen: base.onscreen,
      conflicts: base.conflicts,
    };
  }
  const safe = layout.safe;
  const banner = moveBanner(base.rects, base.block, safe, draft);
  const captions = placeBottom(zoneRect(base.rects, "captions", base.captions), draft.captions_bottom, safe);
  const title = placeTop(zoneRect(base.rects, "title", base.title), draft.title_top, safe);
  const onscreen = placeTop(zoneRect(base.rects, "onscreen", base.onscreen), draft.onscreen_top, safe);
  const zones: Record<ZoneId, Rect> = { captions, title, onscreen };
  const rects = banner.rects.map((r) => (r.kind === "banner" ? r : { ...r, ...zones[r.id as ZoneId] }));
  return { rects, block: banner.block, captions, title, onscreen, conflicts: findConflicts(rects, skip) };
}

/** Шаг клавиатуры/перетаскивания для выбранного элемента. */
export function nudge(draft: Draft, selection: Selection, dx: number, dy: number): Draft {
  const next: Draft = { ...draft, mode: "manual" };
  switch (selection) {
    case "banner":
      next.dx = clamp(draft.dx + dx, -CANVAS_W, CANVAS_W);
      next.dy = clamp(draft.dy + dy, -CANVAS_H, CANVAS_H);
      break;
    case "captions":
      next.captions_bottom = clamp(draft.captions_bottom + dy, 0, CANVAS_H);
      break;
    case "title":
      next.title_top = clamp(draft.title_top + dy, 0, CANVAS_H);
      break;
    case "onscreen":
      next.onscreen_top = clamp(draft.onscreen_top + dy, 0, CANVAS_H);
      break;
  }
  return next;
}

export function setScale(draft: Draft, scale: number): Draft {
  return { ...draft, mode: "manual", scale: clamp(Number(scale.toFixed(3)), SCALE_MIN, SCALE_MAX) };
}

export type PresetId = "bottom" | "top" | "center";
export const PRESETS: ReadonlyArray<{ id: PresetId; label: string; hint: string }> = [
  { id: "bottom", label: "Баннер внизу", hint: "Блок у нижнего края safe zone, субтитры над ним" },
  { id: "top", label: "Баннер вверху", hint: "Блок под микробрендом, текст и субтитры ниже" },
  { id: "center", label: "По центру под речь", hint: "Блок посередине кадра, субтитры над ним" },
];

/** Пресет: сдвиг блока к краю/центру safe zone и зоны текста вокруг него (масштаб — текущий). */
export function applyPreset(layout: JobLayout, draft: Draft, preset: PresetId): Draft {
  const base = layout.auto;
  const safe = layout.safe;
  const scaled = moveBanner(base.rects, base.block, safe, { dx: 0, dy: 0, scale: draft.scale });
  const block = scaled.block;
  const captionsH = rectH(base.captions);
  const titleH = rectH(base.title);
  const onscreenH = rectH(base.onscreen);
  const next: Draft = { ...draft, mode: "manual", dx: 0 };
  if (!block) {
    // без баннера пресеты двигают только текст
    next.captions_bottom = preset === "top" ? safe.y2 - PRESET_GAP : Math.min(1100, safe.y2);
    return next;
  }
  const blockH = rectH(block);
  let targetTop: number;
  if (preset === "bottom") targetTop = safe.y2 - blockH;
  else if (preset === "top") targetTop = (layout.micro_brand_area?.y2 ?? safe.y1) + PRESET_GAP;
  else targetTop = round((safe.y1 + safe.y2) / 2 - blockH / 2);
  targetTop = clamp(targetTop, safe.y1, safe.y2 - blockH);
  next.dy = targetTop - block.y1;
  const moved = { y1: targetTop, y2: targetTop + blockH };
  if (preset === "top") {
    next.title_top = clamp(moved.y2 + PRESET_GAP, safe.y1, safe.y2 - titleH);
    next.onscreen_top = clamp(next.title_top + titleH + 16, safe.y1, safe.y2 - onscreenH);
    next.captions_bottom = safe.y2 - PRESET_GAP;
  } else {
    next.captions_bottom = clamp(moved.y1 - PRESET_GAP, safe.y1 + captionsH, safe.y2);
    next.title_top = base.title.y1;
    next.onscreen_top = base.onscreen.y1;
    if (preset === "center" && next.onscreen_top + onscreenH + PRESET_GAP > next.captions_bottom - captionsH) {
      next.onscreen_top = clamp(next.title_top + titleH + 16, safe.y1, safe.y2 - onscreenH);
    }
  }
  return next;
}

/** Подпись пересечения для списка и для метки на канве: «налезает на Код ролика на 40 px». */
export function conflictLabel(conflict: LayoutConflict, rects: readonly LayoutRect[], subject: string): string | null {
  const other = conflict.a === subject ? conflict.b : conflict.b === subject ? conflict.a : null;
  if (!other) return null;
  const label = rects.find((r) => r.id === other)?.label ?? other;
  return `налезает на ${label.toLowerCase()} на ${conflict.overlap_px} px`;
}

export function conflictIds(conflicts: readonly LayoutConflict[]): Set<string> {
  const out = new Set<string>();
  for (const c of conflicts) {
    out.add(c.a);
    out.add(c.b);
  }
  return out;
}
