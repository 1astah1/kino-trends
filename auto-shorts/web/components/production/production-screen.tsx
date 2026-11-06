"use client";

import { Plus, Search, X } from "lucide-react";
import { parseAsInteger, parseAsString, parseAsStringLiteral, useQueryStates } from "nuqs";
import { useEffect, useEffectEvent, useState } from "react";

import { useCurrentUser } from "@/components/auth/auth-context";
import { PageHeader } from "@/components/common/page-header";
import { SectionError } from "@/components/common/section-error";
import { NewJobSheetHost } from "@/components/create-job/new-job-sheet-host";
import { openNewJobSheet } from "@/components/create-job/new-job-store";
import { isTypingTarget } from "@/components/review/review-logic";
import { Button, Input, Pagination, Select } from "@/components/ui";
import type { Schemas } from "@/lib/api/client";
import { canOperate } from "@/lib/auth";
import { cn } from "@/lib/cn";
import { useNow } from "@/lib/hooks/use-now";
import { VIDEO_UID_RE } from "@/lib/palette";
import { JOBS_PAGE_SIZE, useChannelOptions, useJobs, useStageCounts } from "@/lib/queries/jobs";
import { useSystemStatus } from "@/lib/queries/system";

import { JOB_TABS, JobDetailPane, type JobTab } from "./job-detail-pane";
import { JOB_KIND_LABELS, JOB_STATUS_META, jobStageLabel, PLATFORM_LABELS } from "./job-meta";
import { JobsTable, type JobsSortKey } from "./jobs-table";
import { StageRail } from "./stage-rail";
import { TtsMockCallout } from "./tts-mock-callout";

type JobStatus = Schemas["JobStatus"];
type JobStage = Schemas["JobStage"];

const JOB_STATUSES = Object.keys(JOB_STATUS_META) as JobStatus[];
const JOB_STAGES = [
  "script",
  "voice",
  "visuals",
  "clip_fetch",
  "clip_analyze",
  "author_script",
  "author_voice",
  "render",
  "qc",
  "package",
] as const satisfies readonly JobStage[];
const KINDS = ["generate", "clip"] as const;
const SORTS = [
  "-created_at",
  "created_at",
  "-priority",
  "priority",
  "-duration_sec",
  "duration_sec",
  "-cost_usd",
  "cost_usd",
];

const urlState = {
  status: parseAsStringLiteral(JOB_STATUSES),
  stage: parseAsStringLiteral(JOB_STAGES),
  kind: parseAsStringLiteral(KINDS),
  channel: parseAsString,
  q: parseAsString,
  job: parseAsInteger,
  tab: parseAsStringLiteral(JOB_TABS).withDefault("script"),
  page: parseAsInteger.withDefault(1),
  sort: parseAsStringLiteral(SORTS).withDefault("-created_at"),
};

function QueueSummary() {
  const status = useSystemStatus({ slowWhenLive: 30_000 });
  const data = status.data;
  if (!data) return null;
  const slot = data.heavy_slot;
  return (
    <span
      className="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-2xs text-fg-muted tabular"
      data-testid="queue-summary"
    >
      <span>
        тяжёлые: <span className="text-fg">{data.queue.heavy.queued}</span> в очереди ·{" "}
        <span className="text-machine">{data.queue.heavy.running}</span> идёт
        {data.queue.heavy.deferred ? (
          <>
            {" "}
            · <span className="text-warning">{data.queue.heavy.deferred}</span> отложено
          </>
        ) : null}
      </span>
      <span>
        лёгкие: <span className="text-fg">{data.queue.light.queued}</span> в очереди ·{" "}
        <span className="text-machine">{data.queue.light.running}</span> идёт
      </span>
      {slot.task_id !== null ? (
        <span className="text-machine">
          слот: #{slot.task_id} · {jobStageLabel(slot.stage)}
          {slot.pct !== null ? ` · ${Math.round(slot.pct)}%` : ""}
        </span>
      ) : (
        <span>слот свободен</span>
      )}
    </span>
  );
}

