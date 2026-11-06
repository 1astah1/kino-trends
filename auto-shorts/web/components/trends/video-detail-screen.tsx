"use client";

import { ArrowLeft, Download, ExternalLink, ListVideo, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useQueryState } from "nuqs";
import { toast } from "sonner";

import { useCurrentUser } from "@/components/auth/auth-context";
import { PageHeader } from "@/components/common/page-header";
import { SectionError } from "@/components/common/section-error";
import {
  Badge,
  Button,
  buttonVariants,
  EmptyState,
  SectionCard,
  Skeleton,
  SkeletonStatGrid,
  StatTile,
  StatTileGrid,
  StatusBadge,
} from "@/components/ui";
import { errorMessage, isApiError } from "@/lib/api/errors";
import { canOperate } from "@/lib/auth";
import { cn } from "@/lib/cn";
import { BUSINESS_TIMEZONE, formatDateTime, formatNumber } from "@/lib/format";
import { routeHref } from "@/lib/nav";
import { type TrendsVideoDetail, useTrendsVideo, useTrendsVideoActions } from "@/lib/queries/trends";

import { cleanLevel, FILE_STATUS_META, fileStatus, fitTone, formatDuration, formatFit } from "./labels";
import { PipelineRefCard } from "./pipeline-ref-card";
import { LazyTrendSeriesChart } from "./trend-series-chart-lazy";
import { VideoAnalysisCard, VideoChartsCard, VideoYoutubeCard } from "./video-analysis";
import { VideoFrames } from "./video-frames";
import { VideoVerdict } from "./video-verdict";

const VIDEO_ID_RE = /^[A-Za-z0-9_-]{6,20}$/;

const measureFmt = new Intl.DateTimeFormat("ru-RU", {
  timeZone: BUSINESS_TIMEZONE,
  day: "2-digit",
  month: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});

/** `2026-10-24T13:12:02Z` → «24.10, 16:12» (МСК) для оси замеров. */
function formatMeasureTick(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : measureFmt.format(date);
}

function HeaderActions({ video, operator }: { video: TrendsVideoDetail; operator: boolean }) {
  const actions = useTrendsVideoActions(video.video_id);
  const file = video.file ?? null;
  const status = fileStatus(file?.status, file?.file_url);

  async function onAnalyze() {
    try {
      const updated = await actions.analyze.mutateAsync();
      const notes = updated.notes ?? [];
      toast.success("Разобрано", { description: notes.length ? notes.join(" · ") : "Текст, кадры и оценка обновлены" });
    } catch (error) {
      toast.error("Разбор не удался", { description: errorMessage(error) });
    }
  }

  async function onFile() {
    try {
      await actions.file.mutateAsync();
      toast.success("Поставлено на скачивание", { description: "Прямая ссылка на mp4 появится через пару минут." });
    } catch (error) {
      toast.error("mp4 не заказан", { description: errorMessage(error) });
    }
  }

  return (
    <>
      {video.url ? (
        <a
          href={video.url}
          target="_blank"
          rel="noopener noreferrer"
          className={buttonVariants({ variant: "outline", size: "sm" })}
          data-testid="trends-video-youtube"
        >
          YouTube
          <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
        </a>
      ) : null}
      {operator ? (
        <>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void onAnalyze()}
            disabled={actions.analyze.isPending}
            title="Разобрать заново: текст нейросетью, 6 кадров с хранилища, пересчёт оценки (до минуты)"
            data-testid="trends-video-analyze"
          >
            <RefreshCw className={cn("h-3.5 w-3.5", actions.analyze.isPending && "animate-spin")} aria-hidden="true" />
            {actions.analyze.isPending ? "Разбираю…" : "Разобрать заново"}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void onFile()}
            disabled={actions.file.isPending || status === "running" || status === "done"}
            title={
              status === "done"
                ? "mp4 уже на хранилище"
                : status === "running"
                  ? "mp4 уже качается"
                  : "Скачать 720p на хранилище Kino-trends и получить прямую ссылку для заливщика"
            }
            data-testid="trends-video-file"
          >
            <Download className="h-3.5 w-3.5" aria-hidden="true" />
            {actions.file.isPending ? "Заказываю…" : status === "error" ? "mp4: повторить" : "Заказать mp4"}
          </Button>
        </>
      ) : null}
    </>
  );
}

