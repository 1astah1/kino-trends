"use client";

import { ArrowUpRight } from "lucide-react";
import Link from "next/link";
import { parseAsStringLiteral, useQueryState } from "nuqs";

import { PageHeader } from "@/components/common/page-header";
import { SectionError } from "@/components/common/section-error";
import { Badge, Callout, PillToggle, SectionCard, Select, Skeleton, SkeletonStatGrid, SkeletonTable } from "@/components/ui";
import { cn } from "@/lib/cn";
import { formatDay, formatTime } from "@/lib/format";
import { useNow } from "@/lib/hooks/use-now";
import { routeHref } from "@/lib/nav";
import { useChannels } from "@/lib/queries/channels";
import { useOverview } from "@/lib/queries/overview";

import { AlertsList } from "./alerts-list";
import { FunnelBars } from "./funnel-bars";
import { HealthLine } from "./health-line";
import { KpiGrid } from "./kpi-grid";
import { BASES, BASIS_OPTIONS, businessToday, RANGE_OPTIONS, rangeDates, RANGES } from "./metric-meta";
import { OnboardingChecklist } from "./onboarding-checklist";
import { TodoList } from "./todo-list";
import { TopVideos } from "./top-videos";
import { TrendCharts } from "./trend-charts";

function OverviewSkeleton() {
  return (
    <div className="flex flex-col gap-4" aria-busy="true" aria-label="Загрузка сводки">
      <SkeletonStatGrid count={10} className="sm:grid-cols-3 lg:grid-cols-5" />
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="flex flex-col gap-4">
          <Skeleton className="h-72 rounded-lg" />
          <Skeleton className="h-56 rounded-lg" />
          <SkeletonTable rows={5} />
        </div>
        <div className="flex flex-col gap-4">
          <Skeleton className="h-48 rounded-lg" />
          <Skeleton className="h-40 rounded-lg" />
        </div>
      </div>
    </div>
  );
}

function SectionLink({ href, children }: { href: string; children: string }) {
  return (
    <Link href={href} className="inline-flex items-center gap-1 text-xs text-accent hover:underline">
      {children}
      <ArrowUpRight className="h-3 w-3" aria-hidden="true" />
    </Link>
  );
}

/**
 * Обзор — «Пульт» (§7.3 `/?range=&channel=&basis=`): KPI с дельтами и спарклайнами, динамика по дням
 * малыми мультипликаторами, «Сделать сейчас», мини-воронка двух веток, алерты, топ роликов, бюджет и
 * свежесть данных. Пустая база — чек-лист первого запуска вместо «сломанного» экрана. Один запрос
 * `GET /overview`, границы периода считаются на клиенте по бизнес-дню (МСК).
 */
