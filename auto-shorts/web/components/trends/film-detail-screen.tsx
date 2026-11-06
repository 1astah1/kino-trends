"use client";

import { ArrowLeft, Clapperboard, ExternalLink } from "lucide-react";
import Link from "next/link";
import { parseAsStringLiteral, useQueryState } from "nuqs";
import { toast } from "sonner";

import { useCurrentUser } from "@/components/auth/auth-context";
import { PageHeader } from "@/components/common/page-header";
import { SectionError } from "@/components/common/section-error";
import {
  Badge,
  Button,
  buttonVariants,
  Callout,
  EmptyState,
  SectionCard,
  Select,
  Skeleton,
  SkeletonStatGrid,
  StatTile,
  StatTileGrid,
} from "@/components/ui";
import { isApiError } from "@/lib/api/errors";
import { canOperate } from "@/lib/auth";
import { cn } from "@/lib/cn";
import { formatDateTime, formatDay, formatNumber } from "@/lib/format";
import { routeHref } from "@/lib/nav";
import { type TrendsFilmDetail, type TrendsFilmPeriod, useTrendsFilm } from "@/lib/queries/trends";

import { EnqueueButton } from "./enqueue-button";
import { FilmRightsCard } from "./film-rights-card";
import { FilmSourcesCard } from "./film-sources-card";
import { Poster } from "./films-panel";
import {
  FILE_STATUS_META,
  fileStatus,
  FILM_PERIOD_OPTIONS,
  filmReadiness,
  formatDuration,
  rightsLevel,
  sourcePlatform,
} from "./labels";
import { activePipelineItem, PipelineRefCard, usePipelineItemsFor } from "./pipeline-ref-card";
import { LazyTrendSeriesChart } from "./trend-series-chart-lazy";
import { KeyValueList } from "./video-analysis";

const TITLE_ID_RE = /^[A-Za-z0-9_-]{1,40}$/;
const PERIODS = ["7", "30", "90", "all"] as const;

