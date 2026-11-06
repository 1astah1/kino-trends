/**
 * Подписи аналитики по-русски: метрики и их единицы, разрезы, когорты, шаги воронки, возраста,
 * лестница опроса, классы провала удержания (docs/ARCHITECTURE.md §6.7, §7.3).
 */
import { CircleCheck, CircleDashed, Clock, type LucideIcon, TriangleAlert } from "lucide-react";

import type { StatusMetaMap } from "@/components/ui";
import { formatNumber, formatPct, formatUsd } from "@/lib/format";
import type {
  AgePoint,
  AgeVerdict,
  AnalyticsDimension,
  AnalyticsGranularity,
  AnalyticsMetric,
  AttributionLevel,
  CohortAge,
  CohortAggregate,
  CohortMetric,
  CostGroupBy,
  FunnelStepCode,
  LadderStepStatus,
  Platform,
  RetentionFailureClass,
} from "@/lib/queries/analytics";

/** Как показывать число метрики: счётчик, $, ₽, копейки выручки, доля 0..1, «на 1000». */
export type MetricKind = "count" | "usd" | "rub" | "rub_minor" | "ratio" | "per_1k";

export const METRIC_LABELS: Record<AnalyticsMetric, string> = {
  videos_produced: "Роликов сделано",
  videos_published: "Роликов опубликовано",
  videos_rejected: "Роликов отклонено",
  videos_failed: "Роликов упало",
  views: "Просмотры",
  engaged_views: "Вовлечённые просмотры",
  likes: "Лайки",
  comments: "Комментарии",
  clicks: "Переходы",
  landing_views: "Показы лендинга",
  unique_visitors: "Уникальные посетители",
  to_telegram: "В Telegram",
  to_site: "На сайт",
  direct_bot_starts: "Старты бота напрямую",
  visitors: "Посетители",
  bot_starts: "Старты бота",
  users: "Пользователи бота",
  tma_opens: "Открытия мини-приложения",
  signups: "Регистрации",
  first_payments: "Первые оплаты",
  payments: "Оплаты",
  revenue_minor: "Выручка",
  spend_usd: "Расход, $",
  spend_rub: "Расход, ₽",
  overhead_usd: "Накладные, $",
  rejected_spend_usd: "Расход на брак, $",
  ctr_per_1k_views: "CTR на 1000 просмотров",
  cr_visitor_to_start: "CR посетитель → старт",
  cr_start_to_payment: "CR старт → оплата",
  cost_per_visitor_usd: "Цена посетителя",
  cost_per_user_usd: "Цена пользователя",
  cost_per_bot_start_usd: "Цена старта бота",
  cost_per_first_payment_usd: "Цена первой оплаты",
  cost_per_published_video_usd: "Цена опубликованного ролика",
  cost_per_video_usd: "Цена ролика",
  romi: "ROMI",
};

export const METRIC_KIND: Record<AnalyticsMetric, MetricKind> = {
  videos_produced: "count",
  videos_published: "count",
  videos_rejected: "count",
  videos_failed: "count",
  views: "count",
  engaged_views: "count",
  likes: "count",
  comments: "count",
  clicks: "count",
  landing_views: "count",
  unique_visitors: "count",
  to_telegram: "count",
  to_site: "count",
  direct_bot_starts: "count",
  visitors: "count",
  bot_starts: "count",
  users: "count",
  tma_opens: "count",
  signups: "count",
  first_payments: "count",
  payments: "count",
  revenue_minor: "rub_minor",
  spend_usd: "usd",
  spend_rub: "rub",
  overhead_usd: "usd",
  rejected_spend_usd: "usd",
  ctr_per_1k_views: "per_1k",
  cr_visitor_to_start: "ratio",
  cr_start_to_payment: "ratio",
  cost_per_visitor_usd: "usd",
  cost_per_user_usd: "usd",
  cost_per_bot_start_usd: "usd",
  cost_per_first_payment_usd: "usd",
  cost_per_published_video_usd: "usd",
  cost_per_video_usd: "usd",
  romi: "ratio",
};

