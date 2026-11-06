/**
 * Подписи экрана «Трендоскаут». Источник истины — Kino-trends (`kinotrends/pipeline.py: STATE_RU`,
 * `web/app.js: POP_RU / RIGHTS_RU`): в ответах уже есть `status_ru` / `level_ru`, карты ниже нужны,
 * чтобы статус был цветом **и** иконкой с подписью (§7.4) и чтобы не ждать ответа ради подписи.
 * Неизвестное состояние с той стороны рисуется нейтрально с подписью из ответа.
 */
import {
  Ban,
  CheckCheck,
  CircleCheck,
  CircleDashed,
  CircleX,
  Clock,
  Download,
  Eye,
  FileCheck,
  FileX,
  Scale,
  Scissors,
  Send,
  Shield,
  ShieldAlert,
  ShieldCheck,
  ShieldQuestion,
  ShieldX,
  SkipForward,
  Upload,
} from "lucide-react";

import type { StatusMetaMap } from "@/components/ui/status-badge";
import type {
  TrendsFilmKind,
  TrendsFilmPeriod,
  TrendsFilmRow,
  TrendsFilmSort,
  TrendsFilmSource,
  TrendsRights,
  TrendsVideoFrames,
  TrendsVideoSort,
} from "@/lib/queries/trends";

// ---------------------------------------------------------------- конвейер Kino-trends → auto-shorts

export const PIPELINE_STATES = [
  "new",
  "file_pending",
  "needs_rights",
  "sent",
  "cutting",
  "review",
  "qc_failed",
  "approved",
  "publishing",
  "published",
  "failed",
  "cancelled",
] as const;
export type PipelineState = (typeof PIPELINE_STATES)[number];

export const PIPELINE_STATE_META: StatusMetaMap<PipelineState> = {
  new: { label: "В очереди", variant: "machine", icon: Clock, outline: true },
  file_pending: { label: "Готовится файл", variant: "machine", icon: Download },
  needs_rights: { label: "Нужен тип прав", variant: "human", icon: Scale },
  sent: { label: "Источник в auto-shorts", variant: "machine", icon: Send },
  cutting: { label: "Нарезка", variant: "machine", icon: Scissors },
  review: { label: "Ждёт ревью", variant: "human", icon: Eye },
  qc_failed: { label: "Не прошёл QC", variant: "negative", icon: ShieldAlert },
  approved: { label: "Одобрено, ждёт залива", variant: "positive", icon: CircleCheck, outline: true },
  publishing: { label: "На автозаливе", variant: "machine", icon: Upload },
  published: { label: "Опубликовано", variant: "positive", icon: CheckCheck },
  failed: { label: "Ошибка", variant: "negative", icon: CircleX },
  cancelled: { label: "Отменено", variant: "neutral", icon: Ban, outline: true },
};

export function isPipelineState(value: string): value is PipelineState {
  return (PIPELINE_STATES as readonly string[]).includes(value);
}

/** Что можно нажать у задания (повторяет правила `renderPipeline` в Kino-trends). */
export function canRetry(status: string): boolean {
  return status === "failed" || status === "qc_failed" || status === "cancelled";
}

export function canCancel(status: string): boolean {
  return status !== "published" && status !== "cancelled";
}

// ---------------------------------------------------------------- чистота ролика и права произведения

export type CleanLevel = "clean" | "check" | "no" | "unknown";

export const CLEAN_LEVEL_META: StatusMetaMap<CleanLevel> = {
  clean: { label: "Чистое", variant: "positive", icon: ShieldCheck },
  check: { label: "Проверить", variant: "warning", icon: ShieldQuestion },
  no: { label: "Не брать", variant: "negative", icon: ShieldX },
  unknown: { label: "Не проверено", variant: "neutral", icon: Shield, outline: true },
};

export function cleanLevel(level: string | null | undefined): CleanLevel {
  return level === "clean" || level === "check" || level === "no" ? level : "unknown";
}

export type RightsLevel = "ok" | "caution" | "block" | "unknown";

export const RIGHTS_LEVEL_META: StatusMetaMap<RightsLevel> = {
  ok: { label: "Права: проходит", variant: "positive", icon: ShieldCheck },
  caution: { label: "Права: с оговорками", variant: "warning", icon: ShieldAlert },
  block: { label: "Права: не проходит", variant: "negative", icon: ShieldX },
  unknown: { label: "Права не проверены", variant: "neutral", icon: Shield, outline: true },
};

