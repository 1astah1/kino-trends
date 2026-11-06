"use client";

import { History, Unplug } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { SectionError } from "@/components/common/section-error";
import {
  Badge,
  DataTable,
  type DataTableColumn,
  Dialog,
  EmptyState,
  SectionCard,
  Skeleton,
  StatusBadge,
} from "@/components/ui";
import { cn } from "@/lib/cn";
import { formatDateTime, formatNumber, formatTime } from "@/lib/format";
import { type TrendsRun, type TrendsRunLogLine, useTrendsRunLog, useTrendsRuns } from "@/lib/queries/trends-ops";

import {
  formatElapsed,
  formatSeconds,
  parseProgress,
  RUN_STATUS_META,
  type RunStatus,
  runTriggerLabel,
} from "./labels";
import { ProgressBar } from "./task-bits";

/** Что показать из `stats` прогона одной строкой: ключ → подпись (порядок важен). */
const STAT_LABELS: ReadonlyArray<[string, string]> = [
  ["new_videos", "новых роликов"],
  ["details_ok", "деталей"],
  ["recognized_new", "опознано"],
  ["pop_new", "популярных новых"],
  ["pop_clean", "чистых"],
  ["pop_picked", "в подборку"],
  ["rights_block", "права: блок"],
  ["files_prepared", "файлов"],
  ["api_quota_used", "квота API"],
];

function statsSummary(stats: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const [key, label] of STAT_LABELS) {
    const value = stats[key];
    if (typeof value === "number" && value > 0) parts.push(`${label} ${formatNumber(value)}`);
  }
  const cost = stats.neuro_cost_usd;
  if (typeof cost === "number" && cost > 0) parts.push(`нейро $${cost.toFixed(3)}`);
  if (stats.aborted === true) parts.push("прерван");
  return parts.join(" · ");
}

function LevelMark({ level }: { level: string | null | undefined }) {
  const tone =
    level === "error" ? "text-negative" : level === "warn" || level === "warning" ? "text-warning" : "text-fg-muted";
  return <span className={cn("inline-block w-10 shrink-0 uppercase", tone)}>{(level ?? "info").slice(0, 5)}</span>;
}

/** Строки лога прогона: время · уровень · сообщение; при `follow` держится внизу. */
function RunLogView({ lines, follow, loading }: { lines: TrendsRunLogLine[]; follow?: boolean; loading?: boolean }) {
  const boxRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!follow) return;
    const box = boxRef.current;
    if (box) box.scrollTop = box.scrollHeight;
  }, [lines, follow]);
  return (
    <div
      ref={boxRef}
      className="max-h-96 min-h-32 overflow-auto rounded-md border border-border bg-video p-2 font-mono text-2xs leading-4 text-white/85"
      tabIndex={0}
      aria-label="Лог прогона"
    >
      {loading
        ? "Загрузка лога…"
        : lines.length === 0
          ? "Лог пуст."
          : lines.map((line, index) => (
              <div key={`${line.ts ?? ""}-${index}`} className="flex gap-2 whitespace-pre-wrap break-all">
                <span className="tabular shrink-0 opacity-60">
                  {line.ts ? formatTime(line.ts).replace(/ МСК$/, "") : "—"}
                </span>
                <LevelMark level={line.level} />
                <span>{line.msg}</span>
              </div>
            ))}
    </div>
  );
}

/**
 * Вкладка «Прогоны»: история сборов трендов Kino-trends (кто запустил, сколько шло, что нашлось, где
 * ошибка), текущий прогон с живым хвостом лога из `current.log`; клик по строке — лог прогона в диалоге.
 */