export function OverviewScreen() {
  const [range, setRange] = useQueryState(
    "range",
    parseAsStringLiteral(RANGES).withDefault("14d").withOptions({ history: "replace" }),
  );
  const [channelId, setChannelId] = useQueryState("channel", { history: "replace" });
  const [basis, setBasis] = useQueryState(
    "basis",
    parseAsStringLiteral(BASES).withDefault("public").withOptions({ history: "replace" }),
  );
  const now = useNow(60_000);
  const today = businessToday(now);
  const { dateFrom, dateTo } = rangeDates(range, today);
  const overview = useOverview({ dateFrom, dateTo, channelId, basis });
  const channels = useChannels();
  const data = overview.data;
  const periodFrom = data?.range.date_from ?? dateFrom;
  const periodTo = data?.range.date_to ?? dateTo;

  return (
    <div className="flex flex-col gap-4 p-4 md:p-6" data-testid="overview-screen">
      <PageHeader
        eyebrow="Пульт"
        title="Обзор"
        description="Что происходит с роликами и деньгами за период: KPI к прошлому периоду той же длины, динамика по дням, воронка двух веток и то, что ждёт решения оператора."
        actions={
          overview.dataUpdatedAt ? (
            <span className="text-xs text-fg-muted">
              обновлено {formatTime(overview.dataUpdatedAt)}
              {overview.isFetching ? " · …" : ""}
            </span>
          ) : null
        }
      />

      <div className="flex flex-wrap items-center gap-2">
        <PillToggle
          label="Период"
          options={RANGE_OPTIONS}
          value={range}
          onChange={(value) => void setRange(value)}
        />
        <Select
          aria-label="Канал"
          className="h-7 w-56 text-xs"
          value={channelId ?? ""}
          onChange={(event) => void setChannelId(event.target.value || null)}
          data-testid="overview-channel-filter"
        >
          <option value="">Все каналы</option>
          {(channels.data?.items ?? []).map((channel) => (
            <option key={channel.id} value={channel.id}>
              {channel.title || channel.id} · {channel.platform === "youtube" ? "YouTube" : "VK"}
            </option>
          ))}
        </Select>
        <PillToggle
          label="База просмотров"
          options={BASIS_OPTIONS}
          value={basis}
          onChange={(value) => void setBasis(value)}
        />
        <span className="tabular ml-auto font-mono text-xs text-fg-muted">
          {formatDay(periodFrom)} – {formatDay(periodTo)}
          {data ? ` · против ${formatDay(data.previous_range.date_from)} – ${formatDay(data.previous_range.date_to)}` : ""}
        </span>
      </div>

      {data?.basis_fallback ? (
        <Callout variant="warning" title="Показаны публичные просмотры">
          Учтённые просмотры (engaged) требуют YouTube Analytics по OAuth, которого пока нет, — сводка посчитана
          по публичным счётчикам.
        </Callout>
      ) : null}

      {overview.isError ? (
        <SectionError error={overview.error} onRetry={() => void overview.refetch()} title="Не удалось загрузить сводку" />
      ) : !data ? (
        <OverviewSkeleton />
      ) : (
        <div
          className={cn(
            "flex flex-col gap-4 transition-opacity duration-150",
            overview.isPlaceholderData && "opacity-60",
          )}
        >
          {!data.onboarding.done ? <OnboardingChecklist onboarding={data.onboarding} /> : null}

          <KpiGrid kpi={data.kpi} series={data.series} />

          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
            <div className="flex min-w-0 flex-col gap-4">
              <SectionCard
                title="Динамика по дням"
                actions={<SectionLink href={routeHref("/analytics", { tab: "funnel" })}>Аналитика</SectionLink>}
              >
                <TrendCharts series={data.series} annotations={data.annotations} />
              </SectionCard>
              <SectionCard
                title="Воронка за период"
                actions={<SectionLink href={routeHref("/analytics", { tab: "funnel" })}>Подробно</SectionLink>}
              >
                <FunnelBars funnel={data.funnel} />
              </SectionCard>
              <SectionCard
                title="Топ роликов за период"
                actions={<SectionLink href={routeHref("/analytics", { tab: "videos" })}>Все ролики</SectionLink>}
                bodyClassName="p-0"
              >
                <TopVideos rows={data.top_videos} now={now} />
              </SectionCard>
            </div>
            <div className="flex min-w-0 flex-col gap-4">
              <SectionCard title="Сделать сейчас">
                <TodoList todo={data.todo} />
              </SectionCard>
              <SectionCard
                title="Алерты"
                actions={
                  data.alerts_open > 0 ? (
                    <Badge variant={data.alerts.some((alert) => alert.severity === "critical") ? "negative" : "warning"}>
                      {data.alerts_open} открыто
                    </Badge>
                  ) : null
                }
              >
                <AlertsList alerts={data.alerts} open={data.alerts_open} />
              </SectionCard>
            </div>
          </div>

          <HealthLine budget={data.budget} freshness={data.data_freshness} now={now} />
        </div>
      )}
    </div>
  );
}