export function rightsLevel(level: string | null | undefined): RightsLevel {
  return level === "ok" || level === "caution" || level === "block" ? level : "unknown";
}

// ---------------------------------------------------------------- слот календаря кино

export type SlotStatus = "planned" | "done" | "skipped";

export const SLOT_STATUS_META: StatusMetaMap<SlotStatus> = {
  planned: { label: "В плане", variant: "neutral", icon: CircleDashed, outline: true },
  done: { label: "Сделано", variant: "positive", icon: CircleCheck },
  skipped: { label: "Пропущено", variant: "warning", icon: SkipForward, outline: true },
};

export function slotStatus(status: string | null | undefined): SlotStatus {
  return status === "done" || status === "skipped" ? status : "planned";
}

// ---------------------------------------------------------------- фильтры вкладки «Видео»

export const LEVEL_FILTERS = ["all", "pick", "fit", "clean", "check", "no"] as const;
export type LevelFilter = (typeof LEVEL_FILTERS)[number];

export const LEVEL_FILTER_OPTIONS: ReadonlyArray<{ value: LevelFilter; label: string }> = [
  { value: "all", label: "Все" },
  { value: "pick", label: "Подборка" },
  { value: "fit", label: "Под аудиторию" },
  { value: "clean", label: "Чистые" },
  { value: "check", label: "Проверить" },
  { value: "no", label: "Не брать" },
];

export const SORT_OPTIONS: ReadonlyArray<{ value: TrendsVideoSort; label: string }> = [
  { value: "score", label: "По баллу чистоты" },
  { value: "audience", label: "По совпадению с аудиторией" },
  { value: "growth", label: "По приросту в день" },
  { value: "views", label: "По просмотрам" },
  { value: "published", label: "По дате выхода" },
  { value: "seen", label: "По появлению в чартах" },
];

export type ShortFilter = "" | "1" | "0";

export const SHORT_OPTIONS: ReadonlyArray<{ value: ShortFilter; label: string }> = [
  { value: "", label: "Шортсы и длинные" },
  { value: "1", label: "Только шортсы" },
  { value: "0", label: "Только длинные" },
];

// ---------------------------------------------------------------- форматирование

/** «0:07», «46 с», «2:03:25»: длительность ролика в секундах. */
export function formatDuration(sec: number | null | undefined): string {
  if (sec === null || sec === undefined || Number.isNaN(sec)) return "—";
  const total = Math.max(0, Math.round(sec));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  if (m > 0) return `${m}:${String(s).padStart(2, "0")}`;
  return `${s} с`;
}

/** Совпадение с аудиторией 0–1 → «70 %». */
export function formatFit(fit: number | null | undefined): string {
  if (fit === null || fit === undefined || Number.isNaN(fit)) return "—";
  return `${Math.round(fit * 100)} %`;
}

export function fitTone(fit: number | null | undefined): string {
  if (fit === null || fit === undefined) return "text-fg-muted";
  if (fit >= 0.6) return "text-positive";
  if (fit >= 0.35) return "text-warning";
  return "text-fg-muted";
}

/** Лицензия YouTube: CC — можно перезаливать с указанием автора; «заявлен» — Content ID партнёра. */
export function licenseMeta(video: { license?: string | null; licensed_content?: number | null }) {
  if (video.license === "creativeCommon") {
    return {
      label: "CC",
      title: "Creative Commons: перезалив разрешён с указанием автора",
      variant: "positive" as const,
    };
  }
  if (video.licensed_content) {
    return {
      label: "Заявлен",
      title: "Контент заявлен партнёром YouTube: заявка Content ID или блок",
      variant: "negative" as const,
    };
  }
  return { label: "Без заявок", title: "Обычная лицензия YouTube, заявок партнёра нет", variant: "neutral" as const };
}

/** Безопасное чтение строк и чисел из «сырых» словарей Kino-trends (`plan`, `source`, `analysis`). */
export function pickString(obj: Record<string, unknown> | null | undefined, key: string): string | null {
  const value = obj?.[key];
  return typeof value === "string" && value.trim() ? value : null;
}

