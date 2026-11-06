"use client";

import { useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { parseAsStringLiteral, useQueryState } from "nuqs";

import { useCurrentUser } from "@/components/auth/auth-context";
import { PageHeader } from "@/components/common/page-header";
import { SectionError } from "@/components/common/section-error";
import { Badge, Button, Tabs, tabPanelId } from "@/components/ui";
import { canOperate, isAdmin } from "@/lib/auth";
import { cn } from "@/lib/cn";
import { formatTime } from "@/lib/format";
import { useNow } from "@/lib/hooks/use-now";
import { queryKeys } from "@/lib/queries/keys";
import { scheduleKeys } from "@/lib/queries/schedules";
import { useSystemStatus } from "@/lib/queries/system";
import { useTasks } from "@/lib/queries/tasks";
import { trendsKeys, useTrendsStatus } from "@/lib/queries/trends";
import { useTrendsRuns } from "@/lib/queries/trends-ops";

import { ControlPanel } from "./control-panel";
import { FAILED_FILTERS, FailedPanel, isRecent } from "./failed-panel";
import { HistoryPanel } from "./history-panel";
import { NowPanel } from "./now-panel";
import { QueuePanel } from "./queue-panel";
import { SummaryBar } from "./summary-bar";

const TABS = ["now", "queue", "failed", "history", "control"] as const;
type TabId = (typeof TABS)[number];
const TAB_LABELS: Record<TabId, string> = {
  now: "Сейчас",
  queue: "Очередь",
  failed: "Сбои",
  history: "Прогоны",
  control: "Управление",
};
const ID_PREFIX = "queue";

/**
 * Очередь (`/queue?tab=now|queue|failed|history|control`): всё, что система делает в фоне, в двух
 * системах — задачи воркеров auto-shorts и фон Kino-trends (сбор трендов, конвейер), с управлением:
 * отменить, повторить, запустить сейчас, включить и выключить автоматику. Живое обновление — события
 * SSE плюс опрос 5–10 с на вкладке «Сейчас».
 */
export function QueueScreen() {
  const user = useCurrentUser();
  const operator = canOperate(user.role);
  const admin = isAdmin(user.role);
  const queryClient = useQueryClient();
  const now = useNow(5_000);
  const [tab, setTab] = useQueryState(
    "tab",
    parseAsStringLiteral(TABS).withDefault("now").withOptions({ history: "replace" }),
  );

  const status = useSystemStatus({ slowWhenLive: 10_000 });
  const trends = useTrendsStatus();
  const trendsConfigured = trends.data?.configured ?? false;
  const runs = useTrendsRuns({}, { enabled: trendsConfigured });
  const failed = useTasks(FAILED_FILTERS, { whenLive: 30_000 });
  const failed24h = failed.data ? failed.data.items.filter((task) => isRecent(task, now)).length : null;

  const queue = status.data?.queue;
  const runningCount = queue ? queue.heavy.running + queue.light.running + (runs.data?.current.running ? 1 : 0) : null;
  const queuedCount = queue
    ? queue.heavy.queued + queue.light.queued + queue.heavy.deferred + queue.light.deferred
    : null;
  const failedCount =
    failed24h === null ? null : failed24h + (trendsConfigured ? (trends.data?.pipeline?.failed ?? 0) : 0);

  const refreshing = status.isFetching || trends.isFetching || runs.isFetching || failed.isFetching;
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.tasks });
    void queryClient.invalidateQueries({ queryKey: queryKeys.systemStatus });
    void queryClient.invalidateQueries({ queryKey: trendsKeys.all });
    void queryClient.invalidateQueries({ queryKey: scheduleKeys.list });
  };

  const badge = (value: number | null, variant: "machine" | "neutral" | "negative" | "warning") =>
    value ? (
      <Badge variant={variant} className="font-mono">
        {value}
      </Badge>
    ) : undefined;

  return (
    <div className="flex flex-col gap-4 p-4 md:p-6" data-testid="queue-screen">
      <PageHeader
        eyebrow="Пульт"
        title="Очередь"
        description="Что система делает сама прямо сейчас и что у неё в планах: задачи воркеров auto-shorts и фон Kino-trends — сбор трендов, фоновые работы, конвейер. Отсюда можно отменить, повторить, запустить сейчас и переключить автоматику."
        actions={
          <Button
            variant="ghost"
            size="sm"
            onClick={refresh}
            disabled={refreshing}
            title="Перечитать задачи, воркеры и фон Kino-trends"
          >
            <RefreshCw className={cn("h-3.5 w-3.5", refreshing && "animate-spin")} aria-hidden="true" />
            Обновить
          </Button>
        }
      />

      {status.isError ? (
        <SectionError error={status.error} onRetry={() => void status.refetch()} title="Нет ответа /system/status" />
      ) : null}

      <SummaryBar
        status={status.data}
        failed24h={failed24h}
        runs={runs.data}
        trends={trends.data}
        trendsConfigured={trendsConfigured}
        trendsPending={trends.isPending}
        now={now}
      />

      <Tabs
        idPrefix={ID_PREFIX}
        label="Разделы очереди"
        value={tab}
        onChange={(value) => void setTab(value as TabId)}
        items={TABS.map((value) => ({
          value,
          label: TAB_LABELS[value],
          badge:
            value === "now"
              ? badge(runningCount, "machine")
              : value === "queue"
                ? badge(queuedCount, "neutral")
                : value === "failed"
                  ? badge(failedCount, "negative")
                  : undefined,
        }))}
      />
      <section id={tabPanelId(ID_PREFIX, tab)} role="tabpanel" aria-labelledby={`${ID_PREFIX}-tab-${tab}`}>
        {tab === "queue" ? (
          <QueuePanel operator={operator} trendsConfigured={trendsConfigured} now={now} />
        ) : tab === "failed" ? (
          <FailedPanel operator={operator} trendsConfigured={trendsConfigured} now={now} />
        ) : tab === "history" ? (
          <HistoryPanel trendsConfigured={trendsConfigured} now={now} />
        ) : tab === "control" ? (
          <ControlPanel operator={operator} admin={admin} trendsConfigured={trendsConfigured} now={now} />
        ) : (
          <NowPanel operator={operator} trendsConfigured={trendsConfigured} now={now} />
        )}
      </section>
      <p className="text-2xs text-fg-muted">
        {status.dataUpdatedAt ? `Состояние воркеров обновлено ${formatTime(status.dataUpdatedAt)}` : "Загрузка…"}
        {runs.dataUpdatedAt ? ` · Kino-trends ${formatTime(runs.dataUpdatedAt)}` : ""}
      </p>
    </div>
  );
}
