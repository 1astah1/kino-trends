"use client";

import { ArrowDown, ArrowUp, Minus } from "lucide-react";

import { Badge, Card, CardContent, InfoTooltip } from "@/components/ui";
import { formatPct } from "@/lib/format";
import type { KpiValue, MetricSeries, OverviewKpis } from "@/lib/queries/overview";

import { LazySparkline } from "./charts-lazy";
import { deltaTone, KPI_TILES, type KpiMeta } from "./metric-meta";

/** Дельта к прошлому периоду той же длины: знак, стрелка и цвет по направлению × «что хорошо». */
function DeltaBadge({ kpi, meta }: { kpi: KpiValue; meta: KpiMeta }) {
  if (kpi.value === null || kpi.previous === null || kpi.delta === null) return null;
  const tone = deltaTone(kpi, meta.good);
  const Icon = kpi.delta > 0 ? ArrowUp : kpi.delta < 0 ? ArrowDown : Minus;
  const sign = kpi.delta > 0 ? "+" : kpi.delta < 0 ? "−" : "";
  const magnitude = Math.abs(kpi.delta);
  const text =
    kpi.delta_pct === null
      ? `${sign}${meta.format(magnitude)}`
      : `${sign}${formatPct(Math.abs(kpi.delta_pct), Math.abs(kpi.delta_pct) < 10 ? 1 : 0)}`;
  return (
    <Badge variant={tone} aria-label={`К прошлому периоду: ${text}`} className="tabular font-mono">
      <Icon className="h-3 w-3" aria-hidden="true" />
      {text}
    </Badge>
  );
}

function KpiTile({ meta, kpi, points }: { meta: KpiMeta; kpi: KpiValue; points: Array<number | null> }) {
  return (
    <Card data-testid={`kpi-${meta.metric}`}>
      <CardContent className="flex flex-col gap-1">
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-xs font-medium text-fg-muted" title={meta.label}>
            {meta.label}
          </span>
          <InfoTooltip text={meta.hint} srLabel={`Определение: ${meta.label}`} />
        </div>
        <div className="flex items-end justify-between gap-2">
          <span className="text-lg font-semibold text-fg">{kpi.value === null ? "—" : meta.format(kpi.value)}</span>
          <DeltaBadge kpi={kpi} meta={meta} />
        </div>
        <span className="tabular font-mono text-2xs text-fg-muted">
          прошлый период: {kpi.previous === null ? "—" : meta.format(kpi.previous)}
        </span>
        <LazySparkline values={points} color={meta.color} height={28} />
      </CardContent>
    </Card>
  );
}

/** Плитки KPI со спарклайнами дневных точек тех же метрик. */
export function KpiGrid({ kpi, series }: { kpi: OverviewKpis; series: MetricSeries[] }) {
  const byMetric = new Map(series.map((item) => [item.metric, item.points]));
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5" data-testid="overview-kpi">
      {KPI_TILES.map((meta) => (
        <KpiTile
          key={meta.metric}
          meta={meta}
          kpi={kpi[meta.metric]}
          points={(byMetric.get(meta.metric) ?? []).map((point) => point.v)}
        />
      ))}
    </div>
  );
}
