"use client";

import { ExternalLink, Search } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { SectionError } from "@/components/common/section-error";
import {
  Badge,
  DataTable,
  type DataTableColumn,
  Input,
  Pagination,
  PillToggle,
  Select,
  StatusBadge,
} from "@/components/ui";
import { cn } from "@/lib/cn";
import { formatDay, formatNumber } from "@/lib/format";
import { routeHref } from "@/lib/nav";
import {
  type TrendsVideo,
  type TrendsVideosFilters,
  type TrendsVideoSort,
  useTrendsVideos,
} from "@/lib/queries/trends";

import { EnqueueButton } from "./enqueue-button";
import {
  CLEAN_LEVEL_META,
  cleanLevel,
  fitTone,
  formatDuration,
  formatFit,
  LEVEL_FILTER_OPTIONS,
  type LevelFilter,
  licenseMeta,
  pickNumber,
  pickString,
  SHORT_OPTIONS,
  type ShortFilter,
  SORT_OPTIONS,
} from "./labels";

const PAGE_SIZE = 50;
/** Порог «под нашу аудиторию» — как в интерфейсе Kino-trends. */
const FIT_THRESHOLD = 0.6;

/** Значение с задержкой: поиск не дёргает прокси на каждую букву. */
function useDebouncedValue<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return debounced;
}

/**
 * Вкладка «Видео»: чарты YouTube по странам СНГ и ролики Creative Commons, каждый с уровнем чистоты
 * (лицензия, заявки Content ID, нейросеть по тексту и кадрам) и совпадением с нашей аудиторией.
 * Фильтры повторяют `/api/videos` Kino-trends; миниатюры — обложки из данных ответа.
 */
