"use client";

import { type KeyboardEvent, type PointerEvent, useRef } from "react";
import { useStore } from "zustand";

import type { PlayerTimeStore } from "@/components/player/player-time";
import { formatTimecode, HOOK_WINDOW_SEC } from "@/components/review/review-logic";
import type { Schemas } from "@/lib/api/client";
import { cn } from "@/lib/cn";

type BannerWindow = Schemas["BannerWindow"];

export interface PlayerTimelineProps {
  timeStore: PlayerTimeStore;
  /** Длительность из API; если видео дало свою — берётся она. */
  durationHint?: number | null;
  sceneCuts?: readonly number[];
  bannerWindows?: readonly BannerWindow[];
  hookSec?: number;
  onSeek: (sec: number) => void;
  className?: string;
}

function pct(value: number, duration: number): string {
  if (!(duration > 0)) return "0%";
  return `${Math.max(0, Math.min(100, (value / duration) * 100))}%`;
}

const WINDOW_STYLE: Record<BannerWindow["group"], { bar: string; label: string }> = {
  persistent: { bar: "bg-positive/40", label: "весь ролик" },
  with_creative: { bar: "bg-human", label: "креатив" },
};

/**
 * Дорожка под плеером (§7.3): склейки сцен, окно хука, окна баннера по группам, указатель времени.
 * Клик или перетаскивание — перемотка; клавиши ←/→ работают на всём экране приёмки.
 */
export function PlayerTimeline({
  timeStore,
  durationHint,
  sceneCuts = [],
  bannerWindows = [],
  hookSec = HOOK_WINDOW_SEC,
  onSeek,
  className,
}: PlayerTimelineProps) {
  const trackRef = useRef<HTMLDivElement>(null);
  const t = useStore(timeStore, (state) => state.t);
  const videoDuration = useStore(timeStore, (state) => state.duration);
  const duration = videoDuration > 0 ? videoDuration : (durationHint ?? 0);
  const dragging = useRef(false);

  const seekFromPointer = (event: PointerEvent<HTMLDivElement>) => {
    const track = trackRef.current;
    if (!track || !(duration > 0)) return;
    const box = track.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (event.clientX - box.left) / box.width));
    onSeek(ratio * duration);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Home") {
      event.preventDefault();
      onSeek(0);
    } else if (event.key === "End" && duration > 0) {
      event.preventDefault();
      onSeek(Math.max(0, duration - 0.05));
    }
  };

  const groups = (["with_creative", "persistent"] as const).filter((group) =>
    bannerWindows.some((w) => w.group === group),
  );

  return (
    <div className={cn("flex flex-col gap-1", className)} data-testid="player-timeline">
      <div className="flex items-center justify-between font-mono text-2xs text-fg-muted tabular">
        <span aria-hidden="true">{formatTimecode(t)}</span>
        <span className="flex items-center gap-3">
          <span className="inline-flex items-center gap-1">
            <span className="h-2 w-2 bg-warning/60" aria-hidden="true" />
            хук {hookSec} с
          </span>
          {groups.map((group) => (
            <span key={group} className="inline-flex items-center gap-1">
              <span className={cn("h-2 w-2", WINDOW_STYLE[group].bar)} aria-hidden="true" />
              баннер: {WINDOW_STYLE[group].label}
            </span>
          ))}
          <span className="inline-flex items-center gap-1">
            <span className="h-2 w-px bg-fg" aria-hidden="true" />
            склейки
          </span>
        </span>
        <span aria-hidden="true">{formatTimecode(duration || null)}</span>
      </div>
      <div
        ref={trackRef}
        role="slider"
        tabIndex={0}
        aria-label="Позиция в ролике"
        aria-valuemin={0}
        aria-valuemax={Math.round(duration * 100) / 100}
        aria-valuenow={Math.round(t * 100) / 100}
        aria-valuetext={`${formatTimecode(t)} из ${formatTimecode(duration || null)}`}
        onKeyDown={onKeyDown}
        onPointerDown={(event) => {
          dragging.current = true;
          event.currentTarget.setPointerCapture(event.pointerId);
          seekFromPointer(event);
        }}
        onPointerMove={(event) => {
          if (dragging.current) seekFromPointer(event);
        }}
        onPointerUp={(event) => {
          dragging.current = false;
          event.currentTarget.releasePointerCapture(event.pointerId);
        }}
        className="relative h-12 cursor-pointer touch-none select-none rounded-sm border border-border bg-surface-2"
      >
        {/* окно хука */}
        <div
          className="absolute inset-y-0 left-0 bg-warning/20"
          style={{ width: pct(Math.min(hookSec, duration), duration) }}
          aria-hidden="true"
        />
        {/* склейки сцен */}
        {sceneCuts.map((cut, index) => (
          <div
            key={`${cut}-${index}`}
            className="absolute top-0 h-5 w-px bg-fg/70"
            style={{ left: pct(cut, duration) }}
            aria-hidden="true"
          />
        ))}
        {/* окна баннера: креатив сверху, «весь ролик» снизу */}
        {bannerWindows.map((window, index) => (
          <div
            key={`${window.group}-${window.start_sec}-${index}`}
            data-group={window.group}
            className={cn(
              "absolute h-2.5 rounded-[1px]",
              WINDOW_STYLE[window.group].bar,
              window.group === "with_creative" ? "bottom-4" : "bottom-1",
            )}
            style={{
              left: pct(window.start_sec, duration),
              width: pct(Math.max(0, window.end_sec - window.start_sec), duration),
            }}
            title={`Баннер (${WINDOW_STYLE[window.group].label}): ${formatTimecode(window.start_sec)}–${formatTimecode(window.end_sec)}`}
          />
        ))}
        {/* указатель */}
        <div
          className="pointer-events-none absolute inset-y-0 w-0.5 -translate-x-1/2 bg-accent"
          style={{ left: pct(t, duration) }}
          aria-hidden="true"
        />
      </div>
    </div>
  );
}