/**
 * Экран «Конвейер» (§7.3): лента стадий-фильтров, плотная таблица роликов, панель ролика справа
 * (на телефоне — поверх). Состояние в URL: `stage`, `status`, `kind`, `channel`, `q`, `job`, `tab`, `page`, `sort`.
 */
export function ProductionScreen() {
  const user = useCurrentUser();
  const now = useNow(30_000);
  const [params, setParams] = useQueryStates(urlState, { history: "replace" });
  const { status, stage, kind, channel, q, job, tab, page, sort } = params;

  // Поиск: печатаем локально, в URL — после паузы 300 мс.
  const [search, setSearch] = useState(q ?? "");
  const [trackedQ, setTrackedQ] = useState(q);
  if (trackedQ !== q) {
    setTrackedQ(q);
    setSearch(q ?? "");
  }
  useEffect(() => {
    const value = search.trim();
    if (value === (q ?? "")) return;
    const timer = setTimeout(() => void setParams({ q: value || null, page: null }), 300);
    return () => clearTimeout(timer);
  }, [search, q, setParams]);

  const offset = Math.max(0, (page - 1) * JOBS_PAGE_SIZE);
  const filters = {
    kind,
    status: status ? [status] : null,
    stage: stage ? [stage] : null,
    channel_id: channel,
    q,
    sort,
    limit: JOBS_PAGE_SIZE,
    offset,
  };
  const jobs = useJobs(filters);
  const counts = useStageCounts();
  const channels = useChannelOptions();
  const rows = jobs.data?.items ?? [];

  // Переход из палитры по video_uid: единственное совпадение сразу открывается.
  const onlyMatch = q && VIDEO_UID_RE.test(q) && rows.length === 1 && rows[0].video_uid === q ? rows[0].id : null;
  useEffect(() => {
    if (job === null && onlyMatch !== null) void setParams({ job: onlyMatch });
  }, [job, onlyMatch, setParams]);

  const openJob = (id: number | null) => void setParams({ job: id });

  const onEscape = useEffectEvent((event: KeyboardEvent) => {
    if (event.key !== "Escape" || job === null || event.defaultPrevented) return;
    if (isTypingTarget(event.target)) return;
    if (document.querySelector('[role="dialog"][data-state="open"]')) return;
    openJob(null);
  });
  useEffect(() => {
    const handler = (event: KeyboardEvent) => onEscape(event);
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const onSort = (key: JobsSortKey) => {
    const next = sort === `-${key}` ? key : `-${key}`;
    void setParams({ sort: next, page: null });
  };

  const filtered = Boolean(status || stage || kind || channel || q);

  return (
    <div className="flex flex-col lg:h-full lg:min-h-0" data-testid="production-screen">
      <NewJobSheetHost />
      <div className="flex flex-col gap-3 px-4 pt-4 md:px-6 md:pt-5">
        <PageHeader
          title="Конвейер"
          description={<QueueSummary />}
          actions={
            canOperate(user.role) ? (
              <Button onClick={() => openNewJobSheet()} data-testid="new-job-open">
                <Plus className="h-4 w-4" aria-hidden="true" />
                Новый ролик
              </Button>
            ) : null
          }
        />
        <TtsMockCallout />
        <StageRail
          counts={counts.data}
          isLoading={counts.isPending}
          status={status}
          stage={stage}
          onSelect={(next) => void setParams({ status: next.status, stage: next.stage, page: null })}
        />
        <div className="flex flex-wrap items-center gap-2" role="search">
          <div className="relative min-w-48 flex-1 sm:max-w-xs">
            <Search
              className="pointer-events-none absolute top-1/2 left-2 h-3.5 w-3.5 -translate-y-1/2 text-fg-muted"
              aria-hidden="true"
            />
            <Input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="video_uid, тема, код"
              aria-label="Поиск роликов"
              className="h-8 pl-7"
            />
          </div>
          <Select
            aria-label="Вид"
            value={kind ?? ""}
            onChange={(event) =>
              void setParams({ kind: (event.target.value || null) as (typeof KINDS)[number] | null, page: null })
            }
            className="w-auto"
          >
            <option value="">Все виды</option>
            {KINDS.map((item) => (
              <option key={item} value={item}>
                {JOB_KIND_LABELS[item]}
              </option>
            ))}
          </Select>
          <Select
            aria-label="Канал"
            value={channel ?? ""}
            onChange={(event) => void setParams({ channel: event.target.value || null, page: null })}
            className="w-auto max-w-56"
          >
            <option value="">Все каналы</option>
            {channel && !channels.data?.some((item) => item.id === channel) ? (
              <option value={channel}>{channel}</option>
            ) : null}
            {(channels.data ?? []).map((item) => (
              <option key={item.id} value={item.id}>
                {item.title ?? item.id} · {PLATFORM_LABELS[item.platform]}
              </option>
            ))}
          </Select>
          <Select
            aria-label="Статус"
            value={status ?? ""}
            onChange={(event) =>
              void setParams({ status: (event.target.value || null) as JobStatus | null, stage: null, page: null })
            }
            className="w-auto"
          >
            <option value="">Все статусы</option>
            {JOB_STATUSES.map((item) => (
              <option key={item} value={item}>
                {JOB_STATUS_META[item].label}
              </option>
            ))}
          </Select>
          {filtered ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setSearch("");
                void setParams({ status: null, stage: null, kind: null, channel: null, q: null, page: null });
              }}
            >
              <X className="h-3.5 w-3.5" aria-hidden="true" />
              Сбросить
            </Button>
          ) : null}
        </div>
      </div>

      <div
        className={cn(
          "mt-3 grid min-h-0 flex-1 grid-cols-1",
          job !== null && "lg:grid-cols-[minmax(0,1fr)_minmax(440px,42%)]",
        )}
      >
        <div className="flex min-h-0 flex-col gap-3 px-4 pb-6 md:px-6 lg:overflow-y-auto">
          {jobs.isError ? (
            <SectionError error={jobs.error} onRetry={() => void jobs.refetch()} />
          ) : (
            <>
              <JobsTable
                rows={rows}
                isLoading={jobs.isPending}
                selectedId={job}
                onSelect={(id) => openJob(id === job ? null : id)}
                sort={sort}
                onSort={onSort}
                now={now}
                emptyTitle={filtered ? "Ничего не найдено" : "Роликов пока нет"}
                emptyDescription={
                  filtered
                    ? "Смените фильтр или сбросьте его."
                    : "Создайте первый: «Новый ролик» — генерация по теме ниши канала."
                }
              />
              {jobs.data && jobs.data.total > JOBS_PAGE_SIZE ? (
                <Pagination
                  total={jobs.data.total}
                  limit={JOBS_PAGE_SIZE}
                  offset={offset}
                  onOffsetChange={(next) => void setParams({ page: Math.floor(next / JOBS_PAGE_SIZE) + 1 })}
                />
              ) : null}
            </>
          )}
        </div>
        {job !== null ? (
          <aside
            className="fixed inset-0 z-40 overflow-y-auto bg-surface lg:static lg:z-auto lg:min-h-0 lg:border-l lg:border-border"
            aria-label="Панель ролика"
          >
            <JobDetailPane
              key={job}
              jobId={job}
              tab={tab as JobTab}
              onTabChange={(next) => void setParams({ tab: next === "script" ? null : next })}
              onClose={() => openJob(null)}
              onOpenJob={(id) => void setParams({ job: id, tab: null })}
            />
          </aside>
        ) : null}
      </div>
    </div>
  );
}