/** Группы для выпадающего списка метрик (optgroup). */
export const METRIC_GROUPS: ReadonlyArray<{ label: string; metrics: AnalyticsMetric[] }> = [
  { label: "Просмотры", metrics: ["views", "engaged_views", "likes", "comments"] },
  {
    label: "Переходы",
    metrics: ["clicks", "landing_views", "unique_visitors", "to_telegram", "to_site", "direct_bot_starts", "visitors"],
  },
  {
    label: "Конверсии",
    metrics: ["bot_starts", "users", "tma_opens", "signups", "first_payments", "payments", "revenue_minor"],
  },
  { label: "Ролики", metrics: ["videos_produced", "videos_published", "videos_rejected", "videos_failed"] },
  { label: "Деньги", metrics: ["spend_usd", "spend_rub", "overhead_usd", "rejected_spend_usd"] },
  {
    label: "Коэффициенты",
    metrics: [
      "ctr_per_1k_views",
      "cr_visitor_to_start",
      "cr_start_to_payment",
      "cost_per_visitor_usd",
      "cost_per_user_usd",
      "cost_per_bot_start_usd",
      "cost_per_first_payment_usd",
      "cost_per_published_video_usd",
      "cost_per_video_usd",
      "romi",
    ],
  },
];

/** Метрики, у которых рост — плохо (цены и брак): дельта красится наоборот. */
export const METRIC_GOOD_WHEN_DOWN: ReadonlySet<AnalyticsMetric> = new Set<AnalyticsMetric>([
  "videos_rejected",
  "videos_failed",
  "spend_usd",
  "spend_rub",
  "overhead_usd",
  "rejected_spend_usd",
  "cost_per_visitor_usd",
  "cost_per_user_usd",
  "cost_per_bot_start_usd",
  "cost_per_first_payment_usd",
  "cost_per_published_video_usd",
  "cost_per_video_usd",
]);

/** Выручка приходит в копейках (`revenue_minor`, RUB). */
export function formatRubMinor(minor: number | null | undefined): string {
  if (minor === null || minor === undefined || Number.isNaN(minor)) return "—";
  return `${formatNumber(minor / 100, 2)} ₽`;
}

