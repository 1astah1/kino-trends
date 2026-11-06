/**
 * Русские подписи редактора баннера. Коды латиницей приходят от сервера (`BannerValidationCode`,
 * `CreativeRejectCode`, …); здесь — только то, что видит оператор.
 */
import {
  CircleAlert,
  CircleCheck,
  Hourglass,
  Info,
  type LucideIcon,
  OctagonAlert,
  PauseCircle,
  Archive,
  PencilLine,
  PlayCircle,
  TriangleAlert,
} from "lucide-react";

import type { StatusMetaMap } from "@/components/ui/status-badge";
import type { Schemas } from "@/lib/api/client";

type Code = Schemas["BannerValidationCode"];
type Severity = Schemas["WarningSeverity"];
type LayerKind = Schemas["BannerLayerKind"];
type RejectCode = Schemas["CreativeRejectCode"];

/** Короткий заголовок предупреждения; подробности — `message` сервера. */
export const VALIDATION_CODE_LABELS: Record<Code, string> = {
  creative_outside_safe_zone: "Креатив выходит за безопасную зону",
  creative_in_bottom_dead_zone: "Креатив в нижней мёртвой зоне: подпись и кнопки площадки",
  micro_brand_in_top_bar: "Микро-бренд под верхней панелью площадки",
  creative_too_small_to_read: "Креатив слишком мелкий — не прочитают",
  creative_upscaled_blurry: "Креатив растянут больше исходника — будет мыльным",
  creative_aspect_distorted: "Искажены пропорции креатива",
  animated_too_long: "Анимация длиннее допустимого",
  erid_missing_when_required: "Маркировка обязательна, а erid не задан",
  banner_overlaps_captions_zone: "Баннер перекрывает зону субтитров",
  file_too_large: "Файл слишком большой",
  unsupported_format: "Формат файла не поддерживается",
  layer_outside_canvas: "Слой выходит за край кадра",
  creative_clipped_by_slot: "Креатив обрезан рамкой слота",
  creative_missing: "Креатива нет в библиотеке или он ещё не готов",
  fallback_plate_used: "Вместо креатива — запасная текстовая плашка",
  text_shrunk_below_min: "Текст ужат ниже минимального кегля",
  text_truncated: "Текст не поместился и обрезан",
  animated_cut_by_window: "Анимация длиннее окна показа — оборвётся",
  impulse_window_stretched: "Ролик короткий: окно импульса растянуто",
  offer_area_too_tall: "Баннер выше рекомендованной зоны оффера",
  captions_zone_too_small: "Под субтитры остаётся мало места",
  effect_unsupported_for_animated: "Эффект не поддерживается у анимации",
};

export function validationCodeLabel(code: string): string {
  return VALIDATION_CODE_LABELS[code as Code] ?? code;
}

export const SEVERITY_META: Record<
  Severity,
  { label: string; icon: LucideIcon; text: string; bg: string; rank: number }
> = {
  error: { label: "Ошибка", icon: OctagonAlert, text: "text-negative", bg: "bg-negative/10", rank: 0 },
  warn: { label: "Внимание", icon: TriangleAlert, text: "text-warning", bg: "bg-warning/10", rank: 1 },
  info: { label: "Заметка", icon: Info, text: "text-accent", bg: "bg-accent/10", rank: 2 },
};

export const LAYER_KIND_LABELS: Record<LayerKind, string> = {
  creative: "Креатив",
  text_code: "Код ролика",
  text_erid: "Реклама · erid",
  micro_brand: "Микро-бренд",
  fallback_plate: "Запасная плашка",
};

export function layerKindLabel(kind: string): string {
  return LAYER_KIND_LABELS[kind as LayerKind] ?? kind;
}

export const LAYER_SHOW_LABELS: Record<Schemas["LayerShow"], string> = {
  persistent: "Весь ролик",
  with_creative: "С креативом (импульс и финал)",
};

