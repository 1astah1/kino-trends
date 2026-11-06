"use client";

import { Badge, SectionCard } from "@/components/ui";
import { mediaSrc } from "@/lib/api/media";
import { formatDateTime } from "@/lib/format";
import type { TrendsVideoDetail } from "@/lib/queries/trends";

import { FRAME_FLAGS, ORIENTATION_RU } from "./labels";

/**
 * «Кадры: что в картинке»: плитка из 6 кадров с хранилища Kino-trends (через наш прокси, с меткой времени
 * разбора, чтобы кеш обновлялся после «Разобрать заново»), флаги помех и свободные зоны под наш баннер.
 */
export function VideoFrames({ video }: { video: TrendsVideoDetail }) {
  const frames = video.frames ?? null;
  const flags = frames ? FRAME_FLAGS.filter((flag) => Boolean(frames[flag.key])) : [];
  const freeZones = frames?.free_zones ?? [];
  const src = video.frames_url
    ? mediaSrc(`${video.frames_url}?t=${encodeURIComponent(video.frames_at ?? "")}`)
    : undefined;

  return (
    <SectionCard title="Кадры: что в картинке" bodyClassName="gap-3">
      {src ? (
        <figure className="flex flex-col gap-2">
          <div className="overflow-hidden rounded-md border border-border bg-video">
            {/* eslint-disable-next-line @next/next/no-img-element -- плитка кадров из Kino-trends через наш API */}
            <img src={src} alt="Шесть кадров ролика" className="block h-auto w-full" loading="lazy" />
          </div>
          {video.frames_at ? (
            <figcaption className="text-2xs text-fg-muted">
              кадры вынуты {formatDateTime(video.frames_at, { short: true })}
              {frames?.model ? ` · модель ${frames.model}` : ""}
            </figcaption>
          ) : null}
        </figure>
      ) : (
        <p className="text-sm text-fg-muted">
          {frames?.error
            ? `Кадры не вынулись: ${frames.error}`
            : "Кадры ещё не проверялись. «Разобрать заново» вынет 6 кадров с хранилища и покажет, что в картинке."}
        </p>
      )}
      {frames && !frames.error ? (
        <div className="flex flex-wrap gap-1.5">
          {flags.length ? (
            flags.map((flag) => (
              <Badge key={flag.key} variant="negative">
                {flag.key === "watermark" && frames.watermark_where
                  ? `${flag.label} (${frames.watermark_where})`
                  : flag.label}
              </Badge>
            ))
          ) : (
            <Badge variant="positive">{frames.clean ? "чисто: чужого брендинга нет" : "явных помех нет"}</Badge>
          )}
          {frames.orientation ? (
            <Badge variant="neutral">{ORIENTATION_RU[frames.orientation] ?? frames.orientation}</Badge>
          ) : null}
          {frames.quality && frames.quality !== "ок" ? (
            <Badge variant="warning">{frames.quality} качество</Badge>
          ) : null}
          {frames.width && frames.height ? (
            <Badge variant="neutral" outline className="font-mono">
              {frames.width}×{frames.height}
            </Badge>
          ) : null}
        </div>
      ) : null}
      {freeZones.length ? (
        <p className="text-sm text-fg">
          <span className="text-fg-muted">свободно под наш баннер: </span>
          <span className="font-medium">{freeZones.join(", ")}</span>
        </p>
      ) : null}
      {frames?.note ? <p className="text-xs text-fg-muted">{frames.note}</p> : null}
    </SectionCard>
  );
}
