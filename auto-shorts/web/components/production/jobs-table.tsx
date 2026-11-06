"use client";

import { Clapperboard, Scissors } from "lucide-react";
import type { KeyboardEvent } from "react";

import { formatTimecode } from "@/components/review/review-logic";
import {
  EmptyState,
  SkeletonTable,
  SortableTh,
  StatusBadge,
  Table,
  TableBody,
  TableHead,
  Td,
  Th,
} from "@/components/ui";
import { cn } from "@/lib/cn";
import { formatAgo, formatDateTime, formatUsd } from "@/lib/format";
import type { JobRow } from "@/lib/queries/jobs";

import { JOB_KIND_LABELS, JOB_STATUS_META, jobStageLabel } from "./job-meta";

export type JobsSortKey = "created_at" | "priority" | "duration_sec" | "cost_usd";

function StageCell({ row }: { row: JobRow }) {
  if (row.status === "failed" && row.error) {
    return (
      <span className="block max-w-56 truncate text-xs text-negative" title={row.error}>
        {jobStageLabel(row.stage)}: {row.error}
      </span>
    );
  }
  const running = row.status === "processing" || row.status === "queued";
  const pct = row.progress === null || row.progress === undefined ? null : Math.max(0, Math.min(100, row.progress));
  return (
    <span className="flex items-center gap-2">
      <span className={cn("text-xs", running ? "text-machine" : "text-fg-muted")}>{jobStageLabel(row.stage)}</span>
      {running && pct !== null ? (
        <span className="flex items-center gap-1" title={`${Math.round(pct)}%`}>
          <span className="h-1 w-14 overflow-hidden rounded-full bg-surface-2" aria-hidden="true">
            <span className="block h-full bg-machine" style={{ width: `${pct}%` }} />
          </span>
          <span className="font-mono text-2xs text-fg-muted tabular">{Math.round(pct)}%</span>
        </span>
      ) : null}
    </span>
  );
}

/** Плотная таблица роликов (§7.3: строки 28–32 px). Строка открывает панель ролика (`?job=`). */
export function JobsTable({
  rows,
  isLoading,
  selectedId,
  onSelect,
  sort,
  onSort,
  now,
  emptyTitle,
  emptyDescription,
}: {
  rows: readonly JobRow[];
  isLoading: boolean;
  selectedId: number | null;
  onSelect: (id: number) => void;
  sort: string;
  onSort: (key: JobsSortKey) => void;
  now: number;
  emptyTitle: string;
  emptyDescription?: string;
}) {
  if (isLoading) return <SkeletonTable rows={8} />;
  if (rows.length === 0) return <EmptyState compact title={emptyTitle} description={emptyDescription} />;

  const sortKey = sort.replace(/^-/, "");
  const direction: "asc" | "desc" = sort.startsWith("-") ? "desc" : "asc";
  const sortable = (key: JobsSortKey, label: string, className?: string) => (
    <SortableTh
      className={className}
      active={sortKey === key}
      direction={sortKey === key ? direction : undefined}
      onSort={() => onSort(key)}
    >
      {label}
    </SortableTh>
  );

  const onRowKey = (event: KeyboardEvent<HTMLTableRowElement>, id: number) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect(id);
    }
  };

  return (
    <Table minWidth={860} data-testid="jobs-table">
      <caption className="sr-only">Ролики</caption>
      <TableHead>
        <tr>
          <Th>Ролик</Th>
          <Th>Статус</Th>
          <Th>Стадия</Th>
          <Th>Канал</Th>
          <Th>Тема</Th>
          {sortable("duration_sec", "Длит.", "text-right")}
          {sortable("cost_usd", "Себест.", "text-right")}
          {sortable("priority", "Приор.", "text-right")}
          {sortable("created_at", "Создан")}
        </tr>
      </TableHead>
      <TableBody>
        {rows.map((row) => {
          const selected = row.id === selectedId;
          const KindIcon = row.kind === "clip" ? Scissors : Clapperboard;
          return (
            <tr
              key={row.id}
              tabIndex={0}
              aria-selected={selected}
              data-job-id={row.id}
              onClick={() => onSelect(row.id)}
              onKeyDown={(event) => onRowKey(event, row.id)}
              className={cn(
                "h-8 cursor-pointer transition-colors duration-150",
                selected ? "bg-accent/10" : "hover:bg-surface-2",
              )}
            >
              <Td>
                <span className="flex items-center gap-1.5">
                  <KindIcon className="h-3.5 w-3.5 text-fg-muted" aria-label={JOB_KIND_LABELS[row.kind]} />
                  <span className="font-mono text-xs">{row.video_uid}</span>
                </span>
              </Td>
              <Td>
                <span className="flex items-center gap-1">
                  <StatusBadge status={row.status} map={JOB_STATUS_META} />
                  {row.kind === "clip" && row.originality_verdict === "fail" ? (
                    <span
                      className="rounded-sm bg-negative/15 px-1 text-2xs text-negative"
                      title="Отчёт об оригинальности: не заливать, пока не исправлено"
                    >
                      ориг.
                    </span>
                  ) : null}
                </span>
              </Td>
              <Td>
                <StageCell row={row} />
              </Td>
              <Td className="max-w-40 truncate text-xs text-fg-muted">{row.channel_id}</Td>
              <Td className="max-w-64 truncate" title={row.topic ?? undefined}>
                {row.topic ?? <span className="text-fg-muted">—</span>}
              </Td>
              <Td className="text-right font-mono text-xs tabular">{formatTimecode(row.duration_sec)}</Td>
              <Td className="text-right font-mono text-xs tabular">{formatUsd(row.cost_usd, 4)}</Td>
              <Td className="text-right font-mono text-xs tabular">{row.priority}</Td>
              <Td className="text-xs whitespace-nowrap text-fg-muted" title={formatDateTime(row.created_at)}>
                {formatAgo(row.created_at, now)}
              </Td>
            </tr>
          );
        })}
      </TableBody>
    </Table>
  );
}
