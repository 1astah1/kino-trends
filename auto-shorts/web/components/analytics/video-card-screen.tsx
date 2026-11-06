"use client";

import { ArrowLeft, ExternalLink, Film, RefreshCw, Workflow } from "lucide-react";
import Link from "next/link";
import { parseAsInteger, useQueryState } from "nuqs";
import { toast } from "sonner";

import { useCurrentUser } from "@/components/auth/auth-context";
import { PageHeader } from "@/components/common/page-header";
import { SectionError } from "@/components/common/section-error";
import { CONVERSION_EVENT_LABELS, PUBLICATION_STATUS_META } from "@/components/packages/labels";
import {
  Badge,
  Button,
  DataTable,
  type DataTableColumn,
  DetailGrid,
  EmptyState,
  KeyValue,
  SectionCard,
  Skeleton,
  SkeletonStatGrid,
  StatTile,
  StatTileGrid,
  StatusBadge,
  Table,
  TableBody,
  TableHead,
  TableRow,
  Td,
  Th,
} from "@/components/ui";
import { errorMessage, isApiError } from "@/lib/api/errors";
import { mediaSrc } from "@/lib/api/media";
import { canOperate } from "@/lib/auth";
import { cn } from "@/lib/cn";
import { formatAgo, formatDateTime, formatNumber, formatPct, formatUsd } from "@/lib/format";
import { useNow } from "@/lib/hooks/use-now";
import { routeHref } from "@/lib/nav";
import {
  analyticsExportHref,
  type ConversionCount,
  type CostItem,
  type LadderStep,
  type MetricsSnapshot,
  type PublicationRetention,
  type VideoAnalytics,
  usePublicationRetention,
  useRefreshPublicationMetrics,
  useVideoAnalytics,
} from "@/lib/queries/analytics";
import { useJob } from "@/lib/queries/jobs";

import { AgeViewsChart, RetentionChart, TrafficChart } from "./charts-lazy";
import {
  formatAgeHours,
  formatRatio,
  formatRub,
  formatRubMinor,
  LADDER_STATUS_META,
  PLATFORM_LABELS,
  RETENTION_FAILURE_LABELS,
  RETENTION_FAILURE_VARIANT,
  VERDICT_LABELS,
} from "./labels";
import { AnnotationList, CsvLink, FunnelBranch } from "./shared";

const price = (value: number | null) => (value === null ? "—" : formatUsd(value, value < 1 ? 4 : 2));

/** Цвет подписи ступени — статически, чтобы Tailwind собрал классы. */
const LADDER_TEXT: Record<LadderStep["status"], string> = {
  done: "text-positive",
  due: "text-human",
  missed: "text-negative",
  future: "text-fg-muted",
};

const SOURCE_LABELS: Record<MetricsSnapshot["source"], string> = {
  yt_data: "YouTube Data",
  yt_analytics: "YouTube Analytics",
  vk_api: "VK API",
  ytdlp: "yt-dlp",
  manual: "вручную",
};

