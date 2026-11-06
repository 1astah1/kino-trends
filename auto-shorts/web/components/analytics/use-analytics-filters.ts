"use client";

import { parseAsStringLiteral, useQueryState } from "nuqs";
import { useCallback, useMemo, useState } from "react";

import { BUSINESS_TIMEZONE } from "@/lib/format";
import type { AnalyticsFilters } from "@/lib/queries/analytics";

/**
 * Строка фильтров `/analytics` в URL (lib/nav.ts urlState): date_from, date_to, platform, channel_id,
 * niche_id, product_id, basis, mode (+ cohort_age для mode=cohort). Даты — `YYYY-MM-DD` бизнес-дня
 * в МСК включительно; без дат в URL показываем последние 30 дней.
 */
export const RANGE_PRESETS = [7, 14, 30, 90] as const;
export type RangePreset = (typeof RANGE_PRESETS)[number];
const DEFAULT_DAYS: RangePreset = 30;

const PLATFORMS = ["youtube", "vk"] as const;
const BASES = ["public", "engaged"] as const;
const MODES = ["activity", "cohort"] as const;
const COHORT_AGES = ["h24", "h72", "d7", "d14", "d30", "lifetime"] as const;

const dayFmt = new Intl.DateTimeFormat("en-CA", {
  timeZone: BUSINESS_TIMEZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

/** Сегодняшний бизнес-день (МСК) как `YYYY-MM-DD`. */
export function todayBusinessDay(now: number = Date.now()): string {
  return dayFmt.format(now);
}

/** `YYYY-MM-DD` ± дней без учёта часовых поясов (это уже день, не момент). */
export function shiftDay(day: string, days: number): string {
  const [year, month, date] = day.split("-").map(Number);
  if (!year || !month || !date) return day;
  return new Date(Date.UTC(year, month - 1, date + days)).toISOString().slice(0, 10);
}

export function useAnalyticsFilters() {
  const [today] = useState(() => todayBusinessDay());
  const [dateFromRaw, setDateFromRaw] = useQueryState("date_from", { history: "replace" });
  const [dateToRaw, setDateToRaw] = useQueryState("date_to", { history: "replace" });
  const [platform, setPlatform] = useQueryState(
    "platform",
    parseAsStringLiteral(PLATFORMS).withOptions({ history: "replace" }),
  );
  const [channelId, setChannelId] = useQueryState("channel_id", { history: "replace" });
  const [nicheId, setNicheId] = useQueryState("niche_id", { history: "replace" });
  const [productId, setProductId] = useQueryState("product_id", { history: "replace" });
  const [basis, setBasis] = useQueryState(
    "basis",
    parseAsStringLiteral(BASES).withDefault("public").withOptions({ history: "replace" }),
  );
  const [mode, setMode] = useQueryState(
    "mode",
    parseAsStringLiteral(MODES).withDefault("activity").withOptions({ history: "replace" }),
  );
  const [cohortAge, setCohortAge] = useQueryState(
    "cohort_age",
    parseAsStringLiteral(COHORT_AGES).withDefault("lifetime").withOptions({ history: "replace" }),
  );

  const dateTo = dateToRaw || today;
  const dateFrom = dateFromRaw || shiftDay(dateTo, -(DEFAULT_DAYS - 1));

  const filters = useMemo<AnalyticsFilters>(
    () => ({
      dateFrom,
      dateTo,
      platform,
      channelId,
      nicheId,
      productId,
      basis,
      mode,
      cohortAge: mode === "cohort" ? cohortAge : null,
    }),
    [dateFrom, dateTo, platform, channelId, nicheId, productId, basis, mode, cohortAge],
  );

  const activePreset =
    RANGE_PRESETS.find((days) => dateTo === today && dateFrom === shiftDay(today, -(days - 1))) ?? null;

  const setPreset = useCallback(
    (days: RangePreset) => {
      void setDateFromRaw(shiftDay(today, -(days - 1)));
      void setDateToRaw(today);
    },
    [setDateFromRaw, setDateToRaw, today],
  );

  const setDateFrom = useCallback((value: string) => void setDateFromRaw(value || null), [setDateFromRaw]);
  const setDateTo = useCallback((value: string) => void setDateToRaw(value || null), [setDateToRaw]);

  return {
    filters,
    today,
    dateFrom,
    dateTo,
    setDateFrom,
    setDateTo,
    activePreset,
    setPreset,
    platform,
    setPlatform,
    channelId,
    setChannelId,
    nicheId,
    setNicheId,
    productId,
    setProductId,
    basis,
    setBasis,
    mode,
    setMode,
    cohortAge,
    setCohortAge,
  };
}

export type AnalyticsFilterState = ReturnType<typeof useAnalyticsFilters>;
