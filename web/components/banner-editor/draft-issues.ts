/**
 * Черновик против пределов схемы `BannerVariantDraft` (domain/banner_schema.py) — по-русски и до
 * запроса. Сервер отвечает на такие черновики 422 с английскими текстами pydantic; редактор не шлёт
 * их на проверку, «Кадр из рендера» и автосохранение, а показывает, какое поле поправить.
 *
 * Здесь же — перевод 422 `details.errors[]` на случай, если сервер строже клиента.
 */
import { type ValidationIssue, ValidationError } from "@/lib/api/errors";

import { type BannerLayer, ERID_PATTERN, type EditorDraft, SLOT_SCALE_MAX, SLOT_SCALE_MIN } from "./draft";
import { layerKindLabel } from "./labels";

/** Пределы полей схемы (banner_schema.py). */
export const LIMITS = {
  fontPx: [8, 200],
  paddingPx: [0, 120],
  styleRadiusPx: [0, 200],
  slotRadiusPx: [0, 400],
  scale: [SLOT_SCALE_MIN, SLOT_SCALE_MAX],
  z: [-100, 100],
  attachGapPx: [0, 200],
  maxLines: [1, 4],
  strokePx: [0, 12],
  hookSilenceSec: [0, 30],
  impulseDurationSec: [0.5, 30],
  impulseMinSec: [0.3, 30],
  fadeMs: [0, 3000],
  finalRepeatSec: [0.5, 30],
  mergeGapSec: [0, 10],
} as const satisfies Record<string, readonly [number, number]>;

const TEXT_KINDS = new Set(["text_code", "text_erid", "fallback_plate"]);
const VARIANT_CODE_PATTERN = /^[A-Za-z0-9_-]{1,16}$/;

export interface DraftIssue {
  /** Стабильный ключ (для React). */
  key: string;
  message: string;
  /** Слой, который выделить на холсте по клику. */
  layerId?: string;
}

function layerName(layer: BannerLayer, index: number): string {
  return `Слой ${index + 1} · ${layerKindLabel(layer.kind)}`;
}

function fmt(value: number): string {
  return String(value).replace(".", ",");
}

/** Число в пределах; `integer` — ещё и целое. null — всё хорошо, иначе текст замечания. */
function rangeProblem(
  value: number | null | undefined,
  [min, max]: readonly [number, number],
  integer = false,
): string | null {
  if (value === null || value === undefined || !Number.isFinite(value)) return "нужно число";
  if (integer && !Number.isInteger(value)) return "нужно целое число";
  if (value < min || value > max) return `от ${fmt(min)} до ${fmt(max)}`;
  return null;
}

