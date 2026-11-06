"use client";

import { Pause, Play, RectangleHorizontal, RectangleVertical, Repeat, SkipBack, Volume2, VolumeX } from "lucide-react";
import { type Ref, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";
import { useStore } from "zustand";

import { Button } from "@/components/ui";
import type { Schemas } from "@/lib/api/client";
import { mediaSrc } from "@/lib/api/media";
import { cn } from "@/lib/cn";
import { type ClipClock, type ClipPlayerController, useClipEditor } from "@/lib/store/clip-editor";

import { formatTc } from "./clip-math";
import { REFRAME_LABELS } from "./labels";

type ReframeMode = Schemas["ReframeMode"];

/** Позиция часов плеера (подписка только на время). */
export function useClockTime(clock: ClipClock): number {
  return useStore(clock, (state) => state.t);
}

function ClockReadout({ clock }: { clock: ClipClock }) {
  const t = useStore(clock, (state) => state.t);
  const duration = useStore(clock, (state) => state.duration);
  return (
    <span className="font-mono text-xs text-fg tabular" data-testid="player-time">
      {formatTc(t)} <span className="text-fg-muted">/ {formatTc(duration, { withCs: false })}</span>
    </span>
  );
}

/**
 * SourcePlayer (§7.3: «плеер прокси с рамкой 9:16 (центр / blur); вертикальный предпросмотр»).
 * Прокси 540p с Range; рамка 9:16 показывает, что попадёт в кадр при центре (слежение за лицом
 * считается при рендере, стадия clip_analyze). Без прокси (ещё готовится или моки без видео) часы
 * идут «вхолостую»: транскрипт, дорожка и границы работают так же.
 */
export function SourcePlayer({
  source,
  clock,
  controllerRef,
  reframe,
  clipRange,
  overlayTitle,
}: {
  source: Schemas["Source"];
  clock: ClipClock;
  controllerRef: Ref<ClipPlayerController>;
  reframe: ReframeMode;
  clipRange: { start: number; end: number } | null;
  overlayTitle?: string;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const loopStartRef = useRef<number | null>(null);
  const virtualRef = useRef<{ timer: ReturnType<typeof setInterval> | null; last: number }>({ timer: null, last: 0 });
  const src = mediaSrc(source.media.proxy_url);
  const [failed, setFailed] = useState(false);
  const [muted, setMuted] = useState(false);
  const view = useClipEditor((state) => state.view);
  const setView = useClipEditor((state) => state.setView);
  const loop = useClipEditor((state) => state.loop);
  const toggleLoop = useClipEditor((state) => state.toggleLoop);
  const playing = useStore(clock, (state) => state.playing);
  const hasVideo = !!src && !failed;
  const duration = source.duration_sec ?? 0;

  // Длительность из API, пока нет метаданных видео.
  useEffect(() => {
    if (duration && !clock.getState().duration) clock.setState({ duration });
  }, [clock, duration]);

  const stopVirtual = useCallback(() => {
    const v = virtualRef.current;
    if (v.timer) clearInterval(v.timer);
    v.timer = null;
  }, []);

  /** Проигрывание момента: остановка или возврат к началу на `stopAt`. */
  const onTick = useCallback(
    (t: number) => {
      const { stopAt } = clock.getState();
      if (stopAt !== null && t >= stopAt) {
        const start = loopStartRef.current;
        if (useClipEditor.getState().loop && start !== null) return start;
        clock.setState({ stopAt: null });
        return -1;
      }
      return null;
    },
    [clock],
  );

  const seek = useCallback(
    (sec: number) => {
      const total = clock.getState().duration || duration;
      const next = Math.max(0, total ? Math.min(total, sec) : sec);
      clock.setState({ t: next });
      const video = videoRef.current;
      if (video && hasVideo) {
        try {
          video.currentTime = next;
        } catch {
          // видео ещё не готово — время уже в часах
        }
      }
    },
    [clock, duration, hasVideo],
  );

  const pause = useCallback(() => {
    stopVirtual();
    videoRef.current?.pause();
    clock.setState({ playing: false });
  }, [clock, stopVirtual]);

  const play = useCallback(() => {
    const video = videoRef.current;
    if (video && hasVideo) {
      void video.play().catch(() => clock.setState({ playing: false }));
      return;
    }
    // Холостой ход: без видео время идёт по таймеру.
    stopVirtual();
    const v = virtualRef.current;
    v.last = performance.now();
    clock.setState({ playing: true });
    v.timer = setInterval(() => {
      const now = performance.now();
      const dt = (now - v.last) / 1000;
      v.last = now;
      const t = clock.getState().t + dt;
      const total = clock.getState().duration || duration;
      const jump = onTick(t);
      if (jump === -1 || (total && t >= total)) {
        stopVirtual();
        clock.setState({ playing: false, t: Math.min(t, total || t) });
        return;
      }
      clock.setState({ t: jump !== null ? jump : t });
    }, 100);
  }, [clock, duration, hasVideo, onTick, stopVirtual]);

  const controller: ClipPlayerController = {
    seek,
    seekBy: (delta) => seek(clock.getState().t + delta),
    toggle: () => (clock.getState().playing ? pause() : play()),
    play,
    pause,
    playRange: (start, end) => {
      loopStartRef.current = start;
      clock.setState({ stopAt: end });
      seek(start);
      play();
    },
  };
  useImperativeHandle(controllerRef, () => controller);

  // События видео → часы. Во время проигрывания — кадр анимации, чтобы слова подсвечивались плавно.
  useEffect(() => {
    const video = videoRef.current;
    if (!video || !src) return;
    let raf = 0;
    const loopFrame = () => {
      const t = video.currentTime;
      const jump = onTick(t);
      if (jump === -1) {
        video.pause();
      } else if (jump !== null) {
        video.currentTime = jump;
      }
      clock.setState({ t: video.currentTime });
      raf = requestAnimationFrame(loopFrame);
    };
    const onPlay = () => {
      clock.setState({ playing: true });
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(loopFrame);
    };
    const onPause = () => {
      cancelAnimationFrame(raf);
      clock.setState({ playing: false, t: video.currentTime });
    };
    const onMeta = () => {
      if (Number.isFinite(video.duration) && video.duration > 0) clock.setState({ duration: video.duration });
    };
    const onSeeked = () => {
      if (video.paused) clock.setState({ t: video.currentTime });
    };
    const onError = () => setFailed(true);
    video.addEventListener("play", onPlay);
    video.addEventListener("pause", onPause);
    video.addEventListener("ended", onPause);
    video.addEventListener("loadedmetadata", onMeta);
    video.addEventListener("seeked", onSeeked);
    video.addEventListener("error", onError);
    return () => {
      cancelAnimationFrame(raf);
      video.removeEventListener("play", onPlay);
      video.removeEventListener("pause", onPause);
      video.removeEventListener("ended", onPause);
      video.removeEventListener("loadedmetadata", onMeta);
      video.removeEventListener("seeked", onSeeked);
      video.removeEventListener("error", onError);
    };
  }, [clock, onTick, src]);

  useEffect(() => () => stopVirtual(), [stopVirtual]);

  // Рамка 9:16 на кадре 16:9: ширина = h·9/16 от ширины кадра.
  const w = source.width ?? 16;
  const h = source.height ?? 9;
  const framePct = Math.min(100, ((h * 9) / 16 / w) * 100);
  const vertical = view === "vertical";

  return (
    <div className="flex flex-col gap-2" data-testid="source-player">
      <div
        className={cn(
          "relative mx-auto overflow-hidden bg-video",
          vertical ? "aspect-[9/16] h-[min(52vh,560px)]" : "aspect-video w-full max-h-[46vh]",
        )}
      >
        {src ? (
          <video
            ref={videoRef}
            src={failed ? undefined : src}
            preload="metadata"
            playsInline
            muted={muted}
            className={cn(
              "absolute inset-0 h-full w-full",
              vertical && reframe !== "fit_blur" ? "object-cover" : "object-contain",
            )}
            onClick={() => controller.toggle()}
          />
        ) : null}
        {!hasVideo ? (
          <div className="absolute inset-0 flex items-center justify-center p-4 text-center text-xs text-fg-muted">
            {src ? "Прокси не загрузился — дорожка и транскрипт работают без картинки" : "Прокси ещё готовится"}
          </div>
        ) : null}
        {!vertical && hasVideo && reframe !== "fit_blur" ? (
          <div
            className="pointer-events-none absolute inset-y-0 border-x-2 border-accent/80"
            style={{
              left: `${(100 - framePct) / 2}%`,
              width: `${framePct}%`,
              boxShadow: "0 0 0 9999px rgb(0 0 0 / 45%)",
            }}
            aria-hidden="true"
          />
        ) : null}
        {vertical && overlayTitle ? (
          <div className="pointer-events-none absolute inset-x-3 top-[12%] rounded-sm bg-black/60 px-2 py-1 text-center text-sm font-semibold text-white">
            {overlayTitle}
          </div>
        ) : null}
        <span className="pointer-events-none absolute bottom-1.5 left-2 rounded-sm bg-black/60 px-1.5 py-0.5 font-mono text-2xs text-white">
          {vertical ? "9:16 · " : ""}
          {REFRAME_LABELS[reframe]}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <Button
          variant="outline"
          size="icon-sm"
          onClick={() => controller.toggle()}
          aria-label={playing ? "Пауза (K)" : "Играть (K)"}
          data-testid="player-toggle"
        >
          {playing ? (
            <Pause className="h-3.5 w-3.5" aria-hidden="true" />
          ) : (
            <Play className="h-3.5 w-3.5" aria-hidden="true" />
          )}
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={!clipRange}
          onClick={() => clipRange && controller.playRange(clipRange.start, clipRange.end)}
          data-testid="play-clip"
          title="Проиграть момент от входа до выхода (P)"
        >
          <SkipBack className="h-3.5 w-3.5" aria-hidden="true" />
          Момент
        </Button>
        <Button
          variant={loop ? "default" : "ghost"}
          size="icon-sm"
          onClick={toggleLoop}
          aria-pressed={loop}
          aria-label="Петля момента"
          title="Петля момента"
        >
          <Repeat className="h-3.5 w-3.5" aria-hidden="true" />
        </Button>
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={() => setMuted((value) => !value)}
          aria-pressed={muted}
          aria-label={muted ? "Включить звук" : "Выключить звук"}
        >
          {muted ? (
            <VolumeX className="h-3.5 w-3.5" aria-hidden="true" />
          ) : (
            <Volume2 className="h-3.5 w-3.5" aria-hidden="true" />
          )}
        </Button>
        <ClockReadout clock={clock} />
        <span
          className="ml-auto inline-flex items-center gap-0.5 rounded-md border border-border p-0.5"
          role="group"
          aria-label="Вид плеера"
        >
          <button
            type="button"
            aria-pressed={!vertical}
            onClick={() => setView("wide")}
            className={cn(
              "flex h-6 items-center gap-1 rounded-sm px-2 text-xs",
              !vertical ? "bg-accent text-accent-fg" : "text-fg-muted hover:text-fg",
            )}
          >
            <RectangleHorizontal className="h-3 w-3" aria-hidden="true" />
            Исходник
          </button>
          <button
            type="button"
            aria-pressed={vertical}
            onClick={() => setView("vertical")}
            className={cn(
              "flex h-6 items-center gap-1 rounded-sm px-2 text-xs",
              vertical ? "bg-accent text-accent-fg" : "text-fg-muted hover:text-fg",
            )}
          >
            <RectangleVertical className="h-3 w-3" aria-hidden="true" />
            9:16
          </button>
        </span>
      </div>
    </div>
  );
}
