/**
 * Время и числа (docs/ARCHITECTURE.md §7.4): хранится UTC, показывается МСК с пометкой;
 * `Intl` ru-RU, tabular-nums в разметке, `$` с 3–4 знаками для себестоимости.
 */
export const BUSINESS_TIMEZONE = "Europe/Moscow";
export const TZ_MARK = "МСК";

const dateTimeFmt = new Intl.DateTimeFormat("ru-RU", {
  timeZone: BUSINESS_TIMEZONE,
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});
const shortDateTimeFmt = new Intl.DateTimeFormat("ru-RU", {
  timeZone: BUSINESS_TIMEZONE,
  day: "2-digit",
  month: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});
const timeFmt = new Intl.DateTimeFormat("ru-RU", {
  timeZone: BUSINESS_TIMEZONE,
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});
const dateFmt = new Intl.DateTimeFormat("ru-RU", {
  timeZone: BUSINESS_TIMEZONE,
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
});

function toDate(value: string | number | Date | null | undefined): Date | null {
  if (value === null || value === undefined || value === "") return null;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDateTime(value: string | number | Date | null | undefined, { short = false } = {}): string {
  const date = toDate(value);
  if (!date) return "—";
  return `${(short ? shortDateTimeFmt : dateTimeFmt).format(date)} ${TZ_MARK}`;
}

export function formatTime(value: string | number | Date | null | undefined): string {
  const date = toDate(value);
  return date ? `${timeFmt.format(date)} ${TZ_MARK}` : "—";
}

/** `YYYY-MM-DD` бизнес-дня (уже в МСК) → `26.09.2026`. */
export function formatDay(value: string | null | undefined): string {
  if (!value) return "—";
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (match) return `${match[3]}.${match[2]}.${match[1]}`;
  const date = toDate(value);
  return date ? dateFmt.format(date) : "—";
}

/** «12 с назад», «3 мин назад», «2 ч назад», «4 дн назад». */
export function formatAgo(value: string | number | Date | null | undefined, now: number = Date.now()): string {
  const date = toDate(value);
  if (!date) return "—";
  const seconds = Math.round((now - date.getTime()) / 1000);
  if (seconds < 0) return "только что";
  if (seconds < 60) return `${seconds} с назад`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} мин назад`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} ч назад`;
  return `${Math.floor(hours / 24)} дн назад`;
}

const numberFmtCache = new Map<string, Intl.NumberFormat>();
function numberFmt(min: number, max: number): Intl.NumberFormat {
  const key = `${min}:${max}`;
  let fmt = numberFmtCache.get(key);
  if (!fmt) {
    fmt = new Intl.NumberFormat("ru-RU", { minimumFractionDigits: min, maximumFractionDigits: max });
    numberFmtCache.set(key, fmt);
  }
  return fmt;
}

export function formatNumber(value: number | null | undefined, maxDigits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return numberFmt(0, maxDigits).format(value);
}

/** Деньги провайдеров `*_usd`: `$1,25`; для себестоимости `digits=4` → `$0,0512`. */
export function formatUsd(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `$${numberFmt(Math.min(2, digits), digits).format(value)}`;
}

export function formatPct(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${numberFmt(0, digits).format(value)}%`;
}

export function formatGb(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${numberFmt(0, value < 100 ? 1 : 0).format(value)} ГБ`;
}

export function formatMb(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (value >= 1024) return `${numberFmt(0, 1).format(value / 1024)} ГБ`;
  return `${numberFmt(0, 0).format(value)} МБ`;
}
