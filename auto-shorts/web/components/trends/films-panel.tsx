"use client";

import { CalendarCheck, Clapperboard, FileSearch, Search, Workflow } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { SectionError } from "@/components/common/section-error";
import { Switch } from "@/components/queue/switch";
import {
  Badge,
  Button,
  DataTable,
  type DataTableColumn,
  Input,
  Pagination,
  PillToggle,
  Select,
  StatusBadge,
} from "@/components/ui";
import { errorMessage } from "@/lib/api/errors";
import { mediaSrc } from "@/lib/api/media";
import { cn } from "@/lib/cn";
import { formatAgo, formatNumber } from "@/lib/format";
import { useNow } from "@/lib/hooks/use-now";
import { routeHref } from "@/lib/nav";
import {
  type TrendsFilmKind,
  type TrendsFilmPeriod,
  type TrendsFilmRow,
  type TrendsFilmsFilters,
  type TrendsFilmSort,
  useFindTopSources,
  useTrendsFilms,
  useTrendsPipeline,
  useTrendsToday,
} from "@/lib/queries/trends";
import { useTrendsRuns } from "@/lib/queries/trends-ops";

import { EnqueueButton } from "./enqueue-button";
import {
  FILE_STATUS_META,
  fileStatus,
  FILM_KIND_OPTIONS,
  FILM_PERIOD_OPTIONS,
  FILM_SORT_OPTIONS,
  filmReadiness,
  formatDuration,
  isPipelineState,
  PIPELINE_STATE_META,
  RIGHTS_LEVEL_META,
  rightsLevel,
  sourcePlatform,
} from "./labels";

const PAGE_SIZE = 50;
const FIND_TOP_LIMIT = 40;

function useDebouncedValue<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return debounced;
}