export function HistoryPanel({ trendsConfigured, now }: { trendsConfigured: boolean; now: number }) {
  const runs = useTrendsRuns({}, { enabled: trendsConfigured });
  const [openId, setOpenId] = useState<number | null>(null);
  const current = runs.data?.current;
  const openRun = runs.data?.items.find((run) => run.id === openId) ?? null;
  const openIsLive = !!openRun && !!current?.running && current.run_id === openRun.id;
  const log = useTrendsRunLog(openRun && !openIsLive ? openRun.id : null, 500);

  if (!trendsConfigured) {
    return (
      <EmptyState
        icon={Unplug}
        title="Мост к Kino-trends не настроен"
        description="История прогонов сбора трендов появится после настройки адреса Kino-trends в «Трендоскауте»."
      />
    );
  }
  if (runs.isError) {
    return <SectionError error={runs.error} onRetry={() => void runs.refetch()} title="Прогоны не загрузились" />;
  }

  const progress = parseProgress(current?.progress);
  const columns: Array<DataTableColumn<TrendsRun>> = [
    { key: "id", header: "#", render: (run) => <span className="font-mono text-xs">{run.id}</span> },
    {
      key: "status",
      header: "Статус",
      render: (run) => (
        <StatusBadge status={run.status as RunStatus} map={RUN_STATUS_META} fallbackLabel={run.status} />
      ),
    },
    {
      key: "trigger",
      header: "Запуск",
      render: (run) => <span className="text-xs text-fg-muted">{runTriggerLabel(run.trigger)}</span>,
    },
    {
      key: "started",
      header: "Начало",
      render: (run) => (
        <span className="tabular font-mono text-xs" title={formatDateTime(run.started_at)}>
          {formatDateTime(run.started_at, { short: true })}
        </span>
      ),
    },
    {
      key: "duration",
      header: "Длительность",
      className: "text-right",
      render: (run) => (
        <span className="tabular font-mono text-xs">
          {run.status === "running"
            ? formatElapsed(run.started_at, now)
            : typeof run.stats?.duration_s === "number"
              ? formatSeconds(run.stats.duration_s)
              : formatElapsed(run.started_at, now, run.finished_at)}
        </span>
      ),
    },
    {
      key: "stats",
      header: "Итоги",
      className: "min-w-72 max-w-xl",
      render: (run) => {
        const summary = statsSummary(run.stats ?? {});
        return (
          <span className="flex min-w-0 flex-col">
            <span className="truncate text-xs" title={summary}>
              {summary || "—"}
            </span>
            {run.error ? (
              <span className="line-clamp-2 text-xs text-negative" title={run.error}>
                {run.error}
              </span>
            ) : null}
          </span>
        );
      },
    },
  ];

  return (
    <div className="flex flex-col gap-4" data-testid="queue-history">
      {current?.running ? (
        <SectionCard
          title={`Идёт сейчас · прогон #${current.run_id ?? "—"}`}
          actions={<Badge variant="machine">{runTriggerLabel(current.trigger)}</Badge>}
        >
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
            <span className="font-medium">{current.phase || "запуск"}</span>
            {progress.text ? <span className="tabular font-mono text-xs text-fg-muted">{progress.text}</span> : null}
            <span className="tabular font-mono text-xs text-fg-muted">
              идёт {formatElapsed(current.started_at, now)} · с {formatDateTime(current.started_at, { short: true })}
            </span>
          </div>
          <ProgressBar pct={progress.pct} tone="bg-human" />
          <RunLogView lines={current.log ?? []} follow />
        </SectionCard>
      ) : null}

      {runs.isPending ? (
        <Skeleton className="h-48" />
      ) : (runs.data?.items.length ?? 0) === 0 ? (
        <EmptyState
          icon={History}
          title="Прогонов ещё не было"
          description="Запустите сбор на вкладке «Управление» или дождитесь автосбора по расписанию."
        />
      ) : (
        <DataTable
          columns={columns}
          rows={runs.data?.items ?? []}
          rowKey={(run) => String(run.id)}
          onRowClick={(run) => setOpenId(run.id)}
          minWidth={900}
          caption="Прогоны сбора трендов Kino-trends"
        />
      )}

      <Dialog
        open={openRun !== null}
        onClose={() => setOpenId(null)}
        title={openRun ? `Прогон #${openRun.id}` : "Прогон"}
        description={
          openRun
            ? `${runTriggerLabel(openRun.trigger)} · ${formatDateTime(openRun.started_at)} · ${
                RUN_STATUS_META[openRun.status as RunStatus]?.label ?? openRun.status
              }${openIsLive ? " · живой лог" : ""}`
            : undefined
        }
        className="max-w-3xl"
      >
        {openRun ? (
          <div className="flex flex-col gap-2">
            {statsSummary(openRun.stats ?? {}) ? (
              <p className="text-xs text-fg-muted">{statsSummary(openRun.stats ?? {})}</p>
            ) : null}
            {openRun.error ? <p className="text-xs text-negative">{openRun.error}</p> : null}
            {openIsLive ? (
              <RunLogView lines={current?.log ?? []} follow />
            ) : log.isError ? (
              <SectionError error={log.error} onRetry={() => void log.refetch()} title="Лог не загрузился" />
            ) : (
              <RunLogView lines={log.data?.items ?? []} loading={log.isPending} />
            )}
          </div>
        ) : null}
      </Dialog>
    </div>
  );
}
