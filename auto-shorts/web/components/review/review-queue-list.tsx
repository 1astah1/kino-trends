"use client";

import { Inbox, ShieldAlert } from "lucide-react";
import { useEffect, useRef } from "react";

import { SEVERITY_META, severityRank } from "@/components/production/job-meta";
import { EmptyState, Skeleton } from "@/components/ui";
import { mediaSrc } from "@/lib/api/media";
import { cn } from "@/lib/cn";
import { formatAgo } from "@/lib/format";
import type { ReviewQueueItem } from "@/lib/queries/review";

import { formatTimecode } from "./review-logic";

function FlagDots({ item }: { item: ReviewQueueItem }) {
  if (item.flags.length === 0) return null;
  const worst = item.flags.reduce(
    (acc, flag) => (severityRank(flag.severity) > severityRank(acc) ? flag.severity : acc),
    item.flags[0].severity,
  );
  const meta = SEVERITY_META[worst];
  const Icon = meta.icon;
  return (
    <span
      className={cn("inline-flex items-center gap-0.5 font-mono text-2xs", meta.text)}
      title={item.flags.map((flag) => flag.code).join(", ")}
    >
      <Icon className="h-3 w-3" aria-hidden="true" />
      {item.flags.length}
      <span className="sr-only">флагов: {meta.label}</span>
    </span>
  );
}

export function ReviewQueueList({
  items,
  currentId,
  onSelect,
  isLoading,
  now,
  className,
}: {
  items: readonly ReviewQueueItem[];
  currentId: number | null;
  onSelect: (jobId: number) => void;
  isLoading: boolean;
  now: number;
  className?: string;
}) {
  const listRef = useRef<HTMLUListElement>(null);

  // Выбранный ролик всегда виден в списке (J/K, автопереход).
  useEffect(() => {
    if (currentId === null) return;
    const node = listRef.current?.querySelector<HTMLElement>(`[data-job-id="${currentId}"]`);
    node?.scrollIntoView({ block: "nearest" });
  }, [currentId]);

  if (isLoading) {
    return (
      <div className={cn("flex flex-col gap-2 p-2", className)} aria-busy="true" aria-label="Загрузка очереди">
        {Array.from({ length: 5 }).map((_, index) => (
          <Skeleton key={index} className="h-16 w-full" />
        ))}
      </div>
    );
  }
  if (items.length === 0) {
    return (
      <div className={cn("p-2", className)}>
        <EmptyState
          icon={Inbox}
          compact
          title="Очередь пуста"
          description="Новые ролики появятся здесь сами, когда пройдут QC."
        />
      </div>
    );
  }

  return (
    <ul
      ref={listRef}
      className={cn("flex flex-col gap-1 p-2", className)}
      aria-label="Очередь приёмки"
      data-testid="review-queue"
    >
      {items.map((item, index) => {
        const active = item.job_id === currentId;
        const poster = mediaSrc(item.poster_url);
        return (
          <li key={item.job_id}>
            <button
              type="button"
              data-job-id={item.job_id}
              data-uid={item.video_uid}
              data-status={item.status}
              data-testid="review-queue-item"
              aria-current={active ? "true" : undefined}
              onClick={() => onSelect(item.job_id)}
              className={cn(
                "flex w-full items-center gap-2 rounded-md border px-2 py-1.5 text-left transition-colors duration-150",
                active ? "border-human/60 bg-human/10" : "border-transparent hover:bg-surface-2",
              )}
            >
              <span className="relative block h-14 w-8 shrink-0 overflow-hidden bg-video" aria-hidden="true">
                {poster ? (
                  // eslint-disable-next-line @next/next/no-img-element -- статический экспорт, картинки без оптимизатора
                  <img src={poster} alt="" loading="lazy" className="h-full w-full object-cover" />
                ) : null}
              </span>
              <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className="flex items-center gap-1.5">
                  <span className="font-mono text-2xs text-fg-muted tabular">{index + 1}</span>
                  <span className="truncate font-mono text-xs text-fg">{item.video_uid}</span>
                </span>
                <span className="truncate text-xs text-fg-muted">{item.channel_id}</span>
                <span className="flex items-center gap-2 text-2xs text-fg-muted">
                  <span className="font-mono tabular">{formatTimecode(item.duration_sec)}</span>
                  {item.attempt_count > 1 ? <span>попытка {item.attempt_count}</span> : null}
                  {item.ready_at ? <span>{formatAgo(item.ready_at, now)}</span> : null}
                </span>
              </span>
              <span className="flex shrink-0 flex-col items-end gap-1">
                {item.status === "qc_failed" ? (
                  <span className="inline-flex items-center gap-0.5 text-2xs text-negative" title="Не прошёл QC">
                    <ShieldAlert className="h-3 w-3" aria-hidden="true" />
                    QC
                  </span>
                ) : null}
                <FlagDots item={item} />
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
