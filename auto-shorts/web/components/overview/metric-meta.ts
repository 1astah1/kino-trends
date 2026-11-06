import type { ChartColor } from "@/components/charts/chart-utils";
import { BUSINESS_TIMEZONE, formatNumber, formatUsd } from "@/lib/format";
import { routeHref } from "@/lib/nav";
import type { FunnelStepCode, KpiValue, OverviewMetric, OverviewOnboarding, OverviewTodo } from "@/lib/queries/overview";

/**
 * Подписи, определения и форматы метрик Обзора. Русские подписи повторяют `METRIC_LABELS`
 * из src/autoshorts/api/schemas/analytics.py; формулы — src/autoshorts/analytics/formulas.py.
 */

// ---------------------------------------------------------------- период

export const RANGES = ["7d", "14d", "30d", "90d"] as const;
export type RangeId = (typeof RANGES)[number];
export const RANGE_DAYS: Record<RangeId, number> = { "7d": 7, "14d": 14, "30d": 30, "90d": 90 };
export const RANGE_OPTIONS: Array<{ value: RangeId; label: string }> = [
  { value: "7d", label: "7 дней" },
  { value: "14d", label: "14 дней" },
  { value: "30d", label: "30 дней" },
  { value: "90d", label: "90 дней" },
];

export const BASES = ["public", "engaged"] as const;
export const BASIS_OPTIONS: Array<{ value: (typeof BASES)[number]; label: string }> = [
  { value: "public", label: "Публичные" },
  { value: "engaged", label: "Учтённые" },
];

const isoDayFmt = new Intl.DateTimeFormat("en-CA", {
  timeZone: BUSINESS_TIMEZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

/** Сегодняшний бизнес-день `YYYY-MM-DD` (по Москве — так же считает сервер). */
export function businessToday(now: number = Date.now()): string {
  return isoDayFmt.format(now);
}

export function shiftDay(iso: string, days: number): string {
  const [year, month, day] = iso.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day + days)).toISOString().slice(0, 10);
}

/** `range` из URL → границы периода включительно, сегодня — последний день. */
export function rangeDates(range: RangeId, today: string): { dateFrom: string; dateTo: string } {
  return { dateFrom: shiftDay(today, -(RANGE_DAYS[range] - 1)), dateTo: today };
}

// ---------------------------------------------------------------- форматы

const rubWhole = new Intl.NumberFormat("ru-RU", { style: "currency", currency: "RUB", maximumFractionDigits: 0 });
const rubCents = new Intl.NumberFormat("ru-RU", { style: "currency", currency: "RUB", minimumFractionDigits: 2 });

/** Выручка приходит в копейках: `123456` → `1 234,56 ₽`, целые рубли без копеек. */
export function formatRubMinor(minor: number | null | undefined): string {
  if (minor === null || minor === undefined || Number.isNaN(minor)) return "—";
  const rub = minor / 100;
  return Number.isInteger(rub) ? rubWhole.format(rub) : rubCents.format(rub);
}

// ---------------------------------------------------------------- KPI

export type GoodDirection = "up" | "down" | "neutral";

export interface KpiMeta {
  metric: OverviewMetric;
  label: string;
  /** Определение — в подсказке у плитки. */
  hint: string;
  format: (value: number) => string;
  /** Цвет спарклайна: слот метрики или нейтральный. */
  color: ChartColor;
  /** Куда «хорошо»: расход и цены — вниз. */
  good: GoodDirection;
}

const count = (value: number) => formatNumber(value);

export const KPI_TILES: KpiMeta[] = [
  {
    metric: "views",
    label: "Просмотры",
    hint: "Просмотры роликов за период по снимкам площадок. Публичные счётчики YouTube с 24.08.2026 завышены на 20–30%.",
    format: count,
    color: 1,
    good: "up",
  },
  {
    metric: "visitors",
    label: "Посетители",
    hint: "Уникальные посетители лендинга плюс старты бота напрямую (не через лендинг).",
    format: count,
    color: 2,
    good: "up",
  },
  {
    metric: "bot_starts",
    label: "Старты бота",
    hint: "Запуски бота по ссылкам из роликов: с лендинга и напрямую.",
    format: count,
    color: 3,
    good: "up",
  },
  {
    metric: "signups",
    label: "Регистрации",
    hint: "События signup от продуктов через вебхук конверсий.",
    format: count,
    color: "muted",
    good: "up",
  },
  {
    metric: "first_payments",
    label: "Первые оплаты",
    hint: "Первая оплата пользователя, пришедшего из роликов (событие first_payment).",
    format: count,
    color: "muted",
    good: "up",
  },
  {
    metric: "revenue_minor",
    label: "Выручка",
    hint: "Сумма оплат из вебхуков продуктов в рублях (сервер хранит копейки).",
    format: formatRubMinor,
    color: "muted",
    good: "up",
  },
  {
    metric: "spend_usd",
    label: "Расход",
    hint: "Платные вызовы провайдеров по роликам периода, включая отклонённые и упавшие.",
    format: (value) => formatUsd(value, 2),
    color: "muted",
    good: "down",
  },
  {
    metric: "videos_published",
    label: "Опубликовано роликов",
    hint: "Публикации, зарегистрированные за период (по ссылке или автозаливом).",
    format: count,
    color: "muted",
    good: "up",
  },
  {
    metric: "ctr_per_1k_views",
    label: "Посетителей на 1000 просмотров",
    hint: "visitors / views × 1000 — сколько зрителей дошло до лендинга или бота.",
    format: (value) => formatNumber(value, 1),
    color: "muted",
    good: "up",
  },
  {
    metric: "cost_per_first_payment_usd",
    label: "Цена первой оплаты",
    hint: "Расход периода / первые оплаты. Нет оплат — прочерк, а не ноль.",
    format: (value) => formatUsd(value, 2),
    color: "muted",
    good: "down",
  },
];