function plural(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return few;
  return many;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function str(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

/** Горизонтальные полоски: каналы, сезоны, длительности — доля от максимума. */
function BarList({
  rows,
  format = (v: number) => formatNumber(v),
}: {
  rows: Array<{ label: string; value: number; sub?: string }>;
  format?: (v: number) => string;
}) {
  const max = Math.max(1, ...rows.map((row) => row.value));
  return (
    <ul className="flex flex-col gap-1.5">
      {rows.map((row) => (
        <li key={row.label} className="flex flex-col gap-0.5 text-xs">
          <span className="flex items-center justify-between gap-2">
            <span className="truncate text-fg" title={row.label}>
              {row.label}
              {row.sub ? <span className="text-fg-muted"> · {row.sub}</span> : null}
            </span>
            <span className="tabular shrink-0 font-mono text-fg-muted">{format(row.value)}</span>
          </span>
          <span className="block h-1.5 w-full overflow-hidden rounded-full bg-surface-2" aria-hidden="true">
            <span
              className="block h-full rounded-full bg-chart-1"
              style={{ width: `${Math.max(2, (row.value / max) * 100)}%` }}
            />
          </span>
        </li>
      ))}
    </ul>
  );
}

/** «Почему заходит»: тезисы Kino-trends, текст нейросети, интерес вне шортсов (Википедия, Google Trends). */
function WhyCard({ film }: { film: TrendsFilmDetail }) {
  const why = film.why ?? [];
  const interest = (film.interest ?? null) as Record<string, unknown> | null;
  const lines = Array.isArray(interest?.lines)
    ? (interest?.lines as unknown[]).filter((line): line is string => typeof line === "string")
    : [];
  const opportunity = typeof film.opportunity === "number" ? film.opportunity : null;
  return (
    <SectionCard
      title="Почему заходит"
      bodyClassName="gap-3"
      actions={
        opportunity !== null ? (
          <Badge
            variant="accent"
            className="font-mono"
            title="Балл возможности: балл темы с поправкой на конкуренцию и интерес"
          >
            возможность {formatNumber(opportunity)}
          </Badge>
        ) : film.opportunity === true ? (
          <Badge variant="positive" outline>
            мало конкурентов
          </Badge>
        ) : undefined
      }
    >
      {why.length ? (
        <ul className="flex flex-col gap-1 text-sm text-fg">
          {why.map((line) => (
            <li key={line} className="flex gap-2">
              <span aria-hidden="true" className="mt-2 h-1 w-1 shrink-0 rounded-full bg-fg-muted" />
              <span>{line}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {film.explain ? (
        <p
          className="text-sm text-fg-muted"
          title={film.explain_at ? `нейросеть, ${formatDateTime(film.explain_at, { short: true })}` : undefined}
        >
          {film.explain}
        </p>
      ) : null}
      {lines.length ? (
        <div className="flex flex-col gap-1 border-t border-border pt-2">
          <span className="font-mono text-2xs tracking-wider text-fg-muted uppercase">Интерес вне шортсов</span>
          {lines.map((line) => (
            <span key={line} className="text-xs text-fg-muted">
              {line}
            </span>
          ))}
        </div>
      ) : null}
      {!why.length && !film.explain && !lines.length ? (
        <p className="text-sm text-fg-muted">Kino-trends пока не объяснил тему: нужны опознанные нарезки за период.</p>
      ) : null}
    </SectionCard>
  );
}

/** «Как резать»: блок нарезчика и план — исходник, прямой mp4, длина клипа, подсказка, хук, время, хэштеги. */
function CutterCard({ film }: { film: TrendsFilmDetail }) {
  const cutter = film.cutter ?? null;
  const plan = film.plan ?? null;
  const source = film.source ?? null;
  const direct = cutter?.direct_file_url ?? null;
  const sourceUrl = cutter?.source_url ?? source?.url ?? null;
  const fileState = fileStatus(source?.file_status, direct);
  const hashtags = plan?.hashtags ?? [];
  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      toast.success("Скопировано");
    } catch {
      toast.error("Не удалось скопировать", { description: text });
    }
  }
  return (
    <SectionCard title="Как резать" bodyClassName="gap-3">
      {sourceUrl ? (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Badge variant="neutral" outline className="font-mono">
            {sourcePlatform(sourceUrl)}
          </Badge>
          <a
            href={sourceUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex min-w-0 items-center gap-1 truncate font-medium text-fg hover:underline"
            title={source?.label ?? sourceUrl}
          >
            <span className="truncate">{source?.label ?? "исходник"}</span>
            <ExternalLink className="h-3 w-3 shrink-0 text-fg-muted" aria-hidden="true" />
          </a>
          {source?.channel ? <span className="text-xs text-fg-muted">{source.channel}</span> : null}
          {source?.duration_s ? (
            <span className="text-xs text-fg-muted">{formatDuration(source.duration_s)}</span>
          ) : null}
          {source?.language ? <span className="text-xs text-fg-muted">{source.language}</span> : null}
        </div>
      ) : (
        <p className="text-sm text-negative">
          Исходника нет — резать нечего. Найдите полный фильм или серии в блоке «Исходники».
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-surface-2/40 px-3 py-2 text-xs">
        <span className="font-medium text-fg">ссылка для нарезчика</span>
        <Badge variant={FILE_STATUS_META[fileState].variant} outline={FILE_STATUS_META[fileState].outline}>
          {direct ? "прямой mp4 из хранилища" : FILE_STATUS_META[fileState].label}
        </Badge>
        {direct || sourceUrl ? (
          <>
            <code className="min-w-0 flex-1 truncate font-mono text-fg-muted" title={direct ?? sourceUrl ?? ""}>
              {direct ?? sourceUrl}
            </code>
            <Button variant="ghost" size="xs" onClick={() => void copy(direct ?? sourceUrl ?? "")}>
              скопировать
            </Button>
          </>
        ) : null}
      </div>
      {cutter?.alt_url ? (
        <p className="text-xs text-fg-muted">
          запасной исходник:{" "}
          <a href={cutter.alt_url} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">
            {sourcePlatform(cutter.alt_url)}
          </a>
        </p>
      ) : null}
      <KeyValueList
        items={[
          {
            label: "длина клипа",
            value:
              cutter?.clip_min_s || cutter?.clip_max_s
                ? `${cutter?.clip_min_s ?? "?"}–${cutter?.clip_max_s ?? "?"} с${plan?.duration ? ` (план: ${plan.duration})` : ""}`
                : (plan?.duration ?? null),
          },
          { label: "хук", value: plan?.hook ?? null },
          { label: "лучшее время", value: plan?.best_time ?? null },
          {
            label: "хэштеги",
            value: hashtags.length ? (
              <span className="flex flex-wrap gap-1">
                {hashtags.map((tag) => (
                  <Badge key={tag} variant="neutral" outline>
                    #{tag}
                  </Badge>
                ))}
              </span>
            ) : null,
          },
          { label: "права", value: cutter?.rights_note ?? null },
        ]}
      />
      {cutter?.hint ? (
        <div className="flex flex-col gap-1 border-t border-border pt-2">
          <span className="flex items-center justify-between gap-2">
            <span className="font-mono text-2xs tracking-wider text-fg-muted uppercase">Что искать в исходнике</span>
            <Button variant="ghost" size="xs" onClick={() => void copy(cutter.hint ?? "")}>
              скопировать подсказку
            </Button>
          </span>
          <p className="text-sm whitespace-pre-wrap text-fg">{cutter.hint}</p>
        </div>
      ) : null}
    </SectionCard>
  );
}

/** «Сцены и хуки»: что режут другие и какие заголовки заходят (нейросеть по чужим нарезкам). */
function ScenesCard({ film }: { film: TrendsFilmDetail }) {
  const scenes = film.scenes ?? [];
  const hooks = film.hooks ?? [];
  const advice = film.advice ?? [];
  if (!scenes.length && !hooks.length && !advice.length) return null;
  return (
    <SectionCard
      title="Сцены и хуки"
      bodyClassName="gap-3"
      actions={
        film.insights_at ? (
          <span className="text-2xs text-fg-muted">разбор {formatDateTime(film.insights_at, { short: true })}</span>
        ) : undefined
      }
    >
      {scenes.length ? (
        <ol className="flex flex-col gap-2">
          {scenes.map((scene, index) => {
            const s = scene as Record<string, unknown>;
            const quotes = Array.isArray(s.quotes)
              ? (s.quotes as unknown[]).filter((q): q is string => typeof q === "string")
              : [];
            const n = num(s.n);
            const views = num(s.views);
            return (
              <li key={`${str(s.name) ?? "scene"}-${index}`} className="flex flex-col gap-0.5 text-sm">
                <span className="flex flex-wrap items-center gap-2">
                  <span className="font-medium text-fg">{str(s.name) ?? `Сцена ${index + 1}`}</span>
                  {str(s.where) ? (
                    <Badge variant="neutral" outline>
                      {str(s.where)}
                    </Badge>
                  ) : null}
                  {n !== null ? (
                    <span className="tabular text-xs text-fg-muted">
                      {formatNumber(n)} {plural(n, "ролик", "ролика", "роликов")}
                      {views !== null ? ` · ${formatNumber(views)} просм.` : ""}
                    </span>
                  ) : null}
                </span>
                {str(s.what_to_cut) ? <span className="text-fg-muted">{str(s.what_to_cut)}</span> : null}
                {quotes.length ? (
                  <span className="text-xs text-fg-muted">реплики: {quotes.map((q) => `«${q}»`).join(", ")}</span>
                ) : null}
              </li>
            );
          })}
        </ol>
      ) : null}
      {hooks.length ? (
        <div className="flex flex-col gap-1 border-t border-border pt-2">
          <span className="font-mono text-2xs tracking-wider text-fg-muted uppercase">Заголовки-хуки</span>
          <ul className="flex flex-wrap gap-1.5">
            {hooks.map((hook) => (
              <li key={hook}>
                <Badge variant="human" outline>
                  {hook}
                </Badge>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {advice.length ? (
        <div className="flex flex-col gap-1 border-t border-border pt-2">
          <span className="font-mono text-2xs tracking-wider text-fg-muted uppercase">Подача</span>
          <ul className="flex flex-col gap-0.5 text-xs text-fg-muted">
            {advice.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </SectionCard>
  );
}

/** «Референсы»: чужие нарезки за период — доказательство, что тема заходит; миниатюры через наш прокси. */
function ReferencesCard({ film }: { film: TrendsFilmDetail }) {
  const videos = film.videos ?? [];
  const refs = film.references ?? [];
  const rows = videos.length
    ? videos.map((video) => ({
        key: video.video_id,
        url: video.url ?? null,
        title: video.title ?? video.video_id,
        thumb: video.thumb ?? null,
        channel: video.channel_name ?? null,
        views: video.views ?? null,
        duration: video.duration_s ?? null,
        outlier: video.outlier_x ?? null,
        growth: video.growth7 ?? null,
        episode:
          video.season || video.episode
            ? [video.season ? `сезон ${video.season}` : null, video.episode ? `серия ${video.episode}` : null]
                .filter(Boolean)
                .join(", ")
            : null,
      }))
    : refs.map((ref, index) => ({
        key: ref.video_id ?? ref.url ?? String(index),
        url: ref.url ?? null,
        title: ref.title ?? ref.url ?? "ролик",
        thumb: ref.thumb ?? null,
        channel: ref.channel ?? null,
        views: ref.views ?? null,
        duration: ref.duration_s ?? null,
        outlier: null,
        growth: null,
        episode: null,
      }));
  return (
    <SectionCard
      title="Референсы: чужие нарезки"
      actions={
        rows.length ? (
          <Badge variant="neutral" outline className="font-mono">
            {rows.length}
          </Badge>
        ) : undefined
      }
    >
      {rows.length === 0 ? (
        <p className="text-sm text-fg-muted">За период чужих нарезок по этому произведению не найдено.</p>
      ) : (
        <ul className="flex flex-col divide-y divide-border">
          {rows.slice(0, 12).map((row) => (
            <li key={row.key} className="flex items-center gap-2.5 py-1.5 first:pt-0 last:pb-0">
              <span
                className="relative block h-10 w-[72px] shrink-0 overflow-hidden rounded-sm bg-video"
                aria-hidden="true"
              >
                {row.thumb ? (
                  // eslint-disable-next-line @next/next/no-img-element -- обложка из данных Kino-trends через наш API
                  <img src={row.thumb} alt="" loading="lazy" className="h-full w-full object-cover" />
                ) : null}
              </span>
              <span className="flex min-w-0 flex-1 flex-col">
                {row.url ? (
                  <a
                    href={row.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="truncate text-sm font-medium text-fg hover:underline"
                    title={row.title}
                  >
                    {row.title}
                  </a>
                ) : (
                  <span className="truncate text-sm font-medium text-fg">{row.title}</span>
                )}
                <span className="truncate text-2xs text-fg-muted">
                  {[row.channel, row.duration !== null ? formatDuration(row.duration) : null, row.episode]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              </span>
              <span className="flex shrink-0 flex-col items-end">
                <span className="tabular font-mono text-xs text-fg">{formatNumber(row.views)}</span>
                <span className="tabular text-2xs text-fg-muted">
                  {row.outlier !== null && row.outlier >= 3 ? (
                    <span className="text-positive">×{formatNumber(row.outlier, 1)} канала</span>
                  ) : null}
                  {row.growth
                    ? `${row.outlier !== null && row.outlier >= 3 ? " · " : ""}+${formatNumber(row.growth)}/7 дн`
                    : null}
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </SectionCard>
  );
}

/** Статистика нарезок: каналы, сезоны (для сериалов), длительности, публикации по дням. */
function StatsCard({ film }: { film: TrendsFilmDetail }) {
  const channels = (film.channels ?? []).map((c) => c as Record<string, unknown>);
  const seasons = (film.seasons ?? []).map((s) => s as Record<string, unknown>);
  const durations = (film.durations ?? []).map((d) => d as Record<string, unknown>);
  const daily = (film.daily ?? []).map((d) => d as Record<string, unknown>);
  const isSeries = film.title.kind ? film.title.kind !== "film" && film.title.kind !== "animated" : false;
  const dailyRows = daily
    .map((d) => ({ x: str(d.day) ?? "", value: num(d.n), extra: num(d.views) }))
    .filter((row) => row.x);
  return (
    <SectionCard title="Статистика нарезок" bodyClassName="gap-4">
      {seasons.length ? (
        <div className="flex flex-col gap-1.5">
          <span className="font-mono text-2xs tracking-wider text-fg-muted uppercase">По сезонам</span>
          <BarList
            rows={seasons
              .map((s) => ({ season: num(s.season), n: num(s.n) ?? 0, views: num(s.views) ?? 0 }))
              .filter((s) => s.season !== null)
              .map((s) => ({
                label: `${s.season} сезон`,
                value: s.views,
                sub: `${s.n} ${plural(s.n, "ролик", "ролика", "роликов")}`,
              }))}
          />
        </div>
      ) : isSeries ? (
        <p className="text-xs text-fg-muted">Сезоны в чужих нарезках не указаны — режут без привязки к серии.</p>
      ) : null}
      {channels.length ? (
        <div className="flex flex-col gap-1.5">
          <span className="font-mono text-2xs tracking-wider text-fg-muted uppercase">Каналы</span>
          <BarList
            rows={channels.slice(0, 6).map((c) => ({
              label: str(c.name) ?? str(c.channel_id) ?? "канал",
              value: num(c.views) ?? 0,
              sub: num(c.n) !== null ? `${num(c.n)} ${plural(num(c.n) ?? 0, "ролик", "ролика", "роликов")}` : undefined,
            }))}
          />
        </div>
      ) : null}
      {durations.length ? (
        <div className="flex flex-col gap-1.5">
          <span className="font-mono text-2xs tracking-wider text-fg-muted uppercase">Длительность роликов</span>
          <BarList rows={durations.map((d) => ({ label: str(d.bucket) ?? "?", value: num(d.n) ?? 0 }))} />
        </div>
      ) : null}
      {dailyRows.length > 1 ? (
        <div className="flex flex-col gap-1.5">
          <span className="font-mono text-2xs tracking-wider text-fg-muted uppercase">Публикации по дням</span>
          <LazyTrendSeriesChart
            rows={dailyRows}
            kind="bars"
            label="роликов"
            extraLabel="просмотров"
            formatX={(value) => formatDay(value).replace(/\.\d{4}$/, "")}
            formatValue={(value) => formatNumber(value)}
          />
        </div>
      ) : null}
      {!channels.length && !durations.length && dailyRows.length <= 1 && !seasons.length ? (
        <p className="text-sm text-fg-muted">Статистики за период нет.</p>
      ) : null}
    </SectionCard>
  );
}

/**
 * Разбор произведения (`/trends/film?id=`): бриф Kino-trends (почему заходит, как резать, права, исходники,
 * сцены и хуки) и статистика чужих нарезок (референсы, каналы, сезоны, по дням). Данные — мост
 * `/trends/films/{id}`; «В конвейер» — в карточке конвейера и шапке.
 */
export function FilmDetailScreen() {
  const [rawId] = useQueryState("id");
  const [period, setPeriod] = useQueryState(
    "period",
    parseAsStringLiteral(PERIODS).withDefault("30").withOptions({ history: "replace" }),
  );
  const id = rawId && TITLE_ID_RE.test(rawId) ? rawId : null;
  const user = useCurrentUser();
  const operator = canOperate(user.role);
  const film = useTrendsFilm(id, { period });
  const { items: pipelineItems } = usePipelineItemsFor("film", id);

  if (!id) {
    return (
      <div className="p-4 md:p-6" data-testid="trends-film-screen">
        <EmptyState
          icon={Clapperboard}
          title="Произведение не выбрано"
          description="Откройте разбор из Трендоскаута: вкладка «Кино и сериалы» или календарь дня → название. В адресе должен быть параметр id произведения."
          action={
            <Link href={routeHref("/trends", { tab: "films" })} className="text-sm text-accent hover:underline">
              К кино и сериалам
            </Link>
          }
        />
      </div>
    );
  }

  const back = (
    <Link
      href={routeHref("/trends", { tab: "films" })}
      className="inline-flex items-center gap-1 text-xs text-fg-muted hover:text-fg"
      data-testid="trends-film-back"
    >
      <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
      Назад к Трендоскауту
    </Link>
  );

  if (film.isError) {
    const notFound = isApiError(film.error) && film.error.status === 404;
    return (
      <div className="flex flex-col gap-4 p-4 md:p-6" data-testid="trends-film-screen">
        {back}
        {notFound ? (
          <EmptyState
            icon={Clapperboard}
            title="Нет такого произведения"
            description="Kino-trends его не знает или оно удалено из разметки."
          />
        ) : (
          <SectionError error={film.error} onRetry={() => void film.refetch()} title="Разбор не загрузился" />
        )}
      </div>
    );
  }

  const data = film.data;
  if (!data) {
    return (
      <div className="flex flex-col gap-4 p-4 md:p-6" aria-busy="true" data-testid="trends-film-screen">
        {back}
        <div className="flex gap-4">
          <Skeleton className="h-36 w-24" />
          <div className="flex flex-1 flex-col gap-2">
            <Skeleton className="h-6 w-40" />
            <Skeleton className="h-8 w-full max-w-xl" />
            <Skeleton className="h-4 w-full max-w-lg" />
          </div>
        </div>
        <SkeletonStatGrid count={6} className="sm:grid-cols-3 xl:grid-cols-6" />
        <div className="grid gap-4 lg:grid-cols-2">
          <Skeleton className="h-64" />
          <Skeleton className="h-64" />
        </div>
      </div>
    );
  }

  const title = data.title;
  const stats = data.stats ?? null;
  const rights = data.rights ?? null;
  const level = rightsLevel(rights?.level);
  const active = activePipelineItem(pipelineItems);
  const readiness = filmReadiness(
    { ready: null, ready_reasons: [], rights, source: data.source ?? null, in_pipeline: null },
    active?.status ?? null,
  );
  const scheduleToday = (data.schedule ?? []).map((slot) => slot as Record<string, unknown>);
  const secondTitle =
    title.original_title && title.original_title !== title.label
      ? title.original_title
      : title.label_en && title.label_en !== title.label
        ? title.label_en
        : null;
  const metaLine = [
    title.kind_ru,
    secondTitle,
    (title.countries ?? []).join(", "),
    (title.genres ?? []).join(", "),
    title.franchise ? `франшиза: ${title.franchise}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  const enqueueDisabled = level === "block" || !data.source;
  const enqueueTitle =
    level === "block"
      ? "Произведение не проходит проверку прав"
      : !data.source
        ? "Нет исходника — Kino-trends не примет"
        : undefined;

  return (
    <div className="flex flex-col gap-4 p-4 md:p-6" data-testid="trends-film-screen">
      {back}
      <div className="flex flex-wrap items-start gap-4">
        <Poster src={data.poster_url} label={title.label ?? title.title_id} className="h-36 w-24 rounded-md" />
        <div className="min-w-0 flex-1">
          <PageHeader
            eyebrow="Тренды · Разбор произведения"
            title={`${title.label ?? title.title_id}${title.year ? ` (${title.year})` : ""}`}
            description={metaLine}
            actions={
              <>
                <Select
                  aria-label="Период статистики"
                  value={period}
                  onChange={(event) => void setPeriod(event.target.value as TrendsFilmPeriod)}
                  className="w-32"
                >
                  {FILM_PERIOD_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </Select>
                {data.kinopoisk_url ? (
                  <a
                    href={data.kinopoisk_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={buttonVariants({ variant: "outline", size: "sm" })}
                  >
                    Кинопоиск
                    <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                  </a>
                ) : null}
                {data.imdb_url ? (
                  <a
                    href={data.imdb_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={buttonVariants({ variant: "outline", size: "sm" })}
                  >
                    IMDb
                    <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                  </a>
                ) : null}
                {operator ? (
                  <EnqueueButton
                    kind="film"
                    refId={title.title_id}
                    size="sm"
                    disabled={enqueueDisabled || !!active}
                    title={active ? "Активное задание уже есть" : enqueueTitle}
                  />
                ) : null}
              </>
            }
          />
        </div>
      </div>

      <Callout
        variant={readiness.ready ? "info" : level === "block" ? "negative" : "warning"}
        title={readiness.ready ? "Готово к нарезке" : "Пока не готово к нарезке"}
      >
        {readiness.ready
          ? `Права проходят${rights?.clip_max_s ? `, клип до ${rights.clip_max_s} с` : ""}, исходник есть${active ? "" : ", в конвейере нет"} — можно ставить в конвейер.`
          : readiness.reasons.join("; ")}
        {scheduleToday.length ? (
          <span className="ml-1">
            · в календаре Kino-trends:{" "}
            {scheduleToday
              .map((slot) => `${formatDay(str(slot.day) ?? "")}${str(slot.status) === "done" ? " (сделано)" : ""}`)
              .join(", ")}
          </span>
        ) : null}
      </Callout>

      {stats ? (
        <StatTileGrid className="sm:grid-cols-3 xl:grid-cols-6">
          <StatTile
            label="Чужих нарезок"
            value={formatNumber(stats.n_shorts)}
            hint={
              stats.n_channels
                ? `${formatNumber(stats.n_channels)} ${plural(stats.n_channels, "канал", "канала", "каналов")}`
                : undefined
            }
          />
          <StatTile
            label="Просмотров всего"
            value={formatNumber(stats.views_sum)}
            hint={stats.views_max ? `макс. ${formatNumber(stats.views_max)}` : undefined}
          />
          <StatTile
            label="Медиана просмотров"
            value={formatNumber(stats.views_median)}
            hint={stats.views_p90 ? `P90 ${formatNumber(stats.views_p90)}` : undefined}
          />
          <StatTile label="Просмотров в день" value={formatNumber(stats.vpd_sum)} hint="сумма по роликам" />
          <StatTile
            label="Прирост за 7 дней"
            value={stats.growth7 ? `+${formatNumber(stats.growth7)}` : "—"}
            hint="по точным замерам"
          />
          <StatTile label="Средняя длительность" value={formatDuration(stats.dur_avg)} />
        </StatTileGrid>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-2">
        <WhyCard film={data} />
        <CutterCard film={data} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <FilmRightsCard titleId={title.title_id} rights={rights} operator={operator} />
        <FilmSourcesCard titleId={title.title_id} sources={data.sources ?? []} best={data.source} operator={operator} />
      </div>

      <ScenesCard film={data} />

      <div className={cn("grid gap-4", "lg:grid-cols-[minmax(0,1fr)_360px]")}>
        <ReferencesCard film={data} />
        <div className="flex flex-col gap-4">
          <StatsCard film={data} />
          <PipelineRefCard
            kind="film"
            refId={title.title_id}
            operator={operator}
            enqueueDisabled={enqueueDisabled}
            enqueueTitle={enqueueTitle}
          />
        </div>
      </div>
    </div>
  );
}
