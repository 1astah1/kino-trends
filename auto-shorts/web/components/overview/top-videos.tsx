"use client";

import { Film } from "lucide-react";
import { useRouter } from "next/navigation";

import { Badge, DataTable, type DataTableColumn } from "@/components/ui";
import { mediaSrc } from "@/lib/api/media";
import { formatAgo, formatDateTime, formatNumber, formatUsd } from "@/lib/format";
import { routeHref } from "@/lib/nav";
import type { BreakdownRow } from "@/lib/queries/overview";

const PLATFORM_LABELS: Record<string, string> = { youtube: "YouTube", vk: "VK" };

function Poster({ url }: { url: string | null }) {
  const src = mediaSrc(url);
  return (
    <span className="flex h-12 w-7 shrink-0 items-center justify-center overflow-hidden rounded-sm bg-video">
      {src ? (
        // eslint-disable-next-line @next/next/no-img-element -- статический экспорт, картинки без оптимизатора
        <img src={src} alt="" loading="lazy" className="h-full w-full object-cover" />
      ) : (
        <Film className="h-3.5 w-3.5 text-fg-muted" aria-hidden="true" />
      )}
    </span>
  );
}

/** Топ-10 роликов периода по просмотрам; строка открывает карточку ролика в Аналитике. */
export function TopVideos({ rows, now }: { rows: BreakdownRow[]; now: number }) {
  const router = useRouter();
  const columns: Array<DataTableColumn<BreakdownRow>> = [
    {
      key: "video",
      header: "Ролик",
      className: "max-w-96",
      render: (row) => {
        const video = row.video;
        const title = video?.title || row.label || video?.video_uid || row.key;
        const subtitle = video
          ? [video.channel.title || video.channel.id, PLATFORM_LABELS[video.platform] ?? video.platform, video.video_uid]
              .filter(Boolean)
              .join(" · ")
          : row.key;
        return (
          <span className="flex min-w-0 items-center gap-2">
            <Poster url={video?.poster_url ?? null} />
            <span className="flex min-w-0 flex-col">
              <span className="truncate font-medium" title={title}>
                {title}
              </span>
              <span className="truncate font-mono text-2xs text-fg-muted">{subtitle}</span>
            </span>
          </span>
        );
      },
    },
    {
      key: "published",
      header: "Опубликован",
      render: (row) => (
        <span className="flex flex-col">
          <span className="tabular font-mono text-xs">
            {row.video?.published_at ? formatDateTime(row.video.published_at, { short: true }) : "—"}
          </span>
          <span className="flex gap-1 text-2xs text-fg-muted">
            {row.video?.published_at ? formatAgo(row.video.published_at, now) : null}
            {row.video?.stale ? <Badge variant="warning">метрики устарели</Badge> : null}
            {row.video?.manual_due ? <Badge variant="human">ввод вручную</Badge> : null}
          </span>
        </span>
      ),
    },
    {
      key: "views",
      header: "Просмотры",
      className: "text-right",
      render: (row) => (
        <span className="flex flex-col items-end">
          <span className="tabular font-mono text-xs font-semibold">{formatNumber(row.metrics.views)}</span>
          {row.video?.views_72h !== null && row.video?.views_72h !== undefined ? (
            <span className="tabular font-mono text-2xs text-fg-muted">72 ч: {formatNumber(row.video.views_72h)}</span>
          ) : null}
        </span>
      ),
    },
    {
      key: "visitors",
      header: "Посетители",
      className: "text-right",
      render: (row) => <span className="tabular font-mono text-xs">{formatNumber(row.metrics.visitors)}</span>,
    },
    {
      key: "bot_starts",
      header: "Старты",
      className: "text-right",
      render: (row) => <span className="tabular font-mono text-xs">{formatNumber(row.metrics.bot_starts)}</span>,
    },
    {
      key: "first_payments",
      header: "Оплаты",
      className: "text-right",
      render: (row) => <span className="tabular font-mono text-xs">{formatNumber(row.metrics.first_payments)}</span>,
    },
    {
      key: "spend",
      header: "Расход",
      className: "text-right",
      render: (row) => <span className="tabular font-mono text-xs">{formatUsd(row.metrics.spend_usd, 2)}</span>,
    },
  ];

  return (
    <DataTable
      caption="Топ роликов за период"
      columns={columns}
      rows={rows}
      rowKey={(row) => row.key}
      onRowClick={(row) =>
        row.video ? router.push(routeHref("/analytics/video", { id: String(row.video.publication_id) })) : undefined
      }
      emptyTitle="Пока нет опубликованных роликов за период"
      emptyDescription="Топ появится после первых публикаций, когда соберутся снимки просмотров."
      minWidth={880}
    />
  );
}
