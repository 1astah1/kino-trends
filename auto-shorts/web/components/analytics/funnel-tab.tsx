"use client";

import { Filter } from "lucide-react";

import { SectionError } from "@/components/common/section-error";
import { EmptyState, SectionCard, Skeleton, SkeletonStatGrid, StatTile, StatTileGrid } from "@/components/ui";
import { formatNumber, formatUsd } from "@/lib/format";
import { type AnalyticsFilters, analyticsExportHref, useAnalyticsFunnel } from "@/lib/queries/analytics";

import { AttributionTable, BasisFallbackNote, CsvLink, FunnelBranch, TabToolbar } from "./shared";

const formatCost = (value: number | null) => (value === null ? "—" : formatUsd(value, value < 1 ? 4 : 2));

/** Воронка двух веток: бот и домен — полосами; цена шага, атрибуция. */
export function FunnelTab({ filters }: { filters: AnalyticsFilters }) {
  const funnel = useAnalyticsFunnel(filters);
  const data = funnel.data;
  const empty =
    data && data.views === 0 && [...data.branches.bot, ...data.branches.domain].every((step) => !step.value);

  return (
    <div className="flex flex-col gap-4" data-testid="analytics-funnel">
      <TabToolbar right={<CsvLink href={analyticsExportHref(filters, { report: "funnel" })} />}>
        {data ? (
          <span className="text-xs text-fg-muted">
            {formatNumber(data.views)} просмотров за период · расход {formatUsd(data.costs.spend_usd, 2)}
          </span>
        ) : null}
      </TabToolbar>

      <BasisFallbackNote show={data?.basis_fallback} />

      {funnel.isError ? (
        <SectionError error={funnel.error} onRetry={() => void funnel.refetch()} title="Воронка не загрузилась" />
      ) : !data ? (
        <>
          <SkeletonStatGrid count={4} />
          <div className="grid gap-4 lg:grid-cols-2">
            <Skeleton className="h-64" />
            <Skeleton className="h-64" />
          </div>
        </>
      ) : empty ? (
        <EmptyState
          icon={Filter}
          title="Воронка пока пустая"
          description="За период не было ни просмотров, ни переходов. Как только ролики наберут просмотры, а по ссылкам пойдут клики, здесь появятся обе ветки: бот и сайт."
        />
      ) : (
        <>
          <StatTileGrid>
            <StatTile label="Расход за период" value={formatUsd(data.costs.spend_usd, 2)} />
            <StatTile label="Цена посетителя" value={formatCost(data.costs.cost_per_visitor_usd)} />
            <StatTile label="Цена старта бота" value={formatCost(data.costs.cost_per_bot_start_usd)} />
            <StatTile label="Цена первой оплаты" value={formatCost(data.costs.cost_per_first_payment_usd)} />
          </StatTileGrid>
          <div className="grid gap-4 lg:grid-cols-2">
            <SectionCard title="Ветка бота">
              <p className="text-xs text-fg-muted">
                Просмотры → в Telegram и старты напрямую → старты бота → пользователи → первые оплаты.
              </p>
              <FunnelBranch steps={data.branches.bot} formatCost={formatCost} />
            </SectionCard>
            <SectionCard title="Ветка домена">
              <p className="text-xs text-fg-muted">
                Просмотры → лендинг → посетители → на сайт → регистрации → первые оплаты.
              </p>
              <FunnelBranch steps={data.branches.domain} formatCost={formatCost} />
            </SectionCard>
          </div>
          <SectionCard title="Атрибуция конверсий">
            <AttributionTable attribution={data.attribution} />
          </SectionCard>
        </>
      )}
    </div>
  );
}