export function pickNumber(obj: Record<string, unknown> | null | undefined, key: string): number | null {
  const value = obj?.[key];
  return typeof value === "number" && !Number.isNaN(value) ? value : null;
}

// ---------------------------------------------------------------- разбор ролика

/** Пороги балла чистоты Kino-trends: от 45 — «проверить», от 70 — «чистое». */
export const SCORE_CHECK = 45;
export const SCORE_CLEAN = 70;

export function scoreTone(score: number | null | undefined): string {
  if (score === null || score === undefined) return "bg-fg-muted";
  if (score >= SCORE_CLEAN) return "bg-positive";
  if (score >= SCORE_CHECK) return "bg-warning";
  return "bg-negative";
}

export const MANUAL_LEVEL_OPTIONS: ReadonlyArray<{ value: "" | "clean" | "check" | "no"; label: string }> = [
  { value: "", label: "авто" },
  { value: "clean", label: "чистое" },
  { value: "check", label: "проверить" },
  { value: "no", label: "не брать" },
];

/** Флаги кадров, которые мешают перезаливу (как `flagChips` в Kino-trends). */
export const FRAME_FLAGS: ReadonlyArray<{ key: keyof TrendsVideoFrames; label: string }> = [
  { key: "watermark", label: "водяной знак" },
  { key: "banner", label: "чужая реклама или плашка" },
  { key: "subscribe_cta", label: "призыв подписаться" },
  { key: "text_overlay", label: "надписи" },
  { key: "subtitles", label: "субтитры" },
  { key: "borders", label: "рамки" },
  { key: "faces", label: "лица" },
  { key: "screen_recording", label: "запись экрана" },
];

export const ORIENTATION_RU: Record<string, string> = {
  vertical: "вертикальное",
  horizontal: "горизонтальное",
  square: "квадратное",
};

export type FileStatus = "running" | "done" | "error" | "none";

export const FILE_STATUS_META: StatusMetaMap<FileStatus> = {
  running: { label: "mp4 качается", variant: "machine", icon: Download },
  done: { label: "mp4 готов", variant: "positive", icon: FileCheck },
  error: { label: "mp4 не скачался", variant: "negative", icon: FileX },
  none: { label: "mp4 не заказан", variant: "neutral", icon: CircleDashed, outline: true },
};

export function fileStatus(status: string | null | undefined, fileUrl?: string | null): FileStatus {
  if (fileUrl) return "done";
  return status === "running" || status === "done" || status === "error" ? status : "none";
}

/** Регионы чартов YouTube (`POP_REGIONS` Kino-trends); в ответе списка есть `regions_ru`, это запас. */
export const REGION_RU: Record<string, string> = {
  RU: "Россия",
  KZ: "Казахстан",
  BY: "Беларусь",
  AZ: "Азербайджан",
  AM: "Армения",
  MD: "Молдова",
  GE: "Грузия",
  UA: "Украина",
};

/** Категории YouTube (`POP_CATS` Kino-trends). */
export const CATEGORY_RU: Record<string, string> = {
  "1": "кино и анимация",
  "2": "авто",
  "10": "музыка",
  "15": "животные",
  "17": "спорт",
  "19": "путешествия",
  "20": "игры",
  "22": "люди и блоги",
  "23": "юмор",
  "24": "развлечения",
  "25": "новости",
  "26": "как сделать и стиль",
  "27": "образование",
  "28": "наука и техника",
  "29": "общество",
};

/** `{RU: 7, KZ: 12}` → пары [код, место] по возрастанию места; значения не-числа отбрасываются. */
export function rankEntries(obj: Record<string, unknown> | null | undefined): Array<[string, number | null]> {
  if (!obj) return [];
  return Object.entries(obj)
    .map(([key, value]): [string, number | null] => [key, typeof value === "number" ? value : null])
    .sort((a, b) => (a[1] ?? 1e9) - (b[1] ?? 1e9));
}

// ---------------------------------------------------------------- кино и сериалы

export const FILM_KIND_OPTIONS: ReadonlyArray<{ value: TrendsFilmKind; label: string }> = [
  { value: "all", label: "Все" },
  { value: "film", label: "Фильмы" },
  { value: "series", label: "Сериалы" },
  { value: "animated", label: "Мультфильмы и аниме" },
];