/** Факты в плитках: просмотры, прирост, в день, комментарии, балл, наша аудитория (как `facts` в Kino-trends). */
function Facts({ video }: { video: TrendsVideoDetail }) {
  const likeShare =
    video.likes && video.views
      ? `лайков ${formatNumber(video.likes)} (${((video.likes / Math.max(1, video.views)) * 100).toFixed(1).replace(".", ",")} %)`
      : null;
  const fit = video.analysis?.audience_fit ?? null;
  return (
    <StatTileGrid className="sm:grid-cols-3 xl:grid-cols-6">
      <StatTile label="Просмотров" value={formatNumber(video.views)} hint={likeShare ?? "по карточке YouTube"} />
      <StatTile
        label="Прирост в день"
        value={video.growth !== null && video.growth !== undefined ? `+${formatNumber(video.growth)}` : "—"}
        hint="по замерам между прогонами"
      />
      <StatTile label="Просмотров в день" value={formatNumber(video.vpd)} hint="с момента публикации" />
      <StatTile label="Комментариев" value={formatNumber(video.comments)} />
      <StatTile
        label="Балл чистоты"
        value={video.score !== null && video.score !== undefined ? formatNumber(video.score) : "—"}
        hint={video.level_ru ?? undefined}
      />
      <StatTile
        label="Наша аудитория"
        value={<span className={fitTone(fit)}>{formatFit(fit)}</span>}
        hint="совпадение по модели"
      />
    </StatTileGrid>
  );
}

/** Блок для заливщика: прямой mp4 из хранилища или ссылка YouTube, статус файла. */
function CutterLine({ video }: { video: TrendsVideoDetail }) {
  const file = video.file ?? null;
  const status = fileStatus(file?.status, file?.file_url);
  const direct = file?.file_url ?? video.cutter?.direct_file_url ?? null;
  const url = direct ?? video.url ?? null;
  async function copy() {
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      toast.success("Ссылка скопирована");
    } catch {
      toast.error("Не удалось скопировать", { description: url });
    }
  }
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-surface-2/40 px-3 py-2 text-sm">
      <span className="font-medium text-fg">ссылка для заливщика</span>
      <StatusBadge status={status} map={FILE_STATUS_META} />
      <span className="text-xs text-fg-muted">
        {direct ? "прямой mp4 из хранилища" : "ссылка YouTube, файл ещё не готов"}
      </span>
      {file?.size ? (
        <span className="tabular text-xs text-fg-muted">{formatNumber(file.size / 1_048_576, 1)} МБ</span>
      ) : null}
      {file?.error && status === "error" ? <span className="text-xs text-negative">{file.error}</span> : null}
      {url ? (
        <>
          <code className="min-w-0 flex-1 truncate font-mono text-xs text-fg-muted" title={url}>
            {url}
          </code>
          <Button variant="ghost" size="xs" onClick={() => void copy()}>
            скопировать
          </Button>
        </>
      ) : null}
    </div>
  );
}

/**
 * Разбор ролика (`/trends/video?id=`): всё, что Kino-trends знает о ролике из чартов — карточка YouTube,
 * оценка чистоты с решением оператора, кадры и свободные зоны под баннер, разбор нейросети, чарты по странам,
 * замеры просмотров, файл на хранилище и состояние в конвейере. Данные — мост `/trends/videos/{id}`.
 */