/** Замечания к черновику, из-за которых сервер ответит 422. Пусто — черновик можно отправлять. */
export function draftIssues(draft: EditorDraft | null | undefined): DraftIssue[] {
  if (!draft) return [];
  const out: DraftIssue[] = [];
  const add = (key: string, message: string, layerId?: string) => out.push({ key, message, layerId });
  const check = (
    key: string,
    label: string,
    value: number | null | undefined,
    range: readonly [number, number],
    integer = false,
    layerId?: string,
  ) => {
    const problem = rangeProblem(value, range, integer);
    if (problem) add(key, `${label}: ${problem}`, layerId);
  };

  if (!VARIANT_CODE_PATTERN.test(draft.code)) add("code", "Код варианта: 1–16 латинских букв, цифр, «-» или «_»");
  if (draft.name.length > 200) add("name", "Название длиннее 200 символов");

  const ids = new Set<string>();
  draft.layers.forEach((layer, index) => {
    const name = layerName(layer, index);
    const id = layer.id;
    const k = (field: string) => `layer:${id}:${index}:${field}`;
    if (ids.has(id)) add(k("id"), `${name}: такой id слоя уже есть`, id);
    ids.add(id);

    const text = (layer.text ?? "").trim();
    if (TEXT_KINDS.has(layer.kind) && !text) add(k("text"), `${name}: пустой текст`, id);
    if (layer.kind === "micro_brand" && !text && !layer.creative_sha256)
      add(k("text"), `${name}: нужен текст или картинка`, id);
    if ((layer.text ?? "").length > 400) add(k("text-len"), `${name}: текст длиннее 400 символов`, id);

    const s = layer.slot;
    check(k("scale"), `${name}, масштаб`, s.scale, LIMITS.scale, false, id);
    check(k("slot-radius"), `${name}, скругление слота`, s.corner_radius_px, LIMITS.slotRadiusPx, true, id);
    check(k("z"), `${name}, порядок (z)`, layer.z, LIMITS.z, true, id);
    check(k("gap"), `${name}, зазор`, layer.attach_gap_px, LIMITS.attachGapPx, true, id);

    const st = layer.style;
    if (st) {
      check(k("max-font"), `${name}, кегль макс.`, st.max_font_px, LIMITS.fontPx, true, id);
      check(k("min-font"), `${name}, кегль мин.`, st.min_font_px, LIMITS.fontPx, true, id);
      if (st.min_font_px > st.max_font_px) add(k("font-order"), `${name}: кегль мин. больше кегля макс.`, id);
      check(k("lines"), `${name}, строк`, st.max_lines, LIMITS.maxLines, true, id);
      check(k("pad-x"), `${name}, поле X`, st.padding_x, LIMITS.paddingPx, true, id);
      check(k("pad-y"), `${name}, поле Y`, st.padding_y, LIMITS.paddingPx, true, id);
      check(k("radius"), `${name}, скругление подложки`, st.corner_radius_px, LIMITS.styleRadiusPx, true, id);
      check(k("stroke"), `${name}, обводка`, st.stroke_px, LIMITS.strokePx, true, id);
    }
  });

  const t = draft.timing;
  check("timing:hook", "Тишина хука", t.hook_silence_sec, LIMITS.hookSilenceSec);
  check("timing:dur", "Длина импульса", t.impulse_duration_sec, LIMITS.impulseDurationSec);
  check("timing:min", "«Не короче»", t.impulse_min_sec, LIMITS.impulseMinSec);
  if (t.impulse_min_sec > t.impulse_duration_sec)
    add("timing:min-order", "«Не короче» больше длины импульса — уменьшите его или удлините импульс");
  const [lo, hi] = t.impulse_window_pct;
  if (!(lo >= 0 && lo < hi && hi <= 1)) add("timing:window", "Окно импульса: «с» должно быть меньше «по»");
  check("timing:fade-in", "Появление", t.fade_in_ms, LIMITS.fadeMs, true);
  check("timing:fade-out", "Исчезание", t.fade_out_ms, LIMITS.fadeMs, true);
  check("timing:final", "Длина финального повтора", t.final_repeat_sec, LIMITS.finalRepeatSec);
  check("timing:merge", "Слить окна ближе, чем", t.merge_gap_sec, LIMITS.mergeGapSec);
  const windows = t.windows ?? [];
  if (t.mode === "windows" && windows.length === 0) add("timing:windows", "Свои окна: добавьте хотя бы одно окно");
  windows.forEach((w, index) => {
    if (!(w.start_sec >= 0)) add(`timing:win-s-${index}`, `Окно ${index + 1}: начало не раньше 0 с`);
    if (!(w.end_sec > w.start_sec)) add(`timing:win-e-${index}`, `Окно ${index + 1}: конец должен быть позже начала`);
  });

  const erid = draft.ad_marking.erid;
  if (erid && !ERID_PATTERN.test(erid)) add("erid", "erid: 4–64 латинских букв, цифр, «-» или «_»");
  if ((draft.ad_marking.advertiser_text ?? "").length > 120) add("advertiser", "Рекламодатель: длиннее 120 символов");
  return out;
}

// ---------------------------------------------------------------- 422 сервера → по-русски

const FIELD_LABELS: Record<string, string> = {
  "slot.x": "X",
  "slot.y": "Y",
  "slot.w": "ширина",
  "slot.h": "высота",
  "slot.scale": "масштаб",
  "slot.opacity": "непрозрачность",
  "slot.corner_radius_px": "скругление слота",
  "slot.offset_x": "сдвиг X",
  "slot.offset_y": "сдвиг Y",
  "style.max_font_px": "кегль макс.",
  "style.min_font_px": "кегль мин.",
  "style.padding_x": "поле X",
  "style.padding_y": "поле Y",
  "style.corner_radius_px": "скругление подложки",
  "style.max_lines": "строк",
  "style.font_file": "шрифт",
  "style.stroke_px": "обводка",
  text: "текст",
  creative_sha256: "креатив",
  attach_gap_px: "зазор",
  z: "порядок (z)",
  "timing.hook_silence_sec": "тишина хука",
  "timing.impulse_duration_sec": "длина импульса",
  "timing.impulse_min_sec": "«Не короче»",
  "timing.impulse_window_pct": "окно импульса",
  "timing.fade_in_ms": "появление",
  "timing.fade_out_ms": "исчезание",
  "timing.final_repeat_sec": "длина финального повтора",
  "timing.merge_gap_sec": "слить окна",
  timing: "тайминг",
  ad_marking: "маркировка",
  "ad_marking.erid": "erid",
  "ad_marking.advertiser_text": "рекламодатель",
  code: "код варианта",
  name: "название",
  code_url_template: "шаблон ссылки",
  layers: "слои",
};

