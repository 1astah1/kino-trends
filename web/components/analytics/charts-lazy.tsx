"use client";

import dynamic from "next/dynamic";

import { Skeleton } from "@/components/ui";

// recharts грузится отдельным чанком только на экранах аналитики (бюджет оболочки, §7.1).
const ChartSkeleton = () => <Skeleton className="h-64 w-full" />;

export const TimeseriesChart = dynamic(() => import("./charts").then((module) => module.TimeseriesChart), {
  ssr: false,
  loading: ChartSkeleton,
});

export const AgeViewsChart = dynamic(() => import("./charts").then((module) => module.AgeViewsChart), {
  ssr: false,
  loading: ChartSkeleton,
});

export const RetentionChart = dynamic(() => import("./charts").then((module) => module.RetentionChart), {
  ssr: false,
  loading: ChartSkeleton,
});

export const TrafficChart = dynamic(() => import("./charts").then((module) => module.TrafficChart), {
  ssr: false,
  loading: ChartSkeleton,
});
