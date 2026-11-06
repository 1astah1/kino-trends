"use client";

import { CalendarDays, Clapperboard, ExternalLink, Film, ListVideo, Sparkles } from "lucide-react";
import Link from "next/link";

import { SectionError } from "@/components/common/section-error";
import {
  Badge,
  EmptyState,
  SectionCard,
  Skeleton,
  SkeletonStatGrid,
  StatTile,
  StatTileGrid,
  StatusBadge,
} from "@/components/ui";
import { cn } from "@/lib/cn";
import { formatAgo, formatDay, formatNumber } from "@/lib/format";
import { useNow } from "@/lib/hooks/use-now";
import { routeHref } from "@/lib/nav";
import { type TrendsFilmSlot, type TrendsPickVideo, useTrendsToday } from "@/lib/queries/trends";

import { EnqueueButton } from "./enqueue-button";
import {
  CLEAN_LEVEL_META,
  cleanLevel,
  fitTone,
  formatDuration,
  formatFit,
  licenseMeta,
  pickNumber,
  pickString,
  RIGHTS_LEVEL_META,
  rightsLevel,
  SLOT_STATUS_META,
  slotStatus,
} from "./labels";

/**
 * Вкладка «Сегодня»: что Kino-trends предлагает отдать в нарезку и на залив — кино по календарю
 * (с проверкой прав) и чистые ролики под нашу аудиторию. Кнопка «В конвейер» ставит задание в очередь
 * Kino-trends, дальше оно само появится в «Производстве» как источник.
 */
