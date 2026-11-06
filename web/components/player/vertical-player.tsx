"use client";

import { VideoOff } from "lucide-react";
import { type ReactNode, type RefObject, useEffect, useImperativeHandle, useRef, useState } from "react";

import { mediaSrc } from "@/lib/api/media";
import { cn } from "@/lib/cn";

import { FRAME_SEC, type PlayerController, type PlayerTimeStore } from "./player-time";

/** Как часто плеер пишет время в стор во время проигрывания (караоке и оверлей). */
const TIME_TICK_MS = 80;

export interface VerticalPlayerProps {
  /** URL из ответа API (`/api/v1/...`); чужие адреса отбрасываются (§7.4, §7.7). */
  src: string | null | undefined;
  poster?: string | null;
  muted?: boolean;
  /** Громкость 0…1; применяется и к новому ролику. */
  volume?: number;
  loop?: boolean;
  autoPlay?: boolean;
  /** Длительность из API — пока видео не загрузило метаданные. */
  durationHint?: number | null;
  timeStore?: PlayerTimeStore;
  controllerRef?: RefObject<PlayerController | null>;
  /** SVG-оверлей `viewBox 0 0 1080 1920`: ложится ровно на кадр (object-contain + meet). */
  overlay?: ReactNode;
  className?: string;
  label: string;
  /** Ключ ролика: смена ключа сбрасывает время и ошибку. */
  mediaKey?: string | number;
}

/**
 * Вертикальный плеер 9:16 на абсолютно чёрном поле, без скруглений (§7.6). Видео вписано
 * `object-contain`, оверлей — SVG с тем же вписыванием (`preserveAspectRatio="xMidYMid meet"`),
 * поэтому рамки совпадают с кадром при любой форме контейнера.
 */
export function VerticalPlayer({
  src,
  poster,
  muted = false,
  volume = 1,
  loop = true,
  autoPlay = false,
  durationHint,
  timeStore,
  controllerRef,
  overlay,
  className,
  label,
  mediaKey,
}: VerticalPlayerProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const safeSrc = mediaSrc(src);
  const safePoster = mediaSrc(poster);
  const [failed, setFailed] = useState<{ key: string | number | undefined; src: string | undefined } | null>(null);
  const hasError = failed !== null && failed.key === mediaKey && failed.src === safeSrc;

  useImperativeHandle(
    controllerRef,
    (): PlayerController => ({
      togglePlay: () => {
        const video = videoRef.current;
        if (!video) return;
        if (video.paused) void video.play().catch(() => undefined);
        else video.pause();
      },
      play: () => void videoRef.current?.play().catch(() => undefined),
      pause: () => videoRef.current?.pause(),
      seekBy: (delta) => {
        const video = videoRef.current;
        if (!video) return;
        const end = Number.isFinite(video.duration) ? video.duration : Number.POSITIVE_INFINITY;
        video.currentTime = Math.max(0, Math.min(end, video.currentTime + delta));
        timeStore?.setState({ t: video.currentTime });
      },
      seekTo: (sec) => {
        const video = videoRef.current;
        if (!video) return;
        video.currentTime = Math.max(0, sec);
        timeStore?.setState({ t: video.currentTime });
      },
      frame: (delta) => {
        const video = videoRef.current;
        if (!video) return;
        video.pause();
        video.currentTime = Math.max(0, video.currentTime + delta * FRAME_SEC);
        timeStore?.setState({ t: video.currentTime });
      },
      fullscreen: () => {
        const box = boxRef.current;
        if (!box) return;
        if (document.fullscreenElement) void document.exitFullscreen().catch(() => undefined);
        else void box.requestFullscreen?.().catch(() => undefined);
      },
    }),
    [timeStore],
  );

  // Новый ролик: время с нуля, длительность из API до загрузки метаданных.
  useEffect(() => {
    timeStore?.setState({ t: 0, duration: durationHint ?? 0, playing: false });
  }, [mediaKey, safeSrc, durationHint, timeStore]);

  // Время во время проигрывания: rAF с прореживанием; на паузе — события seeked/timeupdate.
  useEffect(() => {
    const video = videoRef.current;
    if (!video || !timeStore) return;
    let raf = 0;
    let last = 0;
    const tick = (now: number) => {
      if (now - last >= TIME_TICK_MS) {
        last = now;
        timeStore.setState({ t: video.currentTime });
      }
      raf = requestAnimationFrame(tick);
    };
    const onPlay = () => {
      timeStore.setState({ playing: true });
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(tick);
    };
    const onPause = () => {
      cancelAnimationFrame(raf);
      timeStore.setState({ playing: false, t: video.currentTime });
    };
    const onTime = () => {
      if (video.paused) timeStore.setState({ t: video.currentTime });
    };
    const onMeta = () => {
      if (Number.isFinite(video.duration) && video.duration > 0) timeStore.setState({ duration: video.duration });
    };
    video.addEventListener("play", onPlay);
    video.addEventListener("pause", onPause);
    video.addEventListener("ended", onPause);
    video.addEventListener("seeked", onTime);
    video.addEventListener("timeupdate", onTime);
    video.addEventListener("loadedmetadata", onMeta);
    return () => {
      cancelAnimationFrame(raf);
      video.removeEventListener("play", onPlay);
      video.removeEventListener("pause", onPause);
      video.removeEventListener("ended", onPause);
      video.removeEventListener("seeked", onTime);
      video.removeEventListener("timeupdate", onTime);
      video.removeEventListener("loadedmetadata", onMeta);
    };
  }, [timeStore, safeSrc, mediaKey, hasError]);

  // Громкость: у <video> нет атрибута, только свойство — ставим на каждый новый элемент и при смене.
  useEffect(() => {
    const video = videoRef.current;
    if (video) video.volume = Math.max(0, Math.min(1, volume));
  }, [volume, safeSrc, mediaKey, hasError]);

  // Автозапуск нового ролика (браузер может запретить звук без жеста — тогда просто стоим на паузе).
  useEffect(() => {
    const video = videoRef.current;
    if (!video || !safeSrc || !autoPlay) return;
    void video.play().catch(() => undefined);
  }, [safeSrc, mediaKey, autoPlay]);

  return (
    <div
      ref={boxRef}
      className={cn("relative h-full w-full overflow-hidden bg-video", className)}
      data-testid="vertical-player"
      role="region"
      aria-label={label}
    >
      {safeSrc && !hasError ? (
        <video
          key={`${mediaKey ?? ""}:${safeSrc}`}
          ref={videoRef}
          src={safeSrc}
          poster={safePoster}
          muted={muted}
          loop={loop}
          playsInline
          preload="auto"
          onError={() => setFailed({ key: mediaKey, src: safeSrc })}
          className="absolute inset-0 h-full w-full object-contain"
        />
      ) : (
        <>
          {safePoster ? (
            // eslint-disable-next-line @next/next/no-img-element -- статический экспорт, картинки без оптимизатора
            <img src={safePoster} alt="" className="absolute inset-0 h-full w-full object-contain" />
          ) : null}
          <div className="absolute inset-x-0 bottom-3 flex justify-center">
            <span className="inline-flex items-center gap-1.5 rounded-sm bg-black/70 px-2 py-1 text-xs text-white">
              <VideoOff className="h-3.5 w-3.5" aria-hidden="true" />
              {hasError ? "Видео не загрузилось" : "Видео ещё не готово"}
            </span>
          </div>
        </>
      )}
      {overlay ? <div className="pointer-events-none absolute inset-0">{overlay}</div> : null}
    </div>
  );
}
