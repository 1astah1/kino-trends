"use client";

import dynamic from "next/dynamic";

import { Skeleton } from "@/components/ui";

/** recharts грузится отдельным чанком только на экранах разбора (бюджет оболочки §7.1). */
export const LazyTrendSeriesChart = dynamic(
  () => import("./trend-series-chart").then((module) => module.TrendSeriesChart),
  { ssr: false, loading: () => <Skeleton className="h-35 w-full" /> },
);