export function VideosPanel({ operator, enabled }: { operator: boolean; enabled: boolean }) {
  const [level, setLevel] = useState<LevelFilter>("all");
  const [short, setShort] = useState<ShortFilter>("");
  const [sort, setSort] = useState<TrendsVideoSort>("score");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const q = useDebouncedValue(search.trim(), 350);

  const filters: TrendsVideosFilters = {
    level: level === "clean" || level === "check" || level === "no" ? level : null,
    fit: level === "fit" ? FIT_THRESHOLD : null,
    pick: level === "pick",
    short: short === "1" ? 1 : short === "0" ? 0 : null,
    sort,
    q: q || null,
    limit: PAGE_SIZE,
    offset,
  };
  const videos = useTrendsVideos(filters, { enabled });

  function resetAnd(update: () => void) {
    update();
    setOffset(0);
  }

  const columns: Array<DataTableColumn<TrendsVideo>> = [
    {
      key: "video",
      header: "Ролик",
      className: "min-w-80",
      render: (video) => <VideoCell video={video} />,
    },
    {
      key: "views",
      header: "Просмотры",
      className: "text-right",
      render: (video) => (
        <span className="flex flex-col items-end">
          <span className="tabular font-mono text-xs" title={formatNumber(video.views)}>
            {formatNumber(video.views)}
          </span>
          <span className="tabular text-2xs text-fg-muted">
            {video.growth !== null && video.growth !== undefined
              ? `+${formatNumber(video.growth)}/дн`
              : video.vpd !== null && video.vpd !== undefined
                ? `${formatNumber(video.vpd)}/дн`
                : ""}
          </span>
        </span>
      ),
    },
    {
      key: "fit",
      header: "Аудитория",
      className: "text-right",
      render: (video) => {
        const analysis = video.analysis as Record<string, unknown> | null;
        const fit = pickNumber(analysis, "audience_fit");
        return (
          <span
            className={cn("tabular font-mono text-xs", fitTone(fit))}
            title={pickString(analysis, "audience_why") ?? undefined}
          >
            {formatFit(fit)}
          </span>
        );
      },
    },
    {
      key: "level",
      header: "Чистота",
      render: (video) => {
        const license = licenseMeta(video);
        return (
          <span className="flex flex-wrap items-center gap-1">
            <StatusBadge status={cleanLevel(video.level)} map={CLEAN_LEVEL_META} />
            <Badge variant={license.variant} title={license.title}>
              {license.label}
            </Badge>
            {video.made_for_kids ? <Badge variant="warning">детское</Badge> : null}
            {video.blocked_ru ? <Badge variant="warning">закрыт для РФ</Badge> : null}
            {video.pick ? <Badge variant="accent">{video.pick_auto ? "подборка (авто)" : "подборка"}</Badge> : null}
            {video.used_at ? <Badge variant="accent">залито</Badge> : null}
          </span>
        );
      },
    },
    {
      key: "score",
      header: "Балл",
      className: "text-right",
      render: (video) => (
        <span className="tabular font-mono text-xs" title={(video.reasons ?? []).join("\n") || undefined}>
          {video.score !== null && video.score !== undefined ? formatNumber(video.score) : "—"}
        </span>
      ),
    },
    {
      key: "actions",
      header: "",
      className: "text-right",
      render: (video) => (
        <span className="inline-flex items-center justify-end gap-1.5">
          {operator ? (
            <EnqueueButton kind="video" refId={video.video_id} disabled={cleanLevel(video.level) === "no"} />
          ) : null}
          <Link
            href={routeHref("/trends/video", { id: video.video_id })}
            className="text-xs text-accent hover:underline"
            aria-label={`Разбор: ${video.title ?? video.video_id}`}
          >
            Разбор
          </Link>
          {video.url ? (
            <a
              href={video.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex h-6 w-6 items-center justify-center rounded-md text-fg-muted hover:bg-surface-2 hover:text-fg"
              title="Открыть на площадке"
              aria-label="Открыть на площадке"
            >
              <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
            </a>
          ) : null}
        </span>
      ),
    },
  ];

  const data = videos.data;
  const levels = data?.levels ?? {};

  return (
    <div className="flex flex-col gap-3" data-testid="trends-videos">
      <div className="flex flex-wrap items-center gap-2">
        <PillToggle
          label="Уровень чистоты"
          options={[...LEVEL_FILTER_OPTIONS]}
          value={level}
          onChange={(value) => resetAnd(() => setLevel(value))}
        />
        <Select
          aria-label="Шортсы или длинные"
          value={short}
          onChange={(event) => resetAnd(() => setShort(event.target.value as ShortFilter))}
          className="w-44"
        >
          {SHORT_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Сортировка"
          value={sort}
          onChange={(event) => resetAnd(() => setSort(event.target.value as TrendsVideoSort))}
          className="w-60"
        >
          {SORT_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </Select>
        <div className="relative min-w-56 flex-1 md:max-w-sm">
          <Search
            className="pointer-events-none absolute top-2 left-2.5 h-3.5 w-3.5 text-fg-muted"
            aria-hidden="true"
          />
          <Input
            type="search"
            aria-label="Поиск по видео"
            placeholder="Название, канал, описание"
            value={search}
            onChange={(event) => resetAnd(() => setSearch(event.target.value))}
            className="pl-8"
            data-testid="trends-videos-search"
          />
        </div>
      </div>

      {data ? (
        <p className="tabular flex flex-wrap gap-x-3 gap-y-1 text-xs text-fg-muted">
          <span>
            всего <b className="font-medium text-fg">{formatNumber(data.total)}</b>
          </span>
          <span>
            чистых <b className="font-medium text-positive">{formatNumber(levels.clean ?? 0)}</b>
          </span>
          <span>
            проверить <b className="font-medium text-warning">{formatNumber(levels.check ?? 0)}</b>
          </span>
          <span>
            не брать <b className="font-medium text-fg">{formatNumber(levels.no ?? 0)}</b>
          </span>
          <span>
            не проверено <b className="font-medium text-fg">{formatNumber(levels.unknown ?? 0)}</b>
          </span>
          <span>
            в подборке <b className="font-medium text-fg">{formatNumber(data.picks)}</b>
          </span>
          <span>
            шортсов <b className="font-medium text-fg">{formatNumber(data.shorts)}</b>
          </span>
        </p>
      ) : null}

      {videos.isError ? (
        <SectionError error={videos.error} onRetry={() => void videos.refetch()} title="Чарты не загрузились" />
      ) : (
        <>
          <DataTable
            columns={columns}
            rows={data?.items ?? []}
            rowKey={(video) => video.video_id}
            isLoading={videos.isPending}
            emptyTitle="Роликов с такими фильтрами нет"
            emptyDescription="В Kino-trends нажмите «обновить чарты и анализ» или дождитесь прогона сбора."
            minWidth={960}
            caption="Популярные видео СНГ по данным Kino-trends"
          />
          {data && data.total > PAGE_SIZE ? (
            <Pagination total={data.total} limit={PAGE_SIZE} offset={offset} onOffsetChange={setOffset} />
          ) : null}
        </>
      )}
    </div>
  );
}

function VideoCell({ video }: { video: TrendsVideo }) {
  const analysis = video.analysis as Record<string, unknown> | null;
  const topic = [pickString(analysis, "topic"), pickString(analysis, "format")].filter(Boolean).join(" · ");
  const metaLine = [
    video.channel_name,
    video.channel_subs ? `${formatNumber(video.channel_subs)} подп.` : null,
    video.published_at ? formatDay(video.published_at.slice(0, 10)) : null,
    video.duration_s !== null && video.duration_s !== undefined
      ? `${formatDuration(video.duration_s)}${video.is_short === 1 ? " · шортс" : video.is_short === 0 ? " · длинное" : ""}`
      : null,
    video.category,
    video.lang,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <span className="flex items-center gap-2.5">
      <span className="relative block h-11 w-20 shrink-0 overflow-hidden rounded-sm bg-video" aria-hidden="true">
        {video.thumb ? (
          // eslint-disable-next-line @next/next/no-img-element -- обложка из данных Kino-trends, без оптимизатора
          <img src={video.thumb} alt="" loading="lazy" className="h-full w-full object-cover" />
        ) : null}
      </span>
      <span className="flex min-w-0 flex-col">
        <Link
          href={routeHref("/trends/video", { id: video.video_id })}
          className="truncate font-medium text-fg hover:underline"
          title={video.title ?? video.video_id}
          data-testid="trends-video-link"
        >
          {video.title ?? video.video_id}
        </Link>
        <span className="truncate text-2xs text-fg-muted">{metaLine}</span>
        {topic ? <span className="truncate text-2xs text-fg-muted">{topic}</span> : null}
      </span>
    </span>
  );
}