export type DeltaTone = "positive" | "negative" | "neutral";

/** Цвет дельты: направление × что считается хорошим. */
export function deltaTone(kpi: KpiValue, good: GoodDirection): DeltaTone {
  if (kpi.delta === null || kpi.delta === 0 || good === "neutral") return "neutral";
  const up = kpi.delta > 0;
  return up === (good === "up") ? "positive" : "negative";
}

// ---------------------------------------------------------------- динамика

export interface TrendChartMeta {
  metric: OverviewMetric;
  title: string;
  color: ChartColor;
  kind: "line" | "bar";
}

/** Малые мультипликаторы: у каждой метрики своя ось — двойной оси нет. */
export const TREND_CHARTS: TrendChartMeta[] = [
  { metric: "views", title: "Просмотры", color: 1, kind: "line" },
  { metric: "clicks", title: "Переходы по ссылке", color: 2, kind: "line" },
  { metric: "bot_starts", title: "Старты бота", color: 3, kind: "line" },
  { metric: "videos_published", title: "Опубликовано роликов", color: "muted", kind: "bar" },
];

/** Короткая подпись аннотации на самом графике; полный текст — под графиками. */
export const ANNOTATION_SHORT: Record<string, string> = {
  yt_public_views_change: "YouTube: счётчики ×1,2–1,3",
};

// ---------------------------------------------------------------- воронка

export const FUNNEL_STEP_LABELS: Record<FunnelStepCode, string> = {
  views: "Просмотры",
  clicks: "Переходы",
  landing_views: "Показы лендинга",
  unique_visitors: "Уникальные посетители",
  to_telegram: "В Telegram",
  to_site: "На сайт",
  direct_bot_starts: "Старты напрямую",
  visitors: "Посетители",
  bot_starts: "Старты бота",
  users: "Пользователи",
  signups: "Регистрации",
  first_payments: "Первые оплаты",
};

// ---------------------------------------------------------------- «Сделать сейчас»

export interface TodoMeta {
  key: keyof OverviewTodo;
  label: string;
  href: string;
  tone: "human" | "negative" | "warning" | "accent";
}

export const TODO_ITEMS: TodoMeta[] = [
  { key: "pending_review", label: "Ждут приёмки", href: routeHref("/review"), tone: "human" },
  {
    key: "qc_failed",
    label: "Не прошли QC",
    href: routeHref("/production", { status: "qc_failed" }),
    tone: "negative",
  },
  {
    key: "packages_ready",
    label: "Пакеты готовы к заливке",
    href: routeHref("/packages", { status: "live" }),
    tone: "human",
  },
  {
    key: "packages_awaiting_url",
    label: "Скачаны, ждут ссылку на публикацию",
    href: routeHref("/packages", { status: "live" }),
    tone: "warning",
  },
  {
    key: "unverified_publications",
    label: "Публикации не подтверждены",
    href: routeHref("/packages", { status: "problems" }),
    tone: "negative",
  },
  {
    key: "manual_metrics_due",
    label: "Нужен ручной ввод метрик",
    href: routeHref("/analytics", { tab: "videos" }),
    tone: "warning",
  },
  {
    key: "verdicts_72h_ready",
    label: "Вердикты на 72 ч готовы",
    href: routeHref("/analytics", { tab: "videos" }),
    tone: "accent",
  },
  { key: "experiments_ready", label: "A/B-тесты с вердиктом", href: routeHref("/products"), tone: "accent" },
];

// ---------------------------------------------------------------- онбординг

export interface OnboardingStepMeta {
  key: string;
  label: string;
  href: string;
  done: (o: OverviewOnboarding) => boolean;
  hint: (o: OverviewOnboarding) => string;
}

export const ONBOARDING_STEPS: OnboardingStepMeta[] = [
  {
    key: "products",
    label: "Добавить продукт",
    href: routeHref("/products"),
    done: (o) => o.products > 0,
    hint: (o) => (o.products > 0 ? `${formatNumber(o.products)} в каталоге` : "Что рекламируем: лендинг, бот, UTM"),
  },
  {
    key: "channels",
    label: "Добавить канал",
    href: routeHref("/channels"),
    done: (o) => o.channels > 0,
    hint: (o) => (o.channels > 0 ? `${formatNumber(o.channels)} в списке` : "Куда публикуем: YouTube или VK"),
  },
  {
    key: "youtube_key",
    label: "Задать ключ YouTube Data API",
    href: routeHref("/settings", { tab: "providers" }),
    done: (o) => o.youtube_key_set,
    hint: (o) => (o.youtube_key_set ? "Ключ задан" : "Без ключа не собираются просмотры"),
  },
  {
    key: "webhook_secret",
    label: "Секрет вебхука у продукта",
    href: routeHref("/products"),
    done: (o) => o.webhook_secret_set,
    hint: (o) => (o.webhook_secret_set ? "Хотя бы у одного продукта" : "Иначе продукт не пришлёт конверсии"),
  },
  {
    key: "publications",
    label: "Первая публикация",
    href: routeHref("/packages"),
    done: (o) => o.publications > 0,
    hint: (o) => (o.publications > 0 ? `${formatNumber(o.publications)} зарегистрировано` : "Пакет → заливка → ссылка"),
  },
  {
    key: "conversions",
    label: "Первая конверсия",
    href: routeHref("/products"),
    done: (o) => o.conversions > 0,
    hint: (o) => (o.conversions > 0 ? `${formatNumber(o.conversions)} принято` : "Боевое событие от продукта"),
  },
];
