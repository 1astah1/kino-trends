"use client";

import { cn } from "@/lib/cn";
import { useLiveStore } from "@/lib/store/live";

const META = {
  live: { label: "live", title: "События приходят в реальном времени", dot: "bg-positive tally-pulse" },
  polling: { label: "опрос", title: "Событий нет дольше 45 с: экраны обновляются раз в 10 с", dot: "bg-fg-muted/60" },
  connecting: { label: "связь…", title: "Подключение к потоку событий", dot: "bg-warning" },
} as const;

/** Tally-точка SSE (§7.3, §7.4): live — зелёная, опрос — серая. Цвет + подпись. */
export function LiveDot() {
  const status = useLiveStore((state) => state.status);
  const meta = META[status];
  return (
    <div
      data-testid="live-dot"
      data-state={status}
      title={meta.title}
      className="inline-flex h-7 items-center gap-1.5 rounded-md px-1.5 font-mono text-2xs tracking-wider text-fg-muted uppercase"
    >
      <span className={cn("h-2 w-2 rounded-full", meta.dot)} aria-hidden="true" />
      <span className="hidden lg:inline">{meta.label}</span>
      <span className="sr-only" role="status" aria-live="polite">
        Поток событий: {meta.label}
      </span>
    </div>
  );
}