export const FIT_LABELS: Record<Schemas["FitMode"], string> = {
  contain: "Вписать целиком",
  cover: "Заполнить слот",
  none: "Родной размер",
};

export const ANCHOR_LABELS: Record<Schemas["Anchor"], string> = {
  top_left: "Сверху слева",
  top_center: "Сверху по центру",
  top_right: "Сверху справа",
  center_left: "По центру слева",
  center: "По центру",
  center_right: "По центру справа",
  bottom_left: "Снизу слева",
  bottom_center: "Снизу по центру",
  bottom_right: "Снизу справа",
};

export const ATTACH_LABELS: Record<Schemas["TextAttach"], string> = {
  none: "Свободно (свой слот)",
  below_creative: "Под креативом",
  above_creative: "Над креативом",
  inside_bottom: "Внутри креатива, снизу",
  inside_top: "Внутри креатива, сверху",
};

export const TIMING_MODE_LABELS: Record<Schemas["TimingMode"], string> = {
  impulse: "Импульс + финальный повтор",
  persistent: "Весь ролик",
  windows: "Свои окна",
};

export const ALIGN_LABELS: Record<Schemas["TextAlign"], string> = {
  left: "Влево",
  center: "По центру",
  right: "Вправо",
};

/** Почему файл не принят (`details.asset_error` загрузки и `BannerCreative.error_code`). */
export const REJECT_CODE_LABELS: Record<RejectCode, string> = {
  empty_file: "Файл пустой",
  corrupt_file: "Файл повреждён или не читается",
  unsupported_format: "Формат не поддерживается. Подойдут PNG, JPG, WebP, GIF, APNG, WebM с альфой, MP4",
  file_too_large: "Файл больше 50 МБ",
  dimensions_too_large: "Слишком большое разрешение",
  dimensions_too_small: "Слишком маленькое разрешение — в кадре будет нечитаемо",
  animated_too_long: "Анимация слишком длинная",
  too_many_frames: "Слишком много кадров в анимации",
  normalize_failed: "Не удалось перекодировать файл",
};

export function rejectCodeLabel(code: string | null | undefined): string | null {
  if (!code) return null;
  return REJECT_CODE_LABELS[code as RejectCode] ?? code;
}

export const CREATIVE_STATUS_META: StatusMetaMap<Schemas["CreativeStatus"]> = {
  processing: { label: "Обработка", variant: "machine", icon: Hourglass },
  ready: { label: "Готов", variant: "positive", icon: CircleCheck },
  failed: { label: "Не принят", variant: "negative", icon: CircleAlert },
};

export const CREATIVE_FORMAT_LABELS: Record<Schemas["CreativeSourceFormat"], string> = {
  png: "PNG",
  jpeg: "JPG",
  webp: "WebP",
  gif: "GIF",
  apng: "APNG",
  webp_animated: "WebP (анимация)",
  webm: "WebM",
  mp4: "MP4",
  mov: "MOV",
};

export const VARIANT_STATUS_META: StatusMetaMap<Schemas["VariantStatus"]> = {
  draft: { label: "Черновик", variant: "neutral", icon: PencilLine },
  active: { label: "В ротации", variant: "positive", icon: PlayCircle },
  paused: { label: "На паузе", variant: "warning", icon: PauseCircle },
  archived: { label: "В архиве", variant: "neutral", icon: Archive, outline: true },
};

export const PREVIEW_MOMENT_LABELS: Record<Schemas["PreviewMoment"], string> = {
  impulse: "Импульс",
  final_repeat: "Финал",
  at_time: "Момент t",
};

export const PREVIEW_BACKGROUND_LABELS: Record<"sample" | "black" | "checker", string> = {
  sample: "Образец",
  black: "Чёрный",
  checker: "Шахматка",
};

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || Number.isNaN(bytes)) return "—";
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} КБ`;
  return `${(bytes / 1024 / 1024).toFixed(1).replace(".", ",")} МБ`;
}

export function formatSec(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value.toFixed(digits).replace(".", ",")} с`;
}