export const FILM_PERIOD_OPTIONS: ReadonlyArray<{ value: TrendsFilmPeriod; label: string }> = [
  { value: "7", label: "7 дней" },
  { value: "30", label: "30 дней" },
  { value: "90", label: "90 дней" },
  { value: "all", label: "Всё время" },
];

export const FILM_SORT_OPTIONS: ReadonlyArray<{ value: TrendsFilmSort; label: string }> = [
  { value: "score", label: "По баллу темы" },
  { value: "growth", label: "По приросту за 7 дней" },
  { value: "shorts", label: "По числу нарезок" },
  { value: "fresh", label: "По свежим нарезкам" },
  { value: "views", label: "По просмотрам" },
  { value: "median", label: "По медиане просмотров" },
  { value: "outliers", label: "По аутлаерам" },
];

export const MANUAL_RIGHTS_OPTIONS: ReadonlyArray<{ value: "" | "ok" | "caution" | "block"; label: string }> = [
  { value: "", label: "авто" },
  { value: "ok", label: "проходит" },
  { value: "caution", label: "осторожно" },
  { value: "block", label: "нельзя" },
];

export const HOLDER_POLICY_RU: Record<string, string> = {
  block: "блокирует и страйкует",
  claim: "заявляет права через Content ID",
  tolerant: "терпит нарезки",
  unknown: "поведение неизвестно",
};

export const SOURCE_KIND_RU: Record<string, string> = {
  film: "полный фильм",
  episode: "серия",
  pack: "все серии",
  other: "другое",
};

/** «серия 3, сезон 1» / «полный фильм» — что за исходник. */
export function sourceKindLabel(source: Pick<TrendsFilmSource, "kind" | "season" | "episode">): string {
  const base = SOURCE_KIND_RU[source.kind ?? ""] ?? source.kind ?? "исходник";
  if (source.kind !== "episode") return base;
  const parts = [source.episode ? `серия ${source.episode}` : "серия", source.season ? `сезон ${source.season}` : null];
  return parts.filter(Boolean).join(", ");
}

/** YouTube или Rutube — по ссылке. */
export function sourcePlatform(url: string | null | undefined): string {
  if (!url) return "";
  if (/rutube\.ru/.test(url)) return "Rutube";
  if (/youtu\.?be/.test(url)) return "YouTube";
  if (/vk\.com|vkvideo/.test(url)) return "VK";
  return "ссылка";
}

export interface FilmReadiness {
  ready: boolean;
  reasons: string[];
}

/**
 * «Готово к нарезке»: права проходят (ok или caution), исходник есть и подтверждён, не в активной работе.
 * Kino-trends считает это сам (`ready`, `ready_reasons`); если поля не пришли — считаем здесь по тем же правилам.
 */
export function filmReadiness(
  row: Pick<TrendsFilmRow, "ready" | "ready_reasons" | "rights" | "source" | "in_pipeline">,
  pipelineStatus?: string | null,
): FilmReadiness {
  if (typeof row.ready === "boolean") return { ready: row.ready, reasons: row.ready_reasons ?? [] };
  const reasons: string[] = [];
  const level = rightsLevel(row.rights?.level);
  if (level === "block") reasons.push("права: нельзя");
  else if (level === "unknown") reasons.push("права не проверены");
  if (!row.source) reasons.push("нет исходника");
  else if (!row.source.verified) reasons.push("исходник не подтверждён");
  const status = pipelineStatus ?? row.in_pipeline ?? null;
  if (status && !canRetry(status)) reasons.push(`уже в конвейере (${status})`);
  return { ready: reasons.length === 0, reasons };
}

/** Риск 0–100 → подпись цвета (пороги Kino-trends: от 40 с оговорками, от 65 не проходит). */
export function riskTone(risk: number | null | undefined): string {
  if (risk === null || risk === undefined) return "bg-fg-muted";
  if (risk >= 65) return "bg-negative";
  if (risk >= 40) return "bg-warning";
  return "bg-positive";
}

/** Короткая строка прав для таблиц: «проходит · риск 35». */
export function rightsSummary(rights: TrendsRights | null | undefined): string {
  if (!rights) return "не проверены";
  const label = RIGHTS_LEVEL_META[rightsLevel(rights.level)].label.replace("Права: ", "").replace("Права ", "");
  return rights.risk !== null && rights.risk !== undefined ? `${label} · риск ${Math.round(rights.risk)}` : label;
}
