"use client";

import { ExternalLink } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { parseAsStringLiteral, useQueryState } from "nuqs";

import { PageHeader } from "@/components/common/page-header";
import { SectionError } from "@/components/common/section-error";
import { AUTOPUBLISH_STATE_META, PRIVACY_LABELS } from "@/components/packages/labels";
import { DataTable, type DataTableColumn, PillToggle, Select, StatusBadge } from "@/components/ui";
import { formatDateTime, formatNumber } from "@/lib/format";
import { routeHref } from "@/lib/nav";
import { useChannels } from "@/lib/queries/channels";
import { type AutopublishQueueItem, type AutopublishState, useAutopublishQueue } from "@/lib/queries/packages";

const FILTERS = ["all", "active", "failed", "done", "cancelled"] as const;
type Filter = (typeof FILTERS)[number];
const FILTER_STATES: Record<Filter, AutopublishState[] | null> = {
  all: null,
  active: ["scheduled", "uploading"],
  failed: ["failed"],
  done: ["done"],
  cancelled: ["cancelled"],
};

/**
 * Запланировано (`/autopublish?state=&channel=`): очередь автозалива через YouTube API — что заливается,
 * где ошибка, что ждёт слота и что уже залито. Строка ведёт в карточку пакета (там же «снять с очереди»).
 */
export function AutopublishScreen() {
  const router = useRouter();
  const [filter, setFilter] = useQueryState(
    "state",
    parseAsStringLiteral(FILTERS).withDefault("all").withOptions({ history: "replace" }),
  );
  const [channelId, setChannelId] = useQueryState("channel", { history: "replace" });
  const queue = useAutopublishQueue({ channelId, states: FILTER_STATES[filter] });
  const channels = useChannels({ platform: "youtube" });
  const items = queue.data?.items ?? [];

  const columns: Array<DataTableColumn<AutopublishQueueItem>> = [
    {
      key: "channel",
      header: "Канал",
      render: (item) => (
        <span className="flex flex-col">
          <span className="truncate">{item.channel_title || item.channel_id}</span>
          <span className="font-mono text-2xs text-fg-muted">{item.channel_id}</span>
        </span>
      ),
    },
    {
      key: "title",
      header: "Заголовок",
      className: "max-w-80",
      render: (item) => (
        <span className="flex min-w-0 flex-col">
          <span className="truncate font-medium" title={item.title}>
            {item.title}
          </span>
          <span className="font-mono text-2xs text-fg-muted">
            {item.video_uid} · код {item.tracking_code}
          </span>
        </span>
      ),
    },
    {
      key: "state",
      header: "Состояние",
      render: (item) => <StatusBadge status={item.state} map={AUTOPUBLISH_STATE_META} />,
    },
    {
      key: "at",
      header: "Время",
      render: (item) => (
        <span className="flex flex-col">
          <span className="tabular font-mono text-xs">{formatDateTime(item.at, { short: true })}</span>
          {item.privacy ? <span className="text-2xs text-fg-muted">{PRIVACY_LABELS[item.privacy]}</span> : null}
        </span>
      ),
    },
    {
      key: "attempts",
      header: "Попытки",
      className: "text-right",
      render: (item) => <span className="tabular font-mono text-xs">{item.attempts || "—"}</span>,
    },
    {
      key: "error",
      header: "Ошибка",
      className: "max-w-72",
      render: (item) =>
        item.error ? (
          <span className="line-clamp-2 text-xs text-negative" title={item.error}>
            {item.error}
          </span>
        ) : (
          <span className="text-fg-muted">—</span>
        ),
    },
    {
      key: "video",
      header: "Ролик",
      render: (item) =>
        item.video_url ? (
          <a
            href={item.video_url}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(event) => event.stopPropagation()}
            className="inline-flex items-center gap-1 text-xs text-accent hover:underline"
            data-testid="queue-video-link"
          >
            {item.video_id ?? "открыть"}
            <ExternalLink className="h-3 w-3" aria-hidden="true" />
          </a>
        ) : (
          <span className="text-fg-muted">—</span>
        ),
    },
  ];

  return (
    <div className="flex flex-col gap-4 p-4 md:p-6" data-testid="autopublish-screen">
      <PageHeader
        eyebrow="Производство"
        title="Запланировано"
        description={
          <>
            Очередь автозалива через YouTube API: ролики в слотах плана канала, заливки, ошибки и залитые. Поставить или
            снять пакет — в его карточке в{" "}
            <Link href={routeHref("/packages")} className="text-accent hover:underline">
              «Пакетах»
            </Link>
            .
          </>
        }
      />
      <div className="flex flex-wrap items-center gap-2">
        <PillToggle
          label="Состояние"
          options={[
            { value: "all", label: "Все" },
            { value: "active", label: "В очереди" },
            { value: "failed", label: "Ошибки" },
            { value: "done", label: "Залитые" },
            { value: "cancelled", label: "Снятые" },
          ]}
          value={filter}
          onChange={(value) => void setFilter(value)}
        />
        <Select
          aria-label="Канал"
          className="h-7 w-56 text-xs"
          value={channelId ?? ""}
          onChange={(event) => void setChannelId(event.target.value || null)}
        >
          <option value="">Все каналы YouTube</option>
          {(channels.data?.items ?? []).map((channel) => (
            <option key={channel.id} value={channel.id}>
              {channel.title || channel.id}
            </option>
          ))}
        </Select>
        <span className="ml-auto text-xs text-fg-muted">
          {queue.data ? `${formatNumber(items.length)} в списке · ${formatDateTime(queue.data.generated_at)}` : null}
        </span>
      </div>
      {queue.isError ? (
        <SectionError error={queue.error} onRetry={() => void queue.refetch()} />
      ) : (
        <DataTable
          caption="Очередь автозалива"
          columns={columns}
          rows={items}
          rowKey={(item) => String(item.package_id)}
          onRowClick={(item) => router.push(routeHref("/packages", { package: String(item.package_id) }))}
          isLoading={queue.isPending}
          emptyTitle="Очередь пуста"
          emptyDescription="Поставьте пакет канала YouTube в автозалив: карточка пакета → «В ближайший слот»."
          minWidth={960}
        />
      )}
    </div>
  );
}
