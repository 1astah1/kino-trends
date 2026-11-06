"use client";

import { FileVideoCamera, Link2, Plus, Scissors, Search, X } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { parseAsInteger, parseAsString, parseAsStringLiteral, useQueryStates } from "nuqs";
import { type KeyboardEvent, useEffect, useState } from "react";

import { useCurrentUser } from "@/components/auth/auth-context";
import { PageHeader } from "@/components/common/page-header";
import { SectionError } from "@/components/common/section-error";
import {
  Badge,
  Button,
  EmptyState,
  Input,
  Pagination,
  PillToggle,
  SkeletonTable,
  StatusBadge,
  Table,
  TableBody,
  TableHead,
  Td,
  Th,
} from "@/components/ui";
import { canOperate } from "@/lib/auth";
import { cn } from "@/lib/cn";
import { formatAgo, formatDateTime } from "@/lib/format";
import { useNow } from "@/lib/hooks/use-now";
import { routeHref } from "@/lib/nav";
import { isSourceProcessing, SOURCES_PAGE_SIZE, type SourceRow, useSources } from "@/lib/queries/clipping";
import { useSystemStatus } from "@/lib/queries/system";

import { AddSourceDialog } from "./add-source-dialog";
import { formatLength } from "./clip-math";
import {
  RIGHTS_TYPE_LABELS,
  SELECTION_MODE_META,
  SOURCE_FILTER_KEYS,
  SOURCE_FILTERS,
  SOURCE_ORIGIN_LABELS,
  SOURCE_STATUS_META,
  SOURCE_STEP_LABELS,
} from "./labels";

const urlState = {
  status: parseAsStringLiteral(SOURCE_FILTER_KEYS).withDefault("all"),
  q: parseAsString,
  page: parseAsInteger.withDefault(1),
};

/** Сводка тяжёлой очереди: подготовка и распознавание источников идут через один слот. */
function HeavyQueue() {
  const status = useSystemStatus({ slowWhenLive: 30_000 });
  const data = status.data;
  if (!data) return <span>Свои и лицензированные длинные видео: прогресс обработки и место в тяжёлой очереди.</span>;
  const heavy = data.queue.heavy;
  return (
    <span
      className="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-2xs text-fg-muted tabular"
      data-testid="heavy-queue"
    >
      <span>
        тяжёлая очередь: <span className="text-fg">{heavy.queued}</span> ждёт ·{" "}
        <span className="text-machine">{heavy.running}</span> идёт
        {heavy.deferred ? (
          <>
            {" "}
            · <span className="text-warning">{heavy.deferred}</span> отложено
          </>
        ) : null}
      </span>
      <span>
        диск:{" "}
        <span className={cn(data.disk.free_gb < data.disk.thresholds.soft_gb ? "text-warning" : "text-fg")}>
          {data.disk.free_gb.toLocaleString("ru-RU", { maximumFractionDigits: 1 })} ГБ
        </span>{" "}
        свободно
      </span>
    </span>
  );
}

export function SourceProgressCell({ row }: { row: SourceRow }) {
  if (row.status === "failed") {
    return (
      <span className="block max-w-72 truncate text-xs text-negative" title={row.error ?? undefined}>
        {row.error ?? "Ошибка обработки"}
      </span>
    );
  }
  if (!isSourceProcessing(row.status)) {
    return row.status === "ready" ? <span className="text-xs text-fg-muted">готов</span> : null;
  }
  const pct = row.progress === null || row.progress === undefined ? null : Math.max(0, Math.min(100, row.progress));
  return (
    <span className="flex flex-wrap items-center gap-2">
      <span className="text-xs text-machine">{row.current_step ? SOURCE_STEP_LABELS[row.current_step] : "ждёт"}</span>
      {pct !== null ? (
        <span className="flex items-center gap-1" title={`${Math.round(pct)}%`}>
          <span className="h-1 w-16 overflow-hidden rounded-full bg-surface-2" aria-hidden="true">
            <span className="block h-full bg-machine" style={{ width: `${pct}%` }} />
          </span>
          <span className="font-mono text-2xs text-fg-muted tabular">{Math.round(pct)}%</span>
        </span>
      ) : null}
      {row.queue_position ? (
        <span className="font-mono text-2xs text-fg-muted tabular" title="Место в тяжёлой очереди">
          №{row.queue_position} в очереди
        </span>
      ) : null}
    </span>
  );
}