export function formatRub(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${formatNumber(value, 2)} ₽`;
}

/** Доля 0..1 → проценты. */
export function formatRatio(ratio: number | null | undefined, digits = 1): string {
  if (ratio === null || ratio === undefined || Number.isNaN(ratio)) return "—";
  return formatPct(ratio * 100, digits);
}

export function formatMetric(metric: AnalyticsMetric, value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  switch (METRIC_KIND[metric]) {
    case "usd":
      return formatUsd(value, value !== 0 && Math.abs(value) < 1 ? 4 : 2);
    case "rub":
      return formatRub(value);
    case "rub_minor":
      return formatRubMinor(value);
    case "ratio":
      return formatRatio(value, 1);
    case "per_1k":
      return formatNumber(value, 2);
    default:
      return formatNumber(value);
  }
}

/** Короткая подпись оси для метрики: без копеек и лишних знаков. */
export function formatMetricAxis(metric: AnalyticsMetric, value: number): string {
  switch (METRIC_KIND[metric]) {
    case "usd":
      return formatUsd(value, Math.abs(value) < 10 ? 2 : 0);
    case "rub":
      return `${formatCompact(value)} ₽`;
    case "rub_minor":
      return `${formatCompact(value / 100)} ₽`;
    case "ratio":
      return formatPct(value * 100, 0);
    case "per_1k":
      return formatNumber(value, 1);
    default:
      return formatCompact(value);
  }
}

/** 12 345 → «12,3 тыс.», 1 234 567 → «1,2 млн». */
export function formatCompact(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1_000_000) return `${formatNumber(value / 1_000_000, 1)} млн`;
  if (abs >= 10_000) return `${formatNumber(value / 1000, 0)} тыс.`;
  if (abs >= 1000) return `${formatNumber(value / 1000, 1)} тыс.`;
  return formatNumber(value, abs < 10 && abs !== Math.floor(abs) ? 1 : 0);
}

export const DIMENSION_LABELS: Record<AnalyticsDimension, string> = {
  video: "Ролик",
  channel: "Канал",
  niche: "Ниша",
  product: "Продукт",
  campaign: "Кампания",
  variant: "Вариант баннера",
  track: "Трек",
  platform: "Площадка",
};

export const COST_GROUP_LABELS: Record<CostGroupBy, string> = {
  provider: "Провайдер",
  step: "Шаг",
  track: "Трек",
  outcome: "Исход ролика",
  channel: "Канал",
  niche: "Ниша",
  product: "Продукт",
};

export const COHORT_METRIC_LABELS: Record<CohortMetric, string> = {
  views: "Просмотры",
  visitors: "Посетители",
  bot_starts: "Старты бота",
  users: "Пользователи бота",
  first_payments: "Первые оплаты",
  revenue_minor: "Выручка",
  ctr_per_1k_views: "CTR на 1000 просмотров",
};

export const COHORT_AGE_LABELS: Record<CohortAge, string> = {
  h24: "24 часа",
  h72: "72 часа",
  d7: "7 дней",
  d14: "14 дней",
  d30: "30 дней",
  lifetime: "За всё время",
};

export const COHORT_COLUMN_LABELS: Record<"h24" | "h72" | "d7" | "d14" | "d30", string> = {
  h24: "24 ч",
  h72: "72 ч",
  d7: "7 дн",
  d14: "14 дн",
  d30: "30 дн",
};

export const AGGREGATE_LABELS: Record<CohortAggregate, string> = {
  median: "Медиана",
  sum: "Сумма",
  mean: "Среднее",
};

export const GRANULARITY_LABELS: Record<AnalyticsGranularity, string> = {
  day: "По дням",
  week: "По неделям",
};

export const PLATFORM_LABELS: Record<Platform, string> = { youtube: "YouTube", vk: "VK" };

export const ATTRIBUTION_LABELS: Record<AttributionLevel, string> = {
  exact: "Точная (ролик)",
  channel: "Канал",
  direct: "Прямая",
};

export const ATTRIBUTION_HINTS: Record<AttributionLevel, string> = {
  exact: "Переход по ссылке конкретного ролика",
  channel: "Ссылка канала без ролика",
  direct: "Старт без нашей ссылки",
};

export const FUNNEL_STEP_LABELS: Record<FunnelStepCode, string> = {
  views: "Просмотры",
  clicks: "Переходы",
  landing_views: "Лендинг",
  unique_visitors: "Посетители",
  to_telegram: "В Telegram",
  to_site: "На сайт",
  direct_bot_starts: "Старты напрямую",
  visitors: "Посетители",
  bot_starts: "Старты бота",
  users: "Пользователи",
  signups: "Регистрации",
  first_payments: "Первые оплаты",
};

export const AGE_POINT_LABELS: Record<AgePoint, string> = {
  h1: "1 ч",
  h2: "2 ч",
  h4: "4 ч",
  h8: "8 ч",
  h12: "12 ч",
  h24: "24 ч",
  h48: "48 ч",
  h72: "72 ч",
  d7: "7 дн",
  d14: "14 дн",
  d30: "30 дн",
};

/** Какой возрастной срез уже готов для вердикта. */
export const VERDICT_LABELS: Record<AgeVerdict, string> = {
  h24: "срез 24 ч",
  h72: "срез 72 ч",
  d7: "срез 7 дн",
};

export const LADDER_STATUS_META: StatusMetaMap<LadderStepStatus> = {
  done: { label: "снят", variant: "positive", icon: CircleCheck },
  due: { label: "пора", variant: "human", icon: Clock },
  missed: { label: "пропущен", variant: "negative", icon: TriangleAlert },
  future: { label: "впереди", variant: "neutral", icon: CircleDashed, outline: true },
};

export const RETENTION_FAILURE_LABELS: Record<RetentionFailureClass, string> = {
  hook_drop: "провал на крюке",
  middle_sag: "провис середины",
  no_loop_lift: "нет подъёма на зацикливании",
  none: "норма",
};

export const RETENTION_FAILURE_VARIANT: Record<RetentionFailureClass, "positive" | "warning" | "negative"> = {
  hook_drop: "negative",
  middle_sag: "warning",
  no_loop_lift: "warning",
  none: "positive",
};

/** «3 ч», «36 ч», «5 дн» — возраст ролика по часам. */
export function formatAgeHours(hours: number | null | undefined): string {
  if (hours === null || hours === undefined || Number.isNaN(hours)) return "—";
  if (hours < 1) return "<1 ч";
  if (hours < 48) return `${Math.round(hours)} ч`;
  return `${Math.floor(hours / 24)} дн`;
}

export const EMPTY_ICON: LucideIcon = CircleDashed;