/** Постер через наш прокси; без картинки — первая буква названия. */
export function Poster({
  src,
  label,
  className,
}: {
  src: string | null | undefined;
  label: string;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  const url = mediaSrc(src);
  return (
    <span
      className={cn(
        "relative flex shrink-0 items-center justify-center overflow-hidden rounded-sm bg-video text-fg-muted",
        className,
      )}
      aria-hidden="true"
    >
      {url && !failed ? (
        // eslint-disable-next-line @next/next/no-img-element -- постер из Kino-trends через наш API
        <img src={url} alt="" loading="lazy" className="h-full w-full object-cover" onError={() => setFailed(true)} />
      ) : (
        <span className="text-sm font-semibold">{(label || "?").slice(0, 1).toUpperCase()}</span>
      )}
    </span>
  );
}

/** Кнопка «Найти исходники для топа» с ходом фоновой работы Kino-trends (`jobs.sources_top`). */
function FindTopSources({ enabled }: { enabled: boolean }) {
  const runs = useTrendsRuns({ limit: 1 }, { enabled, whenLive: 10_000 });
  const find = useFindTopSources();
  const now = useNow(15_000);
  const job = runs.data?.jobs?.sources_top;
  const running = Boolean(job?.running);
  const last = (job?.last ?? null) as Record<string, unknown> | null;
  const lastAt = typeof last?.at === "string" ? last.at : null;
  const lastError = typeof last?.error === "string" ? last.error : null;
  const lastChecked = typeof last?.checked === "number" ? last.checked : null;

  async function onFind() {
    try {
      await find.mutateAsync(FIND_TOP_LIMIT);
      toast.success("Поиск исходников запущен", {
        description: `Kino-trends обойдёт до ${FIND_TOP_LIMIT} популярных произведений без исходника.`,
      });
    } catch (error) {
      toast.error("Поиск не запущен", { description: errorMessage(error) });
    }
  }

  return (
    <span className="flex flex-wrap items-center gap-2" data-testid="trends-films-find-top">
      <Button
        variant="outline"
        size="sm"
        onClick={() => void onFind()}
        disabled={running || find.isPending}
        title="Фоновый поиск полных фильмов и серий на YouTube и Rutube для популярных произведений без исходника"
      >
        <FileSearch className={cn("h-3.5 w-3.5", running && "animate-pulse")} aria-hidden="true" />
        {running ? "Ищу исходники…" : "Найти исходники для топа"}
      </Button>
      {running ? (
        <span className="text-xs text-machine" aria-live="polite">
          {job?.progress ?? "поиск идёт"}
        </span>
      ) : lastError ? (
        <span className="text-xs text-negative" title={lastError}>
          прошлый поиск упал
        </span>
      ) : lastAt ? (
        <span className="text-xs text-fg-muted">
          прошлый поиск {formatAgo(lastAt, now)}
          {lastChecked !== null ? ` · проверено ${lastChecked}` : ""}
        </span>
      ) : null}
    </span>
  );
}

/**
 * Вкладка «Кино и сериалы»: произведения, которые сейчас режут в шортсы (данные Kino-trends), с правами,
 * исходником и готовностью. По умолчанию показываем только то, с чем реально можно работать: права проходят,
 * исходник подтверждён, не в конвейере (`ready=1` считает Kino-trends; тумблер снимает фильтр).
 */
export function FilmsPanel({ operator, enabled }: { operator: boolean; enabled: boolean }) {
  const [kind, setKind] = useState<TrendsFilmKind>("all");
  const [period, setPeriod] = useState<TrendsFilmPeriod>("30");
  const [sort, setSort] = useState<TrendsFilmSort>("score");
  const [readyOnly, setReadyOnly] = useState(true);
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const q = useDebouncedValue(search.trim(), 350);

  const filters: TrendsFilmsFilters = {
    kind,
    period,
    sort,
    ready: readyOnly,
    q: q || null,
    limit: PAGE_SIZE,
    offset,
  };
  const films = useTrendsFilms(filters, { enabled });
  const today = useTrendsToday({ enabled });
  const pipeline = useTrendsPipeline({ limit: 500 }, { enabled });

  const scheduled = new Set(
    [...(today.data?.schedule.today ?? []), ...(today.data?.schedule.tomorrow ?? [])]
      .map((slot) => slot.title_id)
      .filter((id): id is string => !!id),
  );
  const pipelineByRef = new Map<string, string>();
  for (const item of pipeline.data?.items ?? []) {
    if (item.kind === "film" && !pipelineByRef.has(item.ref_id)) pipelineByRef.set(item.ref_id, item.status);
  }

  function resetAnd(update: () => void) {
    update();
    setOffset(0);
  }

  const columns: Array<DataTableColumn<TrendsFilmRow>> = [
    {
      key: "title",
      header: "Произведение",
      className: "min-w-72",
      render: (row) => {
        const sub = [row.kind_ru, row.year, row.countries?.[0], (row.genres ?? []).slice(0, 3).join(", ")]
          .filter(Boolean)
          .join(" · ");
        const inPipeline = row.in_pipeline ?? pipelineByRef.get(row.title_id) ?? null;
        return (
          <span className="flex items-center gap-2.5">
            <Poster src={row.poster_url} label={row.label ?? row.title_id} className="h-14 w-10" />
            <span className="flex min-w-0 flex-col gap-0.5">
              <span className="flex min-w-0 flex-wrap items-center gap-1.5">
                <Link
                  href={routeHref("/trends/film", { id: row.title_id })}
                  className="truncate font-medium text-fg hover:underline"
                  title={row.label ?? row.title_id}
                  data-testid="trends-film-link"
                >
                  {row.label ?? row.title_id}
                </Link>
                {scheduled.has(row.title_id) ? (
                  <Badge variant="human" outline title="Стоит в календаре Kino-trends на сегодня или завтра">
                    <CalendarCheck className="h-3 w-3" aria-hidden="true" />в календаре
                  </Badge>
                ) : null}
                {inPipeline ? (
                  isPipelineState(inPipeline) ? (
                    <StatusBadge status={inPipeline} map={PIPELINE_STATE_META} />
                  ) : (
                    <Badge variant="machine">
                      <Workflow className="h-3 w-3" aria-hidden="true" />в конвейере
                    </Badge>
                  )
                ) : null}
                {row.opportunity ? (
                  <Badge variant="positive" outline title="Высокая медиана просмотров при малом числе каналов">
                    мало конкурентов
                  </Badge>
                ) : null}
              </span>
              <span className="truncate text-2xs text-fg-muted">{sub}</span>
            </span>
          </span>
        );
      },
    },
    {
      key: "rights",
      header: "Права",
      render: (row) => {
        const level = rightsLevel(row.rights?.level);
        return (
          <span
            className="flex flex-col items-start gap-0.5"
            title={(row.rights?.reasons ?? []).join("\n") || undefined}
          >
            <StatusBadge status={level} map={RIGHTS_LEVEL_META} />
            {row.rights?.risk !== null && row.rights?.risk !== undefined ? (
              <span className="tabular text-2xs text-fg-muted">
                риск {formatNumber(row.rights.risk)}
                {row.rights.clip_max_s ? ` · клип до ${row.rights.clip_max_s} с` : ""}
              </span>
            ) : null}
          </span>
        );
      },
    },
    {
      key: "source",
      header: "Исходник",
      className: "min-w-40",
      render: (row) => {
        const source = row.source;
        if (source === undefined) return <span className="text-xs text-fg-muted">—</span>; // старый Kino-trends без поля
        if (!source) return <span className="text-xs text-negative">нет</span>;
        const status = fileStatus(source.file_status);
        return (
          <span className="flex flex-col gap-0.5 text-xs">
            <span className="flex flex-wrap items-center gap-1">
              <Badge variant={source.verified ? "positive" : "warning"} outline>
                {sourcePlatform(source.url)}
                {source.verified ? "" : " · не подтверждён"}
              </Badge>
              {source.file_status ? <StatusBadge status={status} map={FILE_STATUS_META} /> : null}
            </span>
            <span className="truncate text-fg-muted" title={source.label ?? undefined}>
              {[source.channel, source.duration_s ? formatDuration(source.duration_s) : null, source.language]
                .filter(Boolean)
                .join(" · ")}
            </span>
            {source.file_error ? (
              <span className="truncate text-negative" title={source.file_error}>
                {source.file_error}
              </span>
            ) : null}
          </span>
        );
      },
    },
    {
      key: "shorts",
      header: "Нарезок",
      className: "text-right",
      render: (row) => (
        <span className="flex flex-col items-end">
          <span className="tabular font-mono text-xs">{formatNumber(row.n_shorts)}</span>
          <span className="tabular text-2xs text-fg-muted">
            {row.n_fresh7 ? `+${formatNumber(row.n_fresh7)} за 7 дн` : ""}
            {row.n_channels ? `${row.n_fresh7 ? " · " : ""}${formatNumber(row.n_channels)} кан.` : ""}
          </span>
        </span>
      ),
    },
    {
      key: "growth",
      header: "Рост 7 дн",
      className: "text-right",
      render: (row) => (
        <span
          className="tabular font-mono text-xs"
          title={row.growth7_measured ? `замерено роликов: ${row.growth7_measured}` : "нужно минимум два замера"}
        >
          {row.growth7 && row.growth7_measured ? `+${formatNumber(row.growth7)}` : "—"}
        </span>
      ),
    },
    {
      key: "median",
      header: "Медиана",
      className: "text-right",
      render: (row) => (
        <span className="flex flex-col items-end">
          <span className="tabular font-mono text-xs" title="Половина чужих нарезок набрала больше">
            {formatNumber(row.views_median)}
          </span>
          <span className="tabular text-2xs text-fg-muted">P90 {formatNumber(row.views_p90)}</span>
        </span>
      ),
    },
    {
      key: "outliers",
      header: "Аутлаеры",
      className: "text-right",
      render: (row) =>
        row.outlier_share !== null && row.outlier_share !== undefined ? (
          <span
            className="tabular font-mono text-xs"
            title={`${row.outliers ?? 0} из ${row.outlier_rated ?? 0} роликов в 3+ раза выше нормы канала${row.outlier_max ? `, максимум ×${Math.round(row.outlier_max)}` : ""}`}
          >
            {Math.round(row.outlier_share * 100)} %
            {row.outlier_max && row.outlier_max >= 10 ? (
              <span className="ml-1 text-positive">×{Math.round(row.outlier_max)}</span>
            ) : null}
          </span>
        ) : (
          <span className="text-xs text-fg-muted">—</span>
        ),
    },
    {
      key: "score",
      header: "Балл",
      className: "text-right",
      render: (row) => (
        <span className="tabular font-mono text-xs" title="Сводный балл темы 0–100">
          {row.score !== null && row.score !== undefined ? formatNumber(row.score) : "—"}
        </span>
      ),
    },
    {
      key: "ready",
      header: "Готовность",
      render: (row) => {
        const readiness = filmReadiness(row, pipelineByRef.get(row.title_id));
        return readiness.ready ? (
          <Badge variant="positive">готово к нарезке</Badge>
        ) : (
          <span className="flex flex-col gap-0.5" title={readiness.reasons.join("\n")}>
            <Badge variant="neutral" outline>
              не готово
            </Badge>
            <span className="max-w-44 truncate text-2xs text-fg-muted">{readiness.reasons.join("; ")}</span>
          </span>
        );
      },
    },
    {
      key: "actions",
      header: "",
      className: "text-right",
      render: (row) => {
        const readiness = filmReadiness(row, pipelineByRef.get(row.title_id));
        const level = rightsLevel(row.rights?.level);
        return (
          <span className="inline-flex items-center justify-end gap-1.5">
            {operator ? (
              <EnqueueButton
                kind="film"
                refId={row.title_id}
                disabled={level === "block" || row.source === null}
                title={
                  level === "block"
                    ? "Произведение не проходит проверку прав"
                    : row.source === null
                      ? "Нет исходника — Kino-trends не примет"
                      : readiness.ready
                        ? undefined
                        : readiness.reasons.join("; ")
                }
              />
            ) : null}
            <Link
              href={routeHref("/trends/film", { id: row.title_id })}
              className="text-xs text-accent hover:underline"
              aria-label={`Разбор: ${row.label ?? row.title_id}`}
            >
              Разбор
            </Link>
          </span>
        );
      },
    },
  ];

  const data = films.data;
  const readyInPage = (data?.items ?? []).filter((row) => filmReadiness(row).ready).length;

  return (
    <div className="flex flex-col gap-3" data-testid="trends-films">
      <div className="flex flex-wrap items-center gap-2">
        <PillToggle
          label="Тип произведения"
          options={[...FILM_KIND_OPTIONS]}
          value={kind}
          onChange={(value) => resetAnd(() => setKind(value))}
        />
        <Select
          aria-label="Период"
          value={period}
          onChange={(event) => resetAnd(() => setPeriod(event.target.value as TrendsFilmPeriod))}
          className="w-32"
        >
          {FILM_PERIOD_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Сортировка"
          value={sort}
          onChange={(event) => resetAnd(() => setSort(event.target.value as TrendsFilmSort))}
          className="w-56"
        >
          {FILM_SORT_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </Select>
        <div className="relative min-w-48 flex-1 md:max-w-xs">
          <Search
            className="pointer-events-none absolute top-2 left-2.5 h-3.5 w-3.5 text-fg-muted"
            aria-hidden="true"
          />
          <Input
            type="search"
            aria-label="Поиск по названию"
            placeholder="Название фильма или сериала"
            value={search}
            onChange={(event) => resetAnd(() => setSearch(event.target.value))}
            className="pl-8"
            data-testid="trends-films-search"
          />
        </div>
        <label className="flex items-center gap-2 text-xs text-fg">
          <Switch
            checked={readyOnly}
            onChange={(next) => resetAnd(() => setReadyOnly(next))}
            label="Только готовые к нарезке"
            id="trends-films-ready"
          />
          только готовые к нарезке
        </label>
        {operator ? <FindTopSources enabled={enabled} /> : null}
      </div>

      {data ? (
        <p className="tabular flex flex-wrap gap-x-3 gap-y-1 text-xs text-fg-muted">
          <span>
            {readyOnly ? "готовых к нарезке" : "всего"}{" "}
            <b className="font-medium text-fg">{formatNumber(data.total)}</b>
          </span>
          {!readyOnly ? (
            <span>
              на странице готовых <b className="font-medium text-positive">{formatNumber(readyInPage)}</b>
            </span>
          ) : null}
          <span className="text-fg-muted">готово = права проходят, исходник подтверждён, не в конвейере</span>
        </p>
      ) : null}

      {films.isError ? (
        <SectionError error={films.error} onRetry={() => void films.refetch()} title="Список кино не загрузился" />
      ) : (
        <>
          <DataTable
            columns={columns}
            rows={data?.items ?? []}
            rowKey={(row) => row.title_id}
            isLoading={films.isPending}
            emptyTitle={readyOnly ? "Готовых к нарезке произведений нет" : "Произведений с такими фильтрами нет"}
            emptyDescription={
              readyOnly
                ? "Снимите тумблер, чтобы увидеть всё, что режут: у большинства нет исходника — «Найти исходники для топа» это лечит."
                : "Расширьте период или смените тип произведения."
            }
            minWidth={1180}
            caption="Фильмы и сериалы, которые режут в шортсы, по данным Kino-trends"
          />
          {data && data.total > PAGE_SIZE ? (
            <Pagination total={data.total} limit={PAGE_SIZE} offset={offset} onOffsetChange={setOffset} />
          ) : null}
        </>
      )}
      {!films.isPending && !films.isError && !data?.items.length && !readyOnly ? (
        <p className="flex items-center gap-2 text-xs text-fg-muted">
          <Clapperboard className="h-3.5 w-3.5" aria-hidden="true" />
          Статистика по кино строится после прогона сбора в Kino-trends.
        </p>
      ) : null}
    </div>
  );
}