export function VideoDetailScreen() {
  const [rawId] = useQueryState("id");
  const id = rawId && VIDEO_ID_RE.test(rawId) ? rawId : null;
  const user = useCurrentUser();
  const operator = canOperate(user.role);
  const video = useTrendsVideo(id);

  if (!id) {
    return (
      <div className="p-4 md:p-6" data-testid="trends-video-screen">
        <EmptyState
          icon={ListVideo}
          title="Ролик не выбран"
          description="Откройте разбор из Трендоскаута: вкладка «Видео» или подборка дня → название ролика. В адресе должен быть параметр id ролика YouTube."
          action={
            <Link href={routeHref("/trends", { tab: "videos" })} className="text-sm text-accent hover:underline">
              К чартам видео
            </Link>
          }
        />
      </div>
    );
  }

  const data = video.data;
  const back = (
    <Link
      href={routeHref("/trends", { tab: "videos" })}
      className="inline-flex items-center gap-1 text-xs text-fg-muted hover:text-fg"
      data-testid="trends-video-back"
    >
      <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
      Назад к Трендоскауту
    </Link>
  );

  if (video.isError) {
    const notFound = isApiError(video.error) && video.error.status === 404;
    return (
      <div className="flex flex-col gap-4 p-4 md:p-6" data-testid="trends-video-screen">
        {back}
        {notFound ? (
          <EmptyState
            icon={ListVideo}
            title="Нет такого ролика"
            description="Ролик не из чартов Kino-trends или уже удалён из них."
          />
        ) : (
          <SectionError error={video.error} onRetry={() => void video.refetch()} title="Разбор не загрузился" />
        )}
      </div>
    );
  }

  if (!data) {
    return (
      <div className="flex flex-col gap-4 p-4 md:p-6" aria-busy="true" data-testid="trends-video-screen">
        {back}
        <Skeleton className="h-6 w-40" />
        <Skeleton className="h-8 w-full max-w-2xl" />
        <SkeletonStatGrid count={6} className="sm:grid-cols-3 xl:grid-cols-6" />
        <div className="grid gap-4 lg:grid-cols-2">
          <Skeleton className="h-72" />
          <Skeleton className="h-72" />
        </div>
      </div>
    );
  }

  const metaLine = [
    data.channel_name
      ? `${data.channel_name}${data.channel_subs ? ` · ${formatNumber(data.channel_subs)} подписчиков` : ""}`
      : null,
    data.published_at ? formatDateTime(data.published_at, { short: true }) : null,
    data.duration_s !== null && data.duration_s !== undefined
      ? `${formatDuration(data.duration_s)}${data.is_short === 1 ? " · шортс" : data.is_short === 0 ? " · длинное" : ""}`
      : null,
    data.category,
    data.lang,
    data.holder ? `канал правообладателя: ${data.holder}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  const series = (data.series ?? []).map((point) => ({ x: point.ts, value: point.views ?? null }));
  const level = cleanLevel(data.level);

  return (
    <div className="flex flex-col gap-4 p-4 md:p-6" data-testid="trends-video-screen">
      {back}
      <PageHeader
        eyebrow="Тренды · Разбор ролика"
        title={data.title ?? data.video_id}
        description={metaLine}
        actions={<HeaderActions video={data} operator={operator} />}
      />
      <Facts video={data} />
      <CutterLine video={data} />

      <div className="grid gap-4 lg:grid-cols-2">
        <VideoFrames video={data} />
        <VideoVerdict video={data} operator={operator} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <VideoAnalysisCard video={data} />
        <div className="flex flex-col gap-4">
          <VideoChartsCard video={data} />
          <VideoYoutubeCard video={data} />
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <SectionCard
          title="Просмотры по замерам"
          actions={
            series.length ? (
              <Badge variant="neutral" outline className="font-mono">
                {series.length} замеров
              </Badge>
            ) : undefined
          }
        >
          {series.length > 1 ? (
            <LazyTrendSeriesChart
              rows={series}
              label="просмотров"
              formatX={formatMeasureTick}
              formatValue={(value) => formatNumber(value)}
            />
          ) : (
            <p className="text-sm text-fg-muted">
              Замеров пока {series.length}: график появится, когда Kino-trends снимет статистику ещё раз.
            </p>
          )}
        </SectionCard>
        <PipelineRefCard
          kind="video"
          refId={data.video_id}
          operator={operator}
          enqueueDisabled={level === "no"}
          enqueueTitle={level === "no" ? "Ролик отмечен «не брать»" : undefined}
        />
      </div>

      {data.description ? (
        <details className="rounded-md border border-border p-3">
          <summary className="cursor-pointer text-sm font-medium text-fg">Описание ролика</summary>
          <p className="mt-2 text-sm whitespace-pre-wrap text-fg-muted">{data.description.slice(0, 3000)}</p>
        </details>
      ) : null}
    </div>
  );
}