const VALUE_ERRORS: Array<[RegExp, string]> = [
  [/needs text or creative/, "нужен текст или картинка"],
  [/needs text/, "пустой текст"],
  [/min_font_px must be <= max_font_px/, "кегль мин. больше кегля макс."],
  [/impulse_min_sec must be <= impulse_duration_sec/, "«Не короче» больше длины импульса"],
  [/end_sec must be greater than start_sec/, "конец окна должен быть позже начала"],
  [/impulse_window_pct/, "окно импульса: «с» должно быть меньше «по»"],
  [/needs at least one window/, "нужно хотя бы одно окно"],
  [/layer ids must be unique/, "id слоёв повторяются"],
  [/attach is only for/, "привязка бывает только у кода ролика и erid"],
  [/sha256/, "неверный креатив"],
];

/** `draft.layers.3.style.min_font_px` → «Слой 4 · Код ролика, кегль мин.». */
export function fieldLabel(path: string, draft?: EditorDraft | null): string {
  const parts = path.split(".").filter((part) => part !== "draft");
  if (parts[0] === "layers" && parts[1] !== undefined && /^\d+$/.test(parts[1])) {
    const index = Number(parts[1]);
    const layer = draft?.layers[index];
    const head = layer ? layerName(layer, index) : `Слой ${index + 1}`;
    const tail = parts.slice(2).join(".");
    return tail ? `${head}, ${FIELD_LABELS[tail] ?? tail}` : head;
  }
  if (parts[0] === "timing" && parts[1] === "windows" && parts[2] !== undefined) {
    const which = parts[3] === "start_sec" ? "начало" : parts[3] === "end_sec" ? "конец" : "";
    return `Окно ${Number(parts[2]) + 1}${which ? `: ${which}` : ""}`;
  }
  const key = parts.join(".");
  return FIELD_LABELS[key] ?? (key || "черновик");
}

function lastNumber(msg: string): string | null {
  const match = /(-?\d+(?:\.\d+)?)\s*$/.exec(msg);
  return match ? fmt(Number(match[1])) : null;
}

/** Текст одной ошибки pydantic по-русски. */
export function issueMessage(issue: ValidationIssue): string {
  const msg = String(issue.msg ?? "");
  if (/[А-Яа-яЁё]/.test(msg)) return msg; // сервер уже ответил по-русски
  const n = lastNumber(msg);
  switch (issue.type) {
    case "greater_than_equal":
      return n ? `не меньше ${n}` : "слишком маленькое значение";
    case "greater_than":
      return n ? `больше ${n}` : "слишком маленькое значение";
    case "less_than_equal":
      return n ? `не больше ${n}` : "слишком большое значение";
    case "less_than":
      return n ? `меньше ${n}` : "слишком большое значение";
    case "int_from_float":
    case "int_type":
    case "int_parsing":
      return "нужно целое число";
    case "float_type":
    case "float_parsing":
      return "нужно число";
    case "string_pattern_mismatch":
      return "недопустимые символы или формат";
    case "string_too_long":
      return "слишком длинный текст";
    case "string_too_short":
      return "пустое значение";
    case "too_long":
      return "слишком много элементов";
    case "missing":
      return "обязательное поле";
    case "enum":
    case "literal_error":
      return "недопустимое значение";
    default:
      break;
  }
  for (const [pattern, text] of VALUE_ERRORS) if (pattern.test(msg)) return text;
  return "значение не прошло проверку";
}

/**
 * 422 `details.errors[]` → строка по-русски: «Слой 4 · Код ролика: пустой текст; erid: …».
 * Не 422 или без списка ошибок — null (вызывающий покажет обычное сообщение).
 */
export function describeValidationError(error: unknown, draft?: EditorDraft | null, limit = 3): string | null {
  if (!(error instanceof ValidationError)) return null;
  const raw = error.details.errors;
  if (!Array.isArray(raw) || raw.length === 0) return null;
  const lines: string[] = [];
  for (const item of raw as ValidationIssue[]) {
    if (!item || !Array.isArray(item.loc)) continue;
    const path = item.loc
      .filter((part) => part !== "body" && part !== "query" && part !== "path")
      .map(String)
      .join(".");
    const line = `${fieldLabel(path, draft)}: ${issueMessage(item)}`;
    if (!lines.includes(line)) lines.push(line);
  }
  if (!lines.length) return null;
  const more = lines.length > limit ? ` и ещё ${lines.length - limit}` : "";
  return lines.slice(0, limit).join("; ") + more;
}

/** Замечания черновика одной строкой для тоста и плашки «Не сохранено». */
export function issuesSummary(issues: DraftIssue[], limit = 3): string {
  const more = issues.length > limit ? ` и ещё ${issues.length - limit}` : "";
  return `Исправьте поля: ${issues
    .slice(0, limit)
    .map((issue) => issue.message)
    .join("; ")}${more}.`;
}
