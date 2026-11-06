"use client";

import dynamic from "next/dynamic";

import { Skeleton } from "@/components/ui";

/**
 * Графики грузятся отдельным чанком при первом показе Обзора (бюджет оболочки §7.1): recharts
 * не попадает в общий бандл. `ssr: false` — статический экспорт рисует скелетон той же высоты.
 */
export const LazyTimeSeriesChart = dynamic(
  () => import("@/components/charts/line-chart").then((module) => module.TimeSeriesChart),
  { ssr: false, loading: () => <Skeleton className="h-55 w-full" /> },
);

export const LazyCategoryBarChart = dynamic(
  () => import("@/components/charts/bar-chart").then((module) => module.CategoryBarChart),
  { ssr: false, loading: () => <Skeleton className="h-55 w-full" /> },
);

export const LazySparkline = dynamic(
  () => import("@/components/charts/sparkline").then((module) => module.Sparkline),
  { ssr: false, loading: () => <span className="block h-7 w-full" aria-hidden="true" /> },
);
