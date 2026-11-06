"use client";

import { Workflow } from "lucide-react";
import Link from "next/link";

import { SectionCard, Skeleton, StatusBadge } from "@/components/ui";
import { formatAgo } from "@/lib/format";
import { useNow } from "@/lib/hooks/use-now";
import { routeHref } from "@/lib/nav";
import { type TrendsPipelineItem, useTrendsPipeline } from "@/lib/queries/trends";

import { EnqueueButton } from "./enqueue-button";
import { canRetry, PIPELINE_STATE_META, type PipelineState } from "./labels";

/** Задания конвейера Kino-trends по этому произведению или ролику (ref_id либо исходник). */
export function usePipelineItemsFor(kind: "film" | "video", refId: string | null | undefined, enabled = true) {
  const pipeline = useTrendsPipeline({ limit: 500 }, { enabled: enabled && !!refId });
  const items = (pipeline.data?.items ?? []).filter(
    (item) => item.ref_id === refId && (item.kind === kind || (kind === "video" && item.source_video_id === refId)),
  );
  return { pipeline, items };
}

/** Активное задание: не завершённое и не сломанное — его повтор «В конвейер» не создаст. */
export function activePipelineItem(items: TrendsPipelineItem[]): TrendsPipelineItem | undefined {
  return items.find((item) => item.status !== "published" && !canRetry(item.status));
}

/**
 * Блок «Конвейер» в разборах: что уже сделано с этим кино или роликом в очереди Kino-trends → auto-shorts,
 * ссылка на вкладку очереди и «В конвейер», если активного задания нет.
 */
export function PipelineRefCard({
  kind,
  refId,
  operator,
  enqueueDisabled,
  enqueueTitle,
  enabled = true,
}: {
  kind: "film" | "video";
  refId: string;
  operator: boolean;
  enqueueDisabled?: boolean;
  enqueueTitle?: string;
  enabled?: boolean;
}) {
  const { pipeline, items } = usePipelineItemsFor(kind, refId, enabled);
  const now = useNow(30_000);
  const active = activePipelineItem(items);

  return (
    <SectionCard
      title="Конвейер"
      actions={
        <Link href={routeHref("/trends", { tab: "pipeline" })} className="text-xs text-accent hover:underline">
          Вся очередь
        </Link>
      }
    >
      {pipeline.isPending && enabled ? (
        <Skeleton className="h-10 w-full" />
      ) : items.length === 0 ? (
        <p className="flex items-center gap-2 text-sm text-fg-muted">
          <Workflow className="h-4 w-4 shrink-0" aria-hidden="true" />
          {kind === "film" ? "Произведение ещё не ставили в конвейер." : "Ролик ещё не ставили в конвейер."}
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {items.slice(0, 5).map((item) => (
            <li key={item.id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
              <span className="flex min-w-0 flex-wrap items-center gap-2">
                <StatusBadge
                  status={item.status as PipelineState}
                  map={PIPELINE_STATE_META}
                  fallbackLabel={item.status_ru ?? item.status}
                />
                <span className="font-mono text-xs text-fg-muted">#{item.id}</span>
                {item.as_source_id ? <span className="text-xs text-fg-muted">источник {item.as_source_id}</span> : null}
                {item.as_jobs?.length ? (
                  <span className="text-xs text-fg-muted">клипов {item.as_jobs.length}</span>
                ) : null}
                {item.as_publications?.length ? (
                  <span className="text-xs text-fg-muted">публикаций {item.as_publications.length}</span>
                ) : null}
              </span>
              <span className="tabular font-mono text-xs text-fg-muted" title={item.updated_at ?? undefined}>
                {formatAgo(item.updated_at, now)}
              </span>
              {item.last_error ? (
                <span className="basis-full text-xs text-negative" title={item.last_error}>
                  {item.last_error}
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      {operator ? (
        <div className="flex items-center justify-between gap-2 border-t border-border pt-3">
          <span className="text-xs text-fg-muted">
            {active
              ? "Задание уже в работе — новое Kino-trends не создаст."
              : "Файл, нарезка, оформление, ревью, залив."}
          </span>
          <EnqueueButton
            kind={kind}
            refId={refId}
            size="sm"
            disabled={enqueueDisabled || !!active}
            title={active ? "Активное задание уже есть" : enqueueTitle}
          />
        </div>
      ) : null}
    </SectionCard>
  );
}
