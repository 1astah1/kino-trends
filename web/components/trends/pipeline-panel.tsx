"use client";

import { Ban, ExternalLink, RotateCcw, Workflow } from "lucide-react";
import { toast } from "sonner";

import { SectionError } from "@/components/common/section-error";
import { Badge, Button, DataTable, type DataTableColumn, EmptyState, StatusBadge } from "@/components/ui";
import { errorMessage } from "@/lib/api/errors";
import { formatAgo, formatNumber } from "@/lib/format";
import { useNow } from "@/lib/hooks/use-now";
import {
  type TrendsPipelineBridge,
  type TrendsPipelineItem,
  useCancelTrendItem,
  useRetryTrendItem,
  useTrendsPipeline,
} from "@/lib/queries/trends";

import {
  canCancel,
  canRetry,
  formatDuration,
  isPipelineState,
  PIPELINE_STATE_META,
  type PipelineState,
} from "./labels";

/**
 * Вкладка «Конвейер»: очередь Kino-trends → auto-shorts (источник → нарезка → авторский слой →
 * ревью → автозалив). Kino-trends ставит задания и следит за ними, auto-shorts делает работу;
 * здесь — состояния, ошибки и «Повторить»/«Отменить» для оператора. Ревью и редактор клипов —
 * в «Производстве» и «Приёмке».
 */