export function TodayPanel({ operator, enabled }: { operator: boolean; enabled: boolean }) {
  const today = useTrendsToday({ enabled });
  const now = useNow(30_000);

  if (today.isError) {
    return (
      <SectionError error={today.error} onRetry={() => void today.refetch()} title="Подборка дня не загрузилась" />
    );
  }
  if (!today.data) {
    return (
      <div className="flex flex-col gap-4" aria-busy="true">
        <SkeletonStatGrid />
        <div className="grid gap-4 lg:grid-cols-2">
          <Skeleton className="h-64" />
          <Skeleton className="h-64" />
        </div>
      </div>
    );
  }

  const data = today.data;
  const counts = data.counts;
  const running = Boolean(data.run && (data.run as Record<string, unknown>).running);
  const phase = pickString(data.run as Record<string, unknown> | null, "phase");
  const lastRunAt = pickString(data.last_run as Record<string, unknown> | null, "started_at");
  const slots = data.schedule.today;
  const candidates = data.candidates ?? [];
  const tomorrow = data.schedule.tomorrow.filter((slot) => slot.title_id);

  return (
    <div className="flex flex-col gap-4" data-testid="trends-today">
      <StatTileGrid>
        <StatTile
          label="Кино сегодня"
          icon={Film}
          value={
            <span>
              {formatNumber(counts.film_done)}{" "}
              <span className="text-sm font-normal text-fg-muted">из {formatNumber(counts.film_total)}</span>
            </span>
          }
          hint={`календарь на ${formatDay(data.today)}`}
        />
        <StatTile
          label="В подборке"
          icon={Sparkles}
          value={formatNumber(counts.picks)}
          hint={`залито всего ${formatNumber(counts.used)}`}
        />
        <StatTile
          label="Чистых роликов"
          icon={ListVideo}
          value={formatNumber(counts.clean)}
          badge={counts.check ? <Badge variant="warning">проверить {formatNumber(counts.check)}</Badge> : undefined}
          hint={`в чартах ${formatNumber(counts.videos)}`}
        />
        <StatTile
          label="Сбор"
          icon={CalendarDays}
          value={
            running ? <span className="text-machine">идёт</span> : lastRunAt ? formatAgo(lastRunAt, now) : "не было"
          }
          hint={running ? phase || "сбор чартов" : "последний прогон Kino-trends"}
        />
      </StatTileGrid>

      <div className="grid gap-4 lg:grid-cols-2">
        <SectionCard title="Кино: режем сегодня">
          {slots.length === 0 ? (
            <EmptyState
              icon={Clapperboard}
              title="На сегодня слотов нет"
              description="Календарь строится после прогона сбора в Kino-trends; добавить руками можно там, в «Кино → Что резать»."
              compact
            />
          ) : (
            slots.map((slot) => <FilmCard key={slot.id} slot={slot} operator={operator} />)
          )}
          {tomorrow.length ? (
            <div className="flex flex-col gap-1.5 border-t border-border pt-3">
              <h3 className="font-mono text-2xs tracking-wider text-fg-muted uppercase">Завтра</h3>
              <ul className="flex flex-col gap-1">
                {tomorrow.map((slot) => (
                  <li key={slot.id} className="flex flex-wrap items-center gap-2 text-sm">
                    {slot.title_id ? (
                      <Link
                        href={routeHref("/trends/film", { id: slot.title_id })}
                        className="truncate font-medium hover:underline"
                      >
                        {slot.label ?? slot.title_id}
                      </Link>
                    ) : (
                      <span className="truncate font-medium">{slot.label ?? slot.title_id}</span>
                    )}
                    {slot.year ? <span className="text-fg-muted">({slot.year})</span> : null}
                    {slot.kind_ru ? <span className="text-xs text-fg-muted">{slot.kind_ru}</span> : null}
                    <StatusBadge status={rightsLevel(slot.rights?.level)} map={RIGHTS_LEVEL_META} />
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </SectionCard>

        <SectionCard title="Видео: подборка под перезалив">
          {data.picks.length === 0 ? (
            <EmptyState
              icon={ListVideo}
              title="Подборка пуста"
              description={
                counts.check
                  ? "Ниже — ролики под аудиторию, которые ещё не подтверждены: посмотрите кадры в Kino-trends и возьмите подходящие."
                  : counts.videos
                    ? "Чистых роликов с баллом от порога пока нет; разбор продолжается с каждым прогоном."
                    : "В Kino-trends нужен ключ YouTube Data API, затем «обновить видео»."
              }
              compact
            />
          ) : (
            data.picks.map((video) => <VideoCard key={video.video_id} video={video} operator={operator} />)
          )}
          {candidates.length ? (
            <div className="flex flex-col gap-3 border-t border-border pt-3">
              <h3 className="font-mono text-2xs tracking-wider text-fg-muted uppercase">
                На проверку: под аудиторию, но не подтверждены
              </h3>
              {candidates.map((video) => (
                <VideoCard key={video.video_id} video={video} operator={operator} candidate />
              ))}
            </div>
          ) : null}
        </SectionCard>
      </div>
    </div>
  );
}

function FilmCard({ slot, operator }: { slot: TrendsFilmSlot; operator: boolean }) {
  const plan = slot.plan as Record<string, unknown> | null;
  const source = slot.source as Record<string, unknown> | null;
  const sourceUrl = pickString(source, "url");
  const sourceChannel = pickString(source, "channel");
  const sourceDuration = pickNumber(source, "duration_s");
  const sourceLanguage = pickString(source, "language");
  const rights = slot.rights;
  const rightsLvl = rightsLevel(rights?.level);
  const rightsReasons = rights?.reasons ?? [];
  const status = slotStatus(slot.status);
  const planLine = [pickString(plan, "duration"), pickString(plan, "best_time"), pickString(plan, "hook")]
    .filter(Boolean)
    .join(" · ");
  const sourceLine = [sourceChannel, sourceDuration !== null ? formatDuration(sourceDuration) : null, sourceLanguage]
    .filter(Boolean)
    .join(" · ");

  return (
    <article
      className={cn("flex flex-col gap-2 rounded-md border border-border p-3", status === "done" && "opacity-70")}
      data-testid="trends-film-card"
    >
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-medium text-fg">
          {slot.title_id ? (
            <Link
              href={routeHref("/trends/film", { id: slot.title_id })}
              className="hover:underline"
              data-testid="trends-film-link"
            >
              {slot.label ?? slot.title_id}
            </Link>
          ) : (
            (slot.label ?? "Без названия")
          )}
          {slot.year ? <span className="ml-1 font-normal text-fg-muted">({slot.year})</span> : null}
        </h3>
        {slot.kind_ru ? <Badge variant="neutral">{slot.kind_ru}</Badge> : null}
        <StatusBadge status={rightsLvl} map={RIGHTS_LEVEL_META} />
        {status !== "planned" ? <StatusBadge status={status} map={SLOT_STATUS_META} /> : null}
      </div>
      {slot.reason ? <p className="text-xs text-fg-muted">{slot.reason}</p> : null}
      {rights && rightsLvl !== "ok" && rightsReasons.length ? (
        <p className={cn("text-xs", rightsLvl === "block" ? "text-negative" : "text-warning")}>
          {rightsReasons.slice(0, 2).join("; ")}
          {rights.clip_max_s ? ` · клип до ${rights.clip_max_s} с` : ""}
        </p>
      ) : rights?.clip_max_s ? (
        <p className="text-xs text-fg-muted">
          клип до {rights.clip_max_s} с{rights.holder ? ` · ${rights.holder}` : ""}
        </p>
      ) : null}
      {planLine ? <p className="text-xs text-fg-muted">План: {planLine}</p> : null}
      {slot.explain ? (
        <p className="line-clamp-3 text-xs text-fg" title={slot.explain}>
          {slot.explain}
        </p>
      ) : null}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex min-w-0 flex-wrap items-center gap-2 text-xs text-fg-muted">
          {sourceUrl ? (
            <a
              href={sourceUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-accent hover:underline"
            >
              исходник
              <ExternalLink className="h-3 w-3" aria-hidden="true" />
            </a>
          ) : (
            <span>исходника пока нет</span>
          )}
          {sourceLine ? <span className="truncate">{sourceLine}</span> : null}
          {slot.kinopoisk_url ? (
            <a
              href={slot.kinopoisk_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-accent hover:underline"
            >
              Кинопоиск
            </a>
          ) : null}
          {slot.title_id ? (
            <Link href={routeHref("/trends/film", { id: slot.title_id })} className="text-accent hover:underline">
              Разбор
            </Link>
          ) : null}
        </span>
        {operator && slot.title_id ? (
          <EnqueueButton
            kind="film"
            refId={slot.title_id}
            disabled={!sourceUrl || rightsLvl === "block"}
            title={
              !sourceUrl
                ? "У произведения ещё нет исходника — Kino-trends не примет"
                : rightsLvl === "block"
                  ? "Произведение не проходит проверку прав"
                  : undefined
            }
          />
        ) : null}
      </div>
    </article>
  );
}

function VideoCard({ video, operator, candidate }: { video: TrendsPickVideo; operator: boolean; candidate?: boolean }) {
  const level = cleanLevel(video.level);
  const license = licenseMeta(video);
  const freeZones = video.free_zones ?? [];
  const metaLine = [
    video.channel,
    video.channel_subs ? `${formatNumber(video.channel_subs)} подп.` : null,
    video.published_at ? formatDay(video.published_at.slice(0, 10)) : null,
    video.duration_s !== null && video.duration_s !== undefined ? formatDuration(video.duration_s) : null,
    video.is_short === 1 ? "шортс" : video.is_short === 0 ? "длинное" : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <article
      className={cn("flex gap-3 rounded-md border border-border p-3", candidate && "border-dashed")}
      data-testid="trends-video-card"
    >
      <a
        href={video.source_url ?? undefined}
        target="_blank"
        rel="noopener noreferrer"
        className="relative block h-16 w-28 shrink-0 overflow-hidden rounded-sm bg-video"
        aria-label="Открыть ролик на площадке"
      >
        {video.thumb ? (
          // eslint-disable-next-line @next/next/no-img-element -- обложка из данных Kino-trends, без оптимизатора
          <img src={video.thumb} alt="" loading="lazy" className="h-full w-full object-cover" />
        ) : null}
      </a>
      <div className="flex min-w-0 flex-1 flex-col gap-1.5">
        <div className="flex flex-wrap items-center gap-2">
          <Link
            href={routeHref("/trends/video", { id: video.video_id })}
            className="min-w-0 truncate text-sm font-medium text-fg hover:underline"
            title={video.title ?? video.video_id}
            data-testid="trends-video-link"
          >
            {video.title ?? video.video_id}
          </Link>
          <StatusBadge status={level} map={CLEAN_LEVEL_META} />
          <Badge variant={license.variant} title={license.title}>
            {license.label}
          </Badge>
          {video.used_at ? <Badge variant="accent">залито</Badge> : null}
        </div>
        {metaLine ? <p className="truncate text-xs text-fg-muted">{metaLine}</p> : null}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
          <span className="tabular">
            <span className="text-fg-muted">просмотры </span>
            <span className="font-medium text-fg">{formatNumber(video.views)}</span>
            {video.growth_per_day ? (
              <span className="text-fg-muted"> · +{formatNumber(video.growth_per_day)}/дн</span>
            ) : null}
          </span>
          <span className="tabular">
            <span className="text-fg-muted">аудитория </span>
            <span className={cn("font-medium", fitTone(video.audience_fit))} title={video.audience_why ?? undefined}>
              {formatFit(video.audience_fit)}
            </span>
          </span>
          {video.score !== null && video.score !== undefined ? (
            <span className="tabular">
              <span className="text-fg-muted">балл </span>
              <span className="font-medium text-fg">{formatNumber(video.score)}</span>
            </span>
          ) : null}
          {video.topic ? (
            <span className="truncate text-fg-muted">{[video.topic, video.format].filter(Boolean).join(" · ")}</span>
          ) : null}
        </div>
        {video.banner_advice ? (
          <p className="line-clamp-2 text-xs text-fg-muted" title={video.banner_advice}>
            Баннер: {video.banner_advice}
            {freeZones.length ? ` (свободно: ${freeZones.join(", ")})` : ""}
          </p>
        ) : null}
        <div className="flex items-center justify-end gap-3">
          <Link
            href={routeHref("/trends/video", { id: video.video_id })}
            className="text-xs text-accent hover:underline"
          >
            Разбор
          </Link>
          {operator ? <EnqueueButton kind="video" refId={video.video_id} disabled={level === "no"} /> : null}
        </div>
      </div>
    </article>
  );
}
