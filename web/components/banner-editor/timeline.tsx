"use client";

import { type KeyboardEvent, type PointerEvent as ReactPointerEvent, useRef } from "react";

import { cn } from "@/lib/cn";
import { clamp, type TimeSpan } from "@/lib/safe-zones";

import type { BannerTiming } from "./draft";

function fmt(sec: number): string {
  return sec.toFixed(1).replace(".", ",");
}

/**
 * Дорожка времени ролика: тишина хука, допустимое окно импульса (%), окна показа креатива, весь ролик
 * для постоянных слоёв и бегунок момента t (им же выбирается кадр холста и «Момент t» рендера).
 */
export function Timeline({
  duration,
  timing,
  windows,
  t,
  onSeek,
  hasPersistent,
}: {
  duration: number;
  timing: BannerTiming;
  windows: TimeSpan[];
  t: number;
  onSeek: (t: number) => void;
  hasPersistent: boolean;
}) {
  const trackRef = useRef<HTMLDivElement>(null);
  const draggingRef = useRef(false);
  const d = Math.max(0.1, duration);
  const pct = (sec: number) => `${clamp((sec / d) * 100, 0, 100)}%`;

  const seekFromPointer = (clientX: number) => {
    const track = trackRef.current;
    if (!track) return;
    const rect = track.getBoundingClientRect();
    const ratio = clamp((clientX - rect.left) / rect.width, 0, 1);
    onSeek(Math.round(ratio * d * 10) / 10);
  };

  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    draggingRef.current = true;
    event.currentTarget.setPointerCapture(event.pointerId);
    seekFromPointer(event.clientX);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = event.shiftKey ? 1 : 0.1;
    let next: number | null = null;
    if (event.key === "ArrowLeft" || event.key === "ArrowDown") next = t - step;
    else if (event.key === "ArrowRight" || event.key === "ArrowUp") next = t + step;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = d;
    else if (event.key === "PageDown") next = t + 2;
    else if (event.key === "PageUp") next = t - 2;
    if (next === null) return;
    event.preventDefault();
    onSeek(Math.round(clamp(next, 0, d) * 10) / 10);
  };

  const ticks: number[] = [];
  const tickStep = d > 40 ? 10 : d > 15 ? 5 : 2;
  for (let s = 0; s <= d + 1e-6; s += tickStep) ticks.push(s);
  const impulse = timing.mode === "impulse";
  const [lo, hi] = timing.impulse_window_pct;

  return (
    <div className="flex flex-col gap-1" data-testid="banner-timeline">
      <div className="flex items-center justify-between text-2xs text-fg-muted">
        <span>Дорожка ролика · {fmt(d)} с</span>
        <span className="tabular font-mono text-fg">t = {fmt(t)} с</span>
      </div>
      <div
        ref={trackRef}
        role="slider"
        tabIndex={0}
        aria-label="Момент ролика для холста и кадра"
        aria-valuemin={0}
        aria-valuemax={d}
        aria-valuenow={t}
        aria-valuetext={`${fmt(t)} с`}
        className="relative h-14 cursor-pointer touch-none rounded-md border border-border bg-video select-none"
        onPointerDown={onPointerDown}
        onPointerMove={(event) => {
          if (draggingRef.current) seekFromPointer(event.clientX);
        }}
        onPointerUp={(event) => {
          draggingRef.current = false;
          event.currentTarget.releasePointerCapture(event.pointerId);
        }}
        onKeyDown={onKeyDown}
      >
        {/* Дорожка 1: постоянные слои (микро-бренд) */}
        <div className="absolute inset-x-0 top-1.5 h-3">
          {hasPersistent ? (
            <div
              className="absolute inset-y-0 rounded-sm bg-fg-muted/35"
              style={{ left: 0, width: "100%" }}
              title="Постоянные слои: весь ролик"
            />
          ) : null}
        </div>
        {/* Дорожка 2: креатив — хук, допустимое окно, окна показа */}
        <div className="absolute inset-x-0 top-6 h-5">
          {impulse ? (
            <>
              <div
                className="absolute inset-y-0 bg-[repeating-linear-gradient(135deg,var(--warning)_0_2px,transparent_2px_7px)] opacity-40"
                style={{ left: 0, width: pct(timing.hook_silence_sec) }}
                title={`Тишина хука: первые ${fmt(timing.hook_silence_sec)} с без оффера`}
              />
              <div
                className="absolute inset-y-0 rounded-sm border border-dashed border-accent/60"
                style={{ left: pct(d * lo), width: `calc(${pct(d * hi)} - ${pct(d * lo)})` }}
                title={`Допустимое окно импульса: ${Math.round(lo * 100)}–${Math.round(hi * 100)}% ролика`}
              />
            </>
          ) : null}
          {windows.map((w, index) => (
            <div
              key={index}
              data-testid="timeline-window"
              className="absolute inset-y-0.5 rounded-sm bg-accent"
              style={{ left: pct(w.start_sec), width: `calc(${pct(w.end_sec)} - ${pct(w.start_sec)})` }}
              title={`Креатив: ${fmt(w.start_sec)}–${fmt(w.end_sec)} с`}
            />
          ))}
        </div>
        {/* Засечки */}
        <div className="pointer-events-none absolute inset-x-0 bottom-0 h-3">
          {ticks.map((s) => (
            <span
              key={s}
              className="absolute bottom-0 font-mono text-[9px] leading-3 text-fg-muted"
              style={{
                left: pct(s),
                transform: s === 0 ? undefined : s >= d - 0.01 ? "translateX(-100%)" : "translateX(-50%)",
              }}
            >
              {Math.round(s)}
            </span>
          ))}
        </div>
        {/* Бегунок */}
        <div className="pointer-events-none absolute inset-y-0 w-px bg-human" style={{ left: pct(t) }}>
          <span className="absolute -top-1 left-1/2 h-2 w-2 -translate-x-1/2 rotate-45 bg-human" />
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-3 text-2xs text-fg-muted">
        <Legend className="bg-accent" label="креатив" />
        {hasPersistent ? <Legend className="bg-fg-muted/35" label="весь ролик" /> : null}
        {impulse ? <Legend className="border border-dashed border-accent/60" label="окно импульса" /> : null}
        {impulse ? <Legend className="bg-warning/40" label="тишина хука" /> : null}
        <Legend className="bg-human" label="момент t" />
      </div>
    </div>
  );
}

function Legend({ className, label }: { className: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      <span className={cn("inline-block h-2 w-3 rounded-[2px]", className)} aria-hidden="true" />
      {label}
    </span>
  );
}