export function PipelinePanel({ operator, enabled }: { operator: boolean; enabled: boolean }) {
  const pipeline = useTrendsPipeline({}, { enabled });
  const retry = useRetryTrendItem();
  const cancel = useCancelTrendItem();
  const now = useNow(15_000);

  async function onRetry(item: TrendsPipelineItem) {
    try {
      const updated = await retry.mutateAsync(item.id);
      toast.success("Задание снова в очереди", { description: updated.status_ru ?? updated.status });
    } catch (error) {
      toast.error("Не удалось повторить", { description: errorMessage(error) });
    }
  }

  async function onCancel(item: TrendsPipelineItem) {
    try {
      await cancel.mutateAsync(item.id);
      toast.success("Задание отменено", { description: item.label ?? item.ref_id });
    } catch (error) {
      toast.error("Не удалось отменить", { description: errorMessage(error) });
    }
  }

  if (pipeline.isError) {
    return (
      <SectionError error={pipeline.error} onRetry={() => void pipeline.refetch()} title="Очередь не загрузилась" />
    );
  }

  const data = pipeline.data;
  const statesRu = data?.states_ru ?? {};
  const counts = data?.counts ?? {};
  const busy = retry.isPending || cancel.isPending;

  const columns: Array<DataTableColumn<TrendsPipelineItem>> = [
    {
      key: "kind",
      header: "Тип",
      render: (item) => <Badge variant="neutral">{item.kind === "film" ? "кино" : "видео"}</Badge>,
    },
    {
      key: "label",
      header: "Что",
      className: "min-w-64 max-w-96",
      render: (item) => (
        <span className="flex min-w-0 flex-col">
          <span className="truncate font-medium text-fg" title={item.label ?? item.ref_id}>
            {item.label ?? item.ref_id}
          </span>
          <span className="truncate text-2xs text-fg-muted">
            {[
              item.channel,
              item.is_short === 1 ? "шортс, без нарезки" : item.duration_s ? formatDuration(item.duration_s) : null,
              item.license === "creativeCommon" ? "CC" : null,
              item.rights_type ? `права: ${item.rights_type}` : null,
              item.note,
            ]
              .filter(Boolean)
              .join(" · ")}
          </span>
        </span>
      ),
    },
    {
      key: "status",
      header: "Статус",
      className: "min-w-48",
      render: (item) => (
        <span className="flex flex-col gap-1">
          <StatusBadge
            status={item.status as PipelineState}
            map={PIPELINE_STATE_META}
            fallbackLabel={item.status_ru ?? statesRu[item.status] ?? item.status}
          />
          {item.last_error ? (
            <span className="line-clamp-2 max-w-80 text-xs text-negative" title={item.last_error}>
              {item.last_error}
            </span>
          ) : null}
          {item.status === "needs_rights" ? (
            <span className="text-2xs text-fg-muted">тип прав задаётся в Kino-trends</span>
          ) : null}
        </span>
      ),
    },
    {
      key: "autoshorts",
      header: "В auto-shorts",
      render: (item) => {
        const jobs = item.as_jobs ?? [];
        const packages = item.as_packages ?? [];
        const publications = item.as_publications ?? [];
        const parts = [
          item.as_source_id ? `источник ${item.as_source_id}` : null,
          jobs.length ? `клипов ${jobs.length}` : null,
          packages.length ? `пакетов ${packages.length}` : null,
          publications.length ? `публикаций ${publications.length}` : null,
        ].filter(Boolean);
        return <span className="text-xs text-fg-muted">{parts.length ? parts.join(" · ") : "—"}</span>;
      },
    },
    {
      key: "updated",
      header: "Обновлено",
      className: "text-right",
      render: (item) => (
        <span className="tabular font-mono text-xs text-fg-muted" title={item.updated_at ?? undefined}>
          {formatAgo(item.updated_at, now)}
        </span>
      ),
    },
    {
      key: "actions",
      header: "",
      className: "text-right",
      render: (item) => (
        <span className="inline-flex items-center justify-end gap-1.5">
          {operator && canRetry(item.status) ? (
            <Button
              variant="outline"
              size="xs"
              disabled={busy}
              onClick={() => void onRetry(item)}
              data-testid="trends-retry"
            >
              <RotateCcw className="h-3 w-3" aria-hidden="true" />
              Повторить
            </Button>
          ) : null}
          {operator && canCancel(item.status) ? (
            <Button
              variant="ghost"
              size="xs"
              disabled={busy}
              onClick={() => void onCancel(item)}
              data-testid="trends-cancel"
            >
              <Ban className="h-3 w-3" aria-hidden="true" />
              Отменить
            </Button>
          ) : null}
          {item.source_url ? (
            <a
              href={item.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex h-6 w-6 items-center justify-center rounded-md text-fg-muted hover:bg-surface-2 hover:text-fg"
              title="Исходник на площадке"
              aria-label="Исходник на площадке"
            >
              <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
            </a>
          ) : null}
        </span>
      ),
    },
  ];

  const countChips = Object.entries(counts)
    .filter(([, n]) => n > 0)
    .sort(([a], [b]) => stateOrder(a) - stateOrder(b));

  return (
    <div className="flex flex-col gap-3" data-testid="trends-pipeline">
      {data ? <BridgeLine status={data.status} /> : null}
      {countChips.length ? (
        <div className="flex flex-wrap gap-1.5">
          {countChips.map(([state, n]) => {
            const meta = isPipelineState(state) ? PIPELINE_STATE_META[state] : null;
            return (
              <Badge key={state} variant={meta?.variant ?? "neutral"} outline={meta?.outline}>
                {meta?.label ?? statesRu[state] ?? state}: {formatNumber(n)}
              </Badge>
            );
          })}
        </div>
      ) : null}
      {data && data.items.length === 0 ? (
        <EmptyState
          icon={Workflow}
          title="Очередь пуста"
          description="Нажмите «В конвейер» на карточке во вкладке «Сегодня» или «взять сегодняшнее» в самом Kino-trends."
          compact
        />
      ) : (
        <DataTable
          columns={columns}
          rows={data?.items ?? []}
          rowKey={(item) => String(item.id)}
          isLoading={pipeline.isPending}
          emptyTitle="Очередь пуста"
          minWidth={960}
          caption="Очередь конвейера Kino-trends → auto-shorts"
        />
      )}
    </div>
  );
}

const STATE_ORDER = [
  "new",
  "file_pending",
  "needs_rights",
  "sent",
  "cutting",
  "review",
  "approved",
  "publishing",
  "published",
  "qc_failed",
  "failed",
  "cancelled",
];

function stateOrder(state: string): number {
  const index = STATE_ORDER.indexOf(state);
  return index === -1 ? STATE_ORDER.length : index;
}

/** Как Kino-trends видит auto-shorts: связь, каналы, включён ли цикл, автоодобрение и автозалив. */
function BridgeLine({ status }: { status: TrendsPipelineBridge }) {
  const health = status.health as Record<string, unknown> | null;
  const healthStatus = typeof health?.status === "string" ? health.status : null;
  const linked = status.configured && healthStatus === "ok";
  const channels = (status.channels ?? [])
    .map((channel) => {
      const c = channel as Record<string, unknown>;
      return typeof c.title === "string" ? c.title : typeof c.id === "string" ? c.id : null;
    })
    .filter(Boolean);
  return (
    <p className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-fg-muted">
      <span>
        auto-shorts из Kino-trends:{" "}
        <b className={linked ? "font-medium text-positive" : "font-medium text-warning"}>
          {!status.configured ? "не настроен" : linked ? "готов" : healthStatus || "нет ответа"}
        </b>
      </span>
      {channels.length ? (
        <span>
          каналы: <b className="font-medium text-fg">{channels.join(", ")}</b>
        </span>
      ) : null}
      <span>
        цикл: <b className="font-medium text-fg">{status.enabled ? "включён" : "выключен"}</b>
      </span>
      <span>
        автоодобрение: <b className="font-medium text-fg">{status.auto_review ? "да" : "нет"}</b>
      </span>
      <span>
        автозалив: <b className="font-medium text-fg">{status.autopublish ? "да" : "нет"}</b>
      </span>
      <span>
        права по умолчанию: <b className="font-medium text-fg">{status.rights_default || "только CC и свободные"}</b>
      </span>
    </p>
  );
}