function SourcesTable({
  rows,
  now,
  onOpen,
}: {
  rows: readonly SourceRow[];
  now: number;
  onOpen: (id: number) => void;
}) {
  const onKey = (event: KeyboardEvent<HTMLTableRowElement>, id: number) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onOpen(id);
    }
  };
  return (
    <Table minWidth={980} data-testid="sources-table">
      <caption className="sr-only">Источники нарезки</caption>
      <TableHead>
        <tr>
          <Th>Источник</Th>
          <Th>Права</Th>
          <Th>Статус</Th>
          <Th>Обработка</Th>
          <Th className="text-right">Длина</Th>
          <Th>Моменты</Th>
          <Th className="text-right">Ролики</Th>
          <Th>Обновлён</Th>
        </tr>
      </TableHead>
      <TableBody>
        {rows.map((row) => {
          const OriginIcon = row.origin === "url" ? Link2 : FileVideoCamera;
          return (
            <tr
              key={row.id}
              data-source-id={row.id}
              tabIndex={0}
              onClick={() => onOpen(row.id)}
              onKeyDown={(event) => onKey(event, row.id)}
              className="h-8 cursor-pointer transition-colors duration-150 hover:bg-surface-2"
            >
              <Td className="max-w-80">
                <span className="flex min-w-0 items-center gap-2">
                  <OriginIcon
                    className="h-3.5 w-3.5 shrink-0 text-fg-muted"
                    aria-label={SOURCE_ORIGIN_LABELS[row.origin]}
                  />
                  <Link
                    href={routeHref("/clipping/source", { id: String(row.id) })}
                    className="truncate text-fg hover:text-accent"
                    onClick={(event) => event.stopPropagation()}
                  >
                    {row.title}
                  </Link>
                  <span className="font-mono text-2xs text-fg-muted">№{row.id}</span>
                  {row.watermark_suspect ? (
                    <Badge variant="warning" title="При подготовке найден возможный чужой логотип">
                      логотип
                    </Badge>
                  ) : null}
                </span>
              </Td>
              <Td>
                <Badge
                  variant={row.rights_type === "own" ? "neutral" : "warning"}
                  outline
                  className="whitespace-nowrap"
                >
                  {RIGHTS_TYPE_LABELS[row.rights_type]}
                </Badge>
              </Td>
              <Td>
                <StatusBadge status={row.status} map={SOURCE_STATUS_META} className="whitespace-nowrap" />
              </Td>
              <Td>
                <SourceProgressCell row={row} />
              </Td>
              <Td className="text-right font-mono text-xs whitespace-nowrap">{formatLength(row.duration_sec)}</Td>
              <Td>
                <span className="flex items-center gap-2">
                  <span className="font-mono text-xs tabular">{row.candidates_count}</span>
                  {row.selection_mode ? (
                    <StatusBadge status={row.selection_mode} map={SELECTION_MODE_META} className="whitespace-nowrap" />
                  ) : null}
                </span>
              </Td>
              <Td className="text-right font-mono text-xs tabular">{row.jobs_count}</Td>
              <Td className="text-xs whitespace-nowrap text-fg-muted" title={formatDateTime(row.updated_at)}>
                {formatAgo(row.updated_at, now)}
              </Td>
            </tr>
          );
        })}
      </TableBody>
    </Table>
  );
}

/**
 * Экран «Источники нарезки» (§7.3 `/clipping?status=`): таблица с прогрессом и местом в тяжёлой очереди
 * (живёт от SSE `source_status`), диалог «Добавить» (файл через tus или ссылка + обязательные права).
 */
export function SourcesScreen() {
  const router = useRouter();
  const user = useCurrentUser();
  const operator = canOperate(user.role);
  const now = useNow(30_000);
  const [params, setParams] = useQueryStates(urlState, { history: "replace" });
  const { status, q, page } = params;
  const [adding, setAdding] = useState(false);

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

  const offset = Math.max(0, (page - 1) * SOURCES_PAGE_SIZE);
  const filter = SOURCE_FILTERS[status];
  const sources = useSources({
    status: filter.statuses ? [...filter.statuses] : null,
    q,
    sort: "-updated_at",
    limit: SOURCES_PAGE_SIZE,
    offset,
  });
  const rows = sources.data?.items ?? [];
  const open = (id: number) => router.push(routeHref("/clipping/source", { id: String(id) }));
  const filtered = status !== "all" || !!q;

  return (
    <div className="flex flex-col gap-3 p-4 md:p-6" data-testid="sources-screen">
      <AddSourceDialog open={adding} onClose={() => setAdding(false)} />
      <PageHeader
        title="Источники нарезки"
        description={<HeavyQueue />}
        actions={
          operator ? (
            <Button onClick={() => setAdding(true)} data-testid="add-source-open">
              <Plus className="h-4 w-4" aria-hidden="true" />
              Добавить источник
            </Button>
          ) : null
        }
      />
      <div className="flex flex-wrap items-center gap-2" role="search">
        <PillToggle
          label="Статус"
          value={status}
          onChange={(next) => void setParams({ status: next === "all" ? null : next, page: null })}
          options={SOURCE_FILTER_KEYS.map((key) => ({ value: key, label: SOURCE_FILTERS[key].label }))}
        />
        <div className="relative min-w-48 flex-1 sm:max-w-xs">
          <Search
            className="pointer-events-none absolute top-1/2 left-2 h-3.5 w-3.5 -translate-y-1/2 text-fg-muted"
            aria-hidden="true"
          />
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Название"
            aria-label="Поиск по названию"
            className="pl-7"
          />
        </div>
        {filtered ? (
          <Button variant="ghost" size="sm" onClick={() => void setParams({ status: null, q: null, page: null })}>
            <X className="h-3.5 w-3.5" aria-hidden="true" />
            Сбросить
          </Button>
        ) : null}
      </div>

      {sources.isError ? (
        <SectionError error={sources.error} onRetry={() => void sources.refetch()} />
      ) : sources.isPending ? (
        <SkeletonTable rows={6} />
      ) : rows.length === 0 ? (
        <EmptyState
          icon={Scissors}
          title={filtered ? "Ничего не нашлось" : "Источников пока нет"}
          description={
            filtered
              ? "Смените фильтр или поиск."
              : "Добавьте своё или лицензированное длинное видео: система распознает речь и предложит моменты для клипов."
          }
          action={
            operator && !filtered ? (
              <Button onClick={() => setAdding(true)}>
                <Plus className="h-4 w-4" aria-hidden="true" />
                Добавить источник
              </Button>
            ) : null
          }
        />
      ) : (
        <>
          <SourcesTable rows={rows} now={now} onOpen={open} />
          {sources.data && sources.data.total > SOURCES_PAGE_SIZE ? (
            <Pagination
              total={sources.data.total}
              limit={SOURCES_PAGE_SIZE}
              offset={offset}
              onOffsetChange={(next) => void setParams({ page: Math.floor(next / SOURCES_PAGE_SIZE) + 1 })}
            />
          ) : null}
        </>
      )}
    </div>
  );
}