/** Лесенка опроса: ровные возраста, снят/пора/пропущен/впереди. */
function Ladder({ steps, now }: { steps: LadderStep[]; now: number }) {
  if (!steps.length) return <p className="text-sm text-fg-muted">Лестница опроса не построена.</p>;
  return (
    <ol className="flex flex-wrap gap-1.5">
      {steps.map((step) => {
        const meta = LADDER_STATUS_META[step.status];
        const Icon = meta.icon;
        const when =
          step.status === "done" && step.captured_at
            ? `снят ${formatAgo(step.captured_at, now)}`
            : step.due_at
              ? `${step.status === "future" ? "ожидается" : "срок"} ${formatDateTime(step.due_at, { short: true })}`
              : null;
        return (
          <li
            key={step.step}
            className={cn(
              "flex min-w-20 flex-col items-center gap-0.5 rounded-md border px-2 py-1.5 text-center",
              step.status === "done" && "border-positive/40 bg-positive/10",
              step.status === "due" && "border-human/40 bg-human/10",
              step.status === "missed" && "border-negative/40 bg-negative/10",
              step.status === "future" && "border-dashed border-border",
            )}
            title={when ?? undefined}
          >
            <span className="tabular font-mono text-xs font-semibold text-fg">{formatAgeHours(step.age_hours)}</span>
            <span className={cn("flex items-center gap-1 text-2xs", LADDER_TEXT[step.status])}>
              <Icon className="h-3 w-3" aria-hidden="true" />
              {meta.label}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function RetentionBlock({ query }: { query: ReturnType<typeof usePublicationRetention> }) {
  if (query.isPending) return <Skeleton className="h-56 w-full" />;
  if (query.isError) {
    const status = isApiError(query.error) ? query.error.status : 0;
    if (status === 404 || status === 501) {
      return (
        <EmptyState
          compact
          title="Удержание недоступно"
          description="Кривую удержания отдаёт YouTube Analytics через OAuth канала. Без подключения здесь пусто — это не ошибка."
        />
      );
    }
    return <SectionError error={query.error} onRetry={() => void query.refetch()} title="Удержание не загрузилось" />;
  }
  const retention: PublicationRetention = query.data;
  if (!retention.points.length) {
    return (
      <EmptyState
        compact
        title="Кривой удержания ещё нет"
        description="YouTube Analytics отдаёт её на возрастах 72 ч, 7 и 30 дней."
      />
    );
  }
  return (
    <>
      <div className="flex flex-wrap items-center gap-3 text-xs text-fg-muted">
        {retention.failure_class ? (
          <Badge variant={RETENTION_FAILURE_VARIANT[retention.failure_class]}>
            {RETENTION_FAILURE_LABELS[retention.failure_class]}
          </Badge>
        ) : null}
        {retention.retention_3s !== null ? (
          <span>
            Досматривают 3 с:{" "}
            <span className="tabular font-mono text-fg">{formatRatio(retention.retention_3s, 1)}</span>
          </span>
        ) : null}
        {retention.duration_sec !== null ? <span>Длительность {formatNumber(retention.duration_sec)} с</span> : null}
        {retention.captured_at ? <span>снято {formatDateTime(retention.captured_at, { short: true })}</span> : null}
      </div>
      <RetentionChart points={retention.points} />
    </>
  );
}

/**
 * Карточка ролика (§7.3 `/analytics/video?id=`): просмотры от возраста на фоне коридора p25–p75 канала,
 * лесенка замеров, удержание с классом провала, переходы по часам, воронка, конверсии, стоимость.
 */
export function VideoCardScreen() {
  const [id] = useQueryState("id", parseAsInteger);
  const user = useCurrentUser();
  const operator = canOperate(user.role);
  const now = useNow(30_000);
  const video = useVideoAnalytics(id);
  const retention = usePublicationRetention(id);
  const refresh = useRefreshPublicationMetrics(id ?? 0);
  const jobId = video.data?.publication.job_id ?? null;
  const job = useJob(jobId);

  if (!id) {
    return (
      <div className="p-4 md:p-6" data-testid="video-card-screen">
        <EmptyState
          icon={Film}
          title="Ролик не выбран"
          description="Откройте карточку из таблицы роликов: Аналитика → Ролики → строка. В адресе должен быть параметр id публикации."
          action={
            <Link href={routeHref("/analytics", { tab: "videos" })} className="text-sm text-accent hover:underline">
              К таблице роликов
            </Link>
          }
        />
      </div>
    );
  }

  const data: VideoAnalytics | undefined = video.data;
  const publication = data?.publication;
  const poster = mediaSrc(job.data?.media.poster_url);
  const title = job.data?.topic || job.data?.script?.hook_text || publication?.video_uid || `Публикация ${id}`;

  const onRefresh = async () => {
    try {
      await refresh.mutateAsync();
      toast.success("Запрошено", { description: "Внеочередной опрос метрик поставлен в очередь коллектора." });
    } catch (error) {
      toast.error("Опрос не запрошен", { description: errorMessage(error) });
    }
  };

  const conversionColumns: Array<DataTableColumn<ConversionCount>> = [
    { key: "type", header: "Событие", render: (row) => CONVERSION_EVENT_LABELS[row.event_type] ?? row.event_type },
    {
      key: "count",
      header: "Событий",
      className: "text-right",
      render: (row) => <span className="tabular font-mono text-xs">{formatNumber(row.count)}</span>,
    },
    {
      key: "users",
      header: "Уникальных",
      className: "text-right",
      render: (row) => <span className="tabular font-mono text-xs">{formatNumber(row.unique_users)}</span>,
    },
    {
      key: "landing",
      header: "Через лендинг",
      className: "text-right",
      render: (row) => <span className="tabular font-mono text-xs">{formatNumber(row.via_landing)}</span>,
    },
    {
      key: "amount",
      header: "Сумма",
      className: "text-right",
      render: (row) => (
        <span className="tabular font-mono text-xs">{row.amount_minor ? formatRubMinor(row.amount_minor) : "—"}</span>
      ),
    },
  ];

  const costColumns: Array<DataTableColumn<CostItem>> = [
    { key: "step", header: "Шаг", render: (row) => row.step_name },
    {
      key: "provider",
      header: "Провайдер",
      render: (row) => <span className="font-mono text-xs">{row.provider}</span>,
    },
    {
      key: "calls",
      header: "Вызовов",
      className: "text-right",
      render: (row) => <span className="tabular font-mono text-xs">{formatNumber(row.calls)}</span>,
    },
    {
      key: "cost",
      header: "Стоимость",
      className: "text-right",
      render: (row) => <span className="tabular font-mono text-xs">{formatUsd(row.cost_usd, 4)}</span>,
    },
  ];

  const snapshotColumns: Array<DataTableColumn<MetricsSnapshot>> = [
    {
      key: "at",
      header: "Снято",
      render: (row) => (
        <span className="tabular font-mono text-xs">{formatDateTime(row.captured_at, { short: true })}</span>
      ),
    },
    {
      key: "age",
      header: "Возраст",
      className: "text-right",
      render: (row) => <span className="tabular font-mono text-xs">{formatAgeHours(row.age_hours)}</span>,
    },
    {
      key: "views",
      header: "Просмотры",
      className: "text-right",
      render: (row) => <span className="tabular font-mono text-xs">{formatNumber(row.views)}</span>,
    },
    {
      key: "likes",
      header: "Лайки",
      className: "text-right",
      render: (row) => <span className="tabular font-mono text-xs">{formatNumber(row.likes)}</span>,
    },
    {
      key: "comments",
      header: "Комментарии",
      className: "text-right",
      render: (row) => <span className="tabular font-mono text-xs">{formatNumber(row.comments)}</span>,
    },
    {
      key: "source",
      header: "Источник",
      render: (row) => (
        <span className="text-xs text-fg-muted">
          {SOURCE_LABELS[row.source] ?? row.source}
          {row.note ? ` · ${row.note}` : null}
        </span>
      ),
    },
  ];

  return (
    <div className="flex flex-col gap-4 p-4 md:p-6" data-testid="video-card-screen">
      <Link
        href={routeHref("/analytics", { tab: "videos" })}
        className="inline-flex items-center gap-1 self-start text-xs text-fg-muted hover:text-fg"
      >
        <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />К таблице роликов
      </Link>

      {video.isError ? (
        <SectionError error={video.error} onRetry={() => void video.refetch()} title="Карточка ролика не загрузилась" />
      ) : !data || !publication ? (
        <>
          <Skeleton className="h-20 w-full max-w-2xl" />
          <SkeletonStatGrid count={6} />
          <Skeleton className="h-64" />
        </>
      ) : (
        <>
          <div className="flex flex-wrap items-start gap-4">
            <span className="flex h-28 w-16 shrink-0 items-center justify-center overflow-hidden rounded-md bg-surface-2">
              {poster ? (
                // eslint-disable-next-line @next/next/no-img-element -- постер из API, без оптимизатора (images.unoptimized)
                <img src={poster} alt="" className="h-full w-full object-cover" />
              ) : (
                <Film className="h-5 w-5 text-fg-muted" aria-hidden="true" />
              )}
            </span>
            <div className="min-w-0 flex-1">
              <PageHeader
                eyebrow={
                  <span className="font-mono normal-case">
                    {publication.video_uid} · публикация {publication.id}
                  </span>
                }
                title={title}
                description={
                  <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
                    <span>
                      {publication.channel.title || publication.channel.id} · {PLATFORM_LABELS[publication.platform]}
                    </span>
                    {publication.published_at ? (
                      <span>
                        опубликован {formatDateTime(publication.published_at, { short: true })} ·{" "}
                        {formatAgo(publication.published_at, now).replace(" назад", "")}
                      </span>
                    ) : null}
                    {publication.url ? (
                      <a
                        href={publication.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 text-accent hover:underline"
                      >
                        открыть на площадке
                        <ExternalLink className="h-3 w-3" aria-hidden="true" />
                      </a>
                    ) : null}
                    <Link
                      href={routeHref("/production", { job: String(publication.job_id) })}
                      className="inline-flex items-center gap-1 text-accent hover:underline"
                    >
                      <Workflow className="h-3 w-3" aria-hidden="true" />
                      задание #{publication.job_id}
                    </Link>
                  </span>
                }
                actions={
                  <>
                    <StatusBadge status={publication.status} map={PUBLICATION_STATUS_META} />
                    {publication.verdict ? <Badge variant="accent">{VERDICT_LABELS[publication.verdict]}</Badge> : null}
                    {publication.freshness?.stale ? (
                      <Badge variant="warning" title="Опрос метрик просрочен">
                        устарело
                      </Badge>
                    ) : null}
                    {publication.freshness?.manual_due ? (
                      <Badge variant="human" title="Ждёт ручного ввода метрик">
                        ручной ввод
                      </Badge>
                    ) : null}
                    <CsvLink
                      href={analyticsExportHref(
                        { dateFrom: null, dateTo: null },
                        { report: "video", publicationId: publication.id },
                      )}
                    />
                    {operator ? (
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={refresh.isPending}
                        onClick={() => void onRefresh()}
                        data-testid="refresh-metrics"
                      >
                        <RefreshCw
                          className={cn("h-3.5 w-3.5", refresh.isPending && "animate-spin")}
                          aria-hidden="true"
                        />
                        Обновить метрики
                      </Button>
                    ) : null}
                  </>
                }
              />
            </div>
          </div>

          <StatTileGrid className="lg:grid-cols-6">
            <StatTile
              label="Просмотры"
              value={formatNumber(publication.latest.views)}
              hint={
                publication.latest.captured_at
                  ? `снимок ${formatAgo(publication.latest.captured_at, now)}`
                  : "снимков пока нет"
              }
            />
            <StatTile
              label="Лайки · комментарии"
              value={`${formatNumber(publication.latest.likes)} · ${formatNumber(publication.latest.comments)}`}
            />
            <StatTile
              label="Переходы"
              value={formatNumber(data.totals.clicks)}
              hint={`посетители ${formatNumber(data.totals.visitors)}`}
            />
            <StatTile
              label="Старты бота"
              value={formatNumber(data.totals.bot_starts)}
              hint={`пользователи ${formatNumber(data.totals.users)}`}
            />
            <StatTile
              label="CTR на 1000"
              value={data.totals.ctr_per_1k_views === null ? "—" : formatNumber(data.totals.ctr_per_1k_views, 2)}
              hint={
                data.totals.cost_per_visitor_usd !== null
                  ? `посетитель ${price(data.totals.cost_per_visitor_usd)}`
                  : undefined
              }
            />
            <StatTile
              label="Стоимость"
              value={formatUsd(data.costs.total_usd, 3)}
              hint={
                data.costs.romi !== null
                  ? `ROMI ${formatPct(data.costs.romi * 100, 0)}`
                  : `выручка ${formatRubMinor(data.costs.revenue_minor)}`
              }
            />
          </StatTileGrid>

          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_380px]">
            <SectionCard title="Просмотры от возраста">
              <p className="text-xs text-fg-muted">
                Снимки интерполированы на ровные возраста без экстраполяции; на фоне — коридор p25–p75 и медиана роликов
                того же канала.
              </p>
              <AgeViewsChart points={data.age_views} />
              <AnnotationList annotations={data.annotations} />
            </SectionCard>
            <div className="flex flex-col gap-4">
              <SectionCard title="Лесенка замеров">
                <Ladder steps={data.ladder} now={now} />
                {publication.freshness ? (
                  <p className="text-2xs text-fg-muted">
                    {publication.freshness.collector
                      ? `коллектор ${SOURCE_LABELS[publication.freshness.collector] ?? publication.freshness.collector}`
                      : "коллектор не назначен"}
                    {publication.freshness.next_poll_at
                      ? ` · следующий опрос ${formatDateTime(publication.freshness.next_poll_at, { short: true })}`
                      : null}
                    {publication.freshness.last_poll_error
                      ? ` · ошибка: ${publication.freshness.last_poll_error}`
                      : null}
                  </p>
                ) : null}
              </SectionCard>
              <SectionCard title="Срезы по возрасту">
                <DetailGrid className="sm:grid-cols-3">
                  <KeyValue label="24 ч" value={formatNumber(publication.age_views.h24)} />
                  <KeyValue label="72 ч" value={formatNumber(publication.age_views.h72)} />
                  <KeyValue label="7 дн" value={formatNumber(publication.age_views.d7)} />
                </DetailGrid>
              </SectionCard>
            </div>
          </div>

          <SectionCard title="Удержание">
            <RetentionBlock query={retention} />
          </SectionCard>

          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_380px]">
            <SectionCard title={`Трафик ${data.traffic_granularity === "hour" ? "по часам" : "по дням"}`}>
              <TrafficChart points={data.traffic} granularity={data.traffic_granularity} />
              <DetailGrid className="sm:grid-cols-5">
                <KeyValue label="Переходы" value={formatNumber(data.totals.clicks)} />
                <KeyValue label="Посетители" value={formatNumber(data.totals.unique_visitors)} />
                <KeyValue label="В Telegram" value={formatNumber(data.totals.to_telegram)} />
                <KeyValue label="На сайт" value={formatNumber(data.totals.to_site)} />
                <KeyValue label="Старты бота" value={formatNumber(data.totals.bot_starts)} />
              </DetailGrid>
            </SectionCard>
            <SectionCard title="Воронка ролика">
              <p className="font-mono text-2xs tracking-wider text-fg-muted uppercase">Бот</p>
              <FunnelBranch steps={data.funnel.bot} formatCost={price} />
              <p className="mt-2 font-mono text-2xs tracking-wider text-fg-muted uppercase">Домен</p>
              <FunnelBranch steps={data.funnel.domain} formatCost={price} />
            </SectionCard>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <SectionCard title="Конверсии" bodyClassName="p-0">
              <DataTable
                caption="Конверсии ролика по типам"
                columns={conversionColumns}
                rows={data.conversions}
                rowKey={(row) => row.event_type}
                emptyTitle="Конверсий пока нет"
                emptyDescription="Старты бота, регистрации и оплаты с точной атрибуцией к этому ролику."
                minWidth={420}
              />
              {data.recent_conversions.length ? (
                <div className="border-t border-border p-3">
                  <p className="mb-1.5 font-mono text-2xs tracking-wider text-fg-muted uppercase">Последние события</p>
                  <Table minWidth={360}>
                    <TableHead>
                      <tr>
                        <Th>Когда</Th>
                        <Th>Событие</Th>
                        <Th className="text-right">Сумма</Th>
                      </tr>
                    </TableHead>
                    <TableBody>
                      {data.recent_conversions.map((event, index) => (
                        <TableRow key={`${event.occurred_at}-${index}`}>
                          <Td className="tabular font-mono text-xs">
                            {formatDateTime(event.occurred_at, { short: true })}
                          </Td>
                          <Td className="text-xs">
                            {CONVERSION_EVENT_LABELS[event.event_type] ?? event.event_type}
                            {event.via_landing ? <span className="text-fg-muted"> · через лендинг</span> : null}
                          </Td>
                          <Td className="tabular text-right font-mono text-xs">
                            {event.amount_minor ? formatRubMinor(event.amount_minor) : "—"}
                          </Td>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              ) : null}
            </SectionCard>
            <SectionCard title="Стоимость">
              <DetailGrid className="sm:grid-cols-3">
                <KeyValue
                  label="Всего"
                  value={`${formatUsd(data.costs.total_usd, 4)} · ${formatRub(data.costs.total_rub)}`}
                />
                <KeyValue label="Сам ролик" value={formatUsd(data.costs.job_cost_usd, 4)} />
                <KeyValue
                  label="Доли источника и брифа"
                  value={`${formatUsd(data.costs.source_share_usd, 4)} · ${formatUsd(data.costs.brief_share_usd, 4)}`}
                />
                <KeyValue label="Цена посетителя" value={price(data.costs.cost_per_visitor_usd)} />
                <KeyValue label="Цена старта бота" value={price(data.costs.cost_per_bot_start_usd)} />
                <KeyValue label="Цена первой оплаты" value={price(data.costs.cost_per_first_payment_usd)} />
                <KeyValue label="Цена пользователя" value={price(data.costs.cost_per_user_usd)} />
                <KeyValue label="Выручка" value={formatRubMinor(data.costs.revenue_minor)} />
                <KeyValue
                  label="ROMI"
                  value={
                    data.costs.romi === null ? (
                      "—"
                    ) : (
                      <span className={data.costs.romi >= 0 ? "text-positive" : "text-negative"}>
                        {formatPct(data.costs.romi * 100, 0)}
                      </span>
                    )
                  }
                />
              </DetailGrid>
              <DataTable
                caption="Стоимость по шагам и провайдерам"
                columns={costColumns}
                rows={data.costs.items}
                rowKey={(row) => `${row.step_name}:${row.provider}`}
                emptyTitle="Платных шагов не было"
                emptyDescription="У ролика нет записей generation_events — провайдеры в мок-режиме или всё бесплатно."
                minWidth={380}
              />
            </SectionCard>
          </div>

          <SectionCard title={`Снимки метрик · ${formatNumber(data.snapshots.length)}`} bodyClassName="p-0">
            <DataTable
              caption="Все снимки метрик ролика"
              columns={snapshotColumns}
              rows={data.snapshots}
              rowKey={(row) => String(row.id)}
              emptyTitle="Снимков пока нет"
              emptyDescription="Первый снимок коллектор снимает через час после публикации."
              minWidth={640}
            />
          </SectionCard>
        </>
      )}
    </div>
  );
}
