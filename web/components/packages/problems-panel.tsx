"use client";

import { CircleCheck, Hourglass, OctagonAlert, PackageX } from "lucide-react";
import Link from "next/link";

import { SectionError } from "@/components/common/section-error";
import { Badge, EmptyState, SectionCard, Skeleton } from "@/components/ui";
import { formatAgo } from "@/lib/format";
import { routeHref } from "@/lib/nav";
import { useOpenAlerts } from "@/lib/queries/alerts";
import { PROBLEM_VERIFY_STATUSES, type Publication, usePublications } from "@/lib/queries/publications";

/** Алерт `package_build`: ролик одобрен, а пакет не собрался (права, оригинальность, erid). */
export const PACKAGE_BLOCKED_ALERT = "package_blocked";

/** Одобренные ролики без пакета — из открытых алертов `package_blocked`. */
export function useBlockedPackages() {
  const alerts = useOpenAlerts();
  return (alerts.data ?? []).filter((alert) => alert.code === PACKAGE_BLOCKED_ALERT);
}

import { PublicationStatusBlock } from "./publication-status";

/**
 * Вкладка «Проблемы» (§7.3): публикации, у которых проверка принадлежности каналу не `ok`, и снятые
 * площадкой. Что делать — в подсказке к статусу: повторить проверку, удалить ошибочную регистрацию
 * (владелец) и вставить правильную ссылку.
 */
export function ProblemsPanel({
  channelId,
  operator,
  admin,
}: {
  channelId: string | null;
  operator: boolean;
  admin: boolean;
}) {
  const problems = usePublications({ verifyStatuses: PROBLEM_VERIFY_STATUSES, channelId });
  const removed = usePublications({ statuses: ["removed_by_platform"], channelId, limit: 50 });
  const pending = usePublications({ verifyStatuses: ["pending"], channelId, limit: 50 }, { pollMs: 15_000 });

  const problemItems = problems.data?.items ?? [];
  const removedItems = (removed.data?.items ?? []).filter((item) => !problemItems.some((p) => p.id === item.id));
  const loading = problems.isPending || removed.isPending;
  const blocked = useBlockedPackages();

  return (
    <div className="flex flex-col gap-4" data-testid="problems-panel">
      {problems.isError ? (
        <SectionError
          error={problems.error}
          onRetry={() => void problems.refetch()}
          title="Реестр публикаций не загрузился"
        />
      ) : null}
      {blocked.length ? (
        <SectionCard title="Одобрены, но пакет не собран" actions={<Badge variant="negative">{blocked.length}</Badge>}>
          <p className="flex items-start gap-1.5 text-xs text-fg-muted">
            <PackageX className="mt-0.5 h-3.5 w-3.5 shrink-0 text-negative" aria-hidden="true" />
            Ролик одобрен, но «К заливке» не попал: права, отчёт об оригинальности или маркировка. Исправьте причину и
            пересоберите пакет из карточки ролика.
          </p>
          <ul className="flex flex-col divide-y divide-border" data-testid="blocked-packages">
            {blocked.map((alert) => {
              const jobId = typeof alert.details.job_id === "number" ? alert.details.job_id : null;
              return (
                <li key={alert.id} className="flex flex-wrap items-center gap-2 py-2 text-sm first:pt-1 last:pb-0">
                  <span className="min-w-0 flex-1 text-fg">{alert.title}</span>
                  <span className="text-2xs text-fg-muted">{formatAgo(alert.last_seen_at)}</span>
                  {jobId !== null ? (
                    <Link
                      href={routeHref("/production", { job: String(jobId) })}
                      className="text-xs text-accent hover:underline"
                    >
                      Открыть в Конвейере
                    </Link>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </SectionCard>
      ) : null}
      {loading ? (
        <Skeleton className="h-40" />
      ) : problemItems.length === 0 && removedItems.length === 0 && blocked.length === 0 ? (
        <EmptyState
          icon={CircleCheck}
          title="Проблем нет"
          description="Все зарегистрированные публикации принадлежат своим каналам, снятых площадкой нет."
          compact
        />
      ) : (
        <>
          {problemItems.length ? (
            <SectionCard
              title="Проверка принадлежности"
              actions={<Badge variant="negative">{problems.data?.total ?? problemItems.length}</Badge>}
            >
              <PublicationList items={problemItems} operator={operator} admin={admin} />
            </SectionCard>
          ) : null}
          {removedItems.length ? (
            <SectionCard title="Сняты площадкой" actions={<Badge variant="negative">{removedItems.length}</Badge>}>
              <p className="flex items-start gap-1.5 text-xs text-fg-muted">
                <OctagonAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-negative" aria-hidden="true" />
                Причина — в карточке публикации; серия снятий на канале — сигнал санкций (журнал инцидентов в
                «Каналах»).
              </p>
              <PublicationList items={removedItems} operator={operator} admin={admin} />
            </SectionCard>
          ) : null}
        </>
      )}
      {pending.data?.items.length ? (
        <SectionCard title="Проверяются" actions={<Badge variant="machine">{pending.data.total}</Badge>}>
          <p className="flex items-start gap-1.5 text-xs text-fg-muted">
            <Hourglass className="mt-0.5 h-3.5 w-3.5 shrink-0 text-machine" aria-hidden="true" />
            YouTube проверяется фоновой задачей через oEmbed; если висит дольше часа — повторите проверку.
          </p>
          <PublicationList items={pending.data.items} operator={operator} admin={admin} compact />
        </SectionCard>
      ) : null}
    </div>
  );
}

function PublicationList({
  items,
  operator,
  admin,
  compact = false,
}: {
  items: Publication[];
  operator: boolean;
  admin: boolean;
  compact?: boolean;
}) {
  return (
    <ul className="flex flex-col divide-y divide-border">
      {items.map((pub) => (
        <li
          key={pub.id}
          className="flex flex-col gap-2 py-2.5 first:pt-0 last:pb-0"
          data-testid="problem-publication"
          data-publication-id={pub.id}
        >
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="font-mono font-medium text-fg">{pub.video_uid}</span>
            <span className="text-fg-muted">·</span>
            <span className="text-fg">{pub.channel.title ?? pub.channel.id}</span>
            <Badge variant={pub.platform === "youtube" ? "accent" : "machine"} outline>
              {pub.platform === "youtube" ? "YouTube" : "VK"}
            </Badge>
            {pub.has_claim ? <Badge variant="warning">клейм</Badge> : null}
            <span className="ml-auto text-2xs text-fg-muted">зарегистрирована {formatAgo(pub.created_at)}</span>
          </div>
          <PublicationStatusBlock publication={pub} operator={operator} admin={admin} compact={compact} />
          {pub.status_reason ? <p className="text-xs text-fg-muted">Причина: {pub.status_reason}</p> : null}
        </li>
      ))}
    </ul>
  );
}
