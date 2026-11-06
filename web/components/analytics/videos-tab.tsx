"use client";

import { Film, Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { parseAsInteger, useQueryState } from "nuqs";
import { useMemo } from "react";

import { SectionError } from "@/components/common/section-error";
import { PUBLICATION_STATUS_META } from "@/components/packages/labels";
import { Badge, DataTable, type DataTableColumn, Input, Pagination, StatusBadge } from "@/components/ui";
import { mediaSrc } from "@/lib/api/media";
import { formatDateTime, formatNumber, formatUsd } from "@/lib/format";
import { routeHref } from "@/lib/nav";
import {
  type AnalyticsFilters,
  analyticsExportHref,
  type BreakdownRow,
  useAnalyticsBreakdown,
} from "@/lib/queries/analytics";

import { formatAgeHours, PLATFORM_LABELS, VERDICT_LABELS } from "./labels";
import { BasisFallbackNote, CsvLink, TabToolbar } from "./shared";

const LIMIT = 50;
const DEFAULT_SORT = "-views";

/** Колонка таблицы → поле сортировки сервера (`sort` в breakdown). */
const SORT_FIELDS: Record<string, string> = {
  title: "label",
  age: "published_at",
  views: "views",
  clicks: "clicks",
  visitors: "visitors",
  spend: "spend_usd",
};

function parseSort(sort: string): { field: string; direction: "asc" | "desc" } {
  return sort.startsWith("-") ? { field: sort.slice(1), direction: "desc" } : { field: sort, direction: "asc" };
}

function Poster({ url, title }: { url: string | null; title: string }) {
  const src = mediaSrc(url);
  return (
    <span className="flex h-12 w-7 shrink-0 items-center justify-center overflow-hidden rounded-sm bg-surface-2">
      {src ? (
        // eslint-disable-next-line @next/next/no-img-element -- постер из API, без оптимизатора (images.unoptimized)
        <img src={src} alt="" title={title} loading="lazy" className="h-full w-full object-cover" />
      ) : (
        <Film className="h-3.5 w-3.5 text-fg-muted" aria-hidden="true" />
      )}
    </span>
  );
}

function Views({ value }: { value: number | null }) {
  return <span className="tabular font-mono text-xs">{value === null ? "—" : formatNumber(value)}</span>;
}

/** Ролики: лидерборд публикаций (`breakdown?dimension=video`) с возрастными срезами, вердиктом и флагами. */
export function VideosTab({ filters }: { filters: AnalyticsFilters }) {
  const router = useRouter();
  const [sort, setSort] = useQueryState("sort", { history: "replace" });
  const [offset, setOffset] = useQueryState(
    "offset",
    parseAsInteger.withDefault(0).withOptions({ history: "replace" }),
  );
  const [q, setQ] = useQueryState("q", { history: "replace" });
  const effectiveSort = sort || DEFAULT_SORT;
  const { field, direction } = parseSort(effectiveSort);

  const breakdown = useAnalyticsBreakdown(filters, { dimension: "video", sort: effectiveSort, limit: LIMIT, offset });
  const rows = breakdown.data?.rows;

  const needle = (q ?? "").trim().toLowerCase();
  const visible = useMemo(() => {
    const list = rows ?? [];
    if (!needle) return list;
    return list.filter((row) => {
      const video = row.video;
      const haystack = [row.label, video?.title, video?.video_uid, video?.channel.title, video?.channel.id]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return haystack.includes(needle);
    });
  }, [rows, needle]);

  const onSort = (key: string) => {
    const target = SORT_FIELDS[key];
    if (!target) return;
    const next =
      target === field ? (direction === "desc" ? target : `-${target}`) : target === "label" ? target : `-${target}`;
    void setSort(next === DEFAULT_SORT ? null : next);
    void setOffset(null);
  };

  const sortColumnKey = Object.keys(SORT_FIELDS).find((key) => SORT_FIELDS[key] === field);

  const columns: Array<DataTableColumn<BreakdownRow>> = [
    {
      key: "title",
      header: "Ролик",
      sortable: true,
      className: "max-w-96",
      render: (row) => {
        const video = row.video;
        const title = video?.title || row.label || video?.video_uid || row.key;
        return (
          <span className="flex min-w-0 items-center gap-2">
            <Poster url={video?.poster_url ?? null} title={title} />
            <span className="flex min-w-0 flex-col">
              <span className="truncate font-medium" title={title}>
                {title}
              </span>
              <span className="truncate font-mono text-2xs text-fg-muted">
                {video?.video_uid}
                {video ? ` · ${PLATFORM_LABELS[video.platform]}` : null}
              </span>
            </span>
          </span>
        );
      },
    },
    {
      key: "channel",
      header: "Канал",
      className: "max-w-48",
      render: (row) => (
        <span className="flex min-w-0 flex-col">
          <span className="truncate">{row.video?.channel.title || row.video?.channel.id || "—"}</span>
          {row.video?.niche_id ? <span className="font-mono text-2xs text-fg-muted">{row.video.niche_id}</span> : null}
        </span>
      ),
    },
    {
      key: "age",
      header: "Возраст",
      sortable: true,
      render: (row) => (
        <span className="flex flex-col">
          <span className="tabular font-mono text-xs">{formatAgeHours(row.video?.age_hours)}</span>
          <span className="text-2xs text-fg-muted">{formatDateTime(row.video?.published_at, { short: true })}</span>
        </span>
      ),
    },
    {
      key: "views_24h",
      header: "24 ч",
      className: "text-right",
      render: (row) => <Views value={row.video?.views_24h ?? null} />,
    },
    {
      key: "views_72h",
      header: "72 ч",
      className: "text-right",
      render: (row) => <Views value={row.video?.views_72h ?? null} />,
    },
    {
      key: "views_7d",
      header: "7 дн",
      className: "text-right",
      render: (row) => <Views value={row.video?.views_7d ?? null} />,
    },
    {
      key: "views",
      header: "Сейчас",
      sortable: true,
      className: "text-right",
      render: (row) => <Views value={row.video?.latest_views ?? row.metrics.views} />,
    },
    {
      key: "clicks",
      header: "Переходы",
      sortable: true,
      className: "text-right",
      render: (row) => <Views value={row.metrics.clicks} />,
    },
    {
      key: "visitors",
      header: "Посетители",
      sortable: true,
      className: "text-right",
      render: (row) => <Views value={row.metrics.visitors} />,
    },
    {
      key: "spend",
      header: "Расход",
      sortable: true,
      className: "text-right",
      render: (row) => <span className="tabular font-mono text-xs">{formatUsd(row.metrics.spend_usd, 3)}</span>,
    },
    {
      key: "flags",
      header: "Состояние",
      render: (row) => {
        const video = row.video;
        if (!video) return <span className="text-fg-muted">—</span>;
        return (
          <span className="flex flex-wrap items-center gap-1">
            {video.status !== "live" ? <StatusBadge status={video.status} map={PUBLICATION_STATUS_META} /> : null}
            {video.verdict ? <Badge variant="accent">{VERDICT_LABELS[video.verdict]}</Badge> : null}
            {video.stale ? (
              <Badge variant="warning" title="Опрос метрик просрочен">
                устарело
              </Badge>
            ) : null}
            {video.manual_due ? (
              <Badge variant="human" title="Ждёт ручного ввода метрик">
                ручной ввод
              </Badge>
            ) : null}
            {!video.verdict && !video.stale && !video.manual_due && video.status === "live" ? (
              <span className="text-2xs text-fg-muted">—</span>
            ) : null}
          </span>
        );
      },
    },
  ];

  return (
    <div className="flex flex-col gap-3" data-testid="analytics-videos">
      <TabToolbar
        right={
          <CsvLink
            href={analyticsExportHref(filters, { report: "breakdown", dimension: "video", sort: effectiveSort })}
          />
        }
      >
        <label className="relative flex items-center">
          <Search className="pointer-events-none absolute left-2 h-3.5 w-3.5 text-fg-muted" aria-hidden="true" />
          <Input
            type="search"
            aria-label="Поиск по названию"
            placeholder="Поиск по названию, каналу, uid"
            className="h-7 w-72 pl-7 text-xs"
            value={q ?? ""}
            onChange={(event) => void setQ(event.target.value || null)}
          />
        </label>
        <span className="text-xs text-fg-muted">
          {breakdown.data
            ? `${formatNumber(breakdown.data.total_rows)} роликов за период${needle ? ` · показано ${visible.length} на этой странице` : ""}`
            : null}
        </span>
      </TabToolbar>

      <BasisFallbackNote show={breakdown.data?.basis_fallback} />

      {breakdown.isError ? (
        <SectionError error={breakdown.error} onRetry={() => void breakdown.refetch()} title="Ролики не загрузились" />
      ) : (
        <>
          <DataTable
            caption="Ролики за период"
            columns={columns}
            rows={visible}
            rowKey={(row) => row.key}
            onRowClick={(row) =>
              router.push(routeHref("/analytics/video", { id: String(row.video?.publication_id ?? row.key) }))
            }
            sortKey={sortColumnKey}
            sortDirection={direction}
            onSort={onSort}
            isLoading={breakdown.isPending}
            emptyTitle={needle ? "Ничего не найдено на этой странице" : "Пока нет опубликованных роликов"}
            emptyDescription={
              needle
                ? "Поиск идёт по названиям на текущей странице; снимите фильтр или перелистните."
                : "Опубликуйте ролик — он появится здесь с первым снимком метрик через час."
            }
            minWidth={1180}
          />
          {breakdown.data && breakdown.data.total_rows > LIMIT ? (
            <Pagination
              total={breakdown.data.total_rows}
              limit={LIMIT}
              offset={offset}
              onOffsetChange={(next) => void setOffset(next || null)}
            />
          ) : null}
        </>
      )}
    </div>
  );
}
