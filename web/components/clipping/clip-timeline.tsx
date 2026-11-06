"use client";

import { Maximize2, ZoomIn, ZoomOut } from "lucide-react";
import { type MouseEvent as ReactMouseEvent, useEffect, useMemo, useRef, useState } from "react";
import { useStore } from "zustand";

import { Skeleton } from "@/components/ui";
import type { Schemas } from "@/lib/api/client";
import { mediaSrc } from "@/lib/api/media";
import { cn } from "@/lib/cn";
import { type ClipClock, type ClipPlayerController, type TimelineZoom, useClipEditor } from "@/lib/store/clip-editor";
import { useThemeStore } from "@/lib/store/theme";

import { formatTc, type StoryboardGrid, storyboardTile, type TranscriptWord, type WordRange } from "./clip-math";

type SourcePeaks = Schemas["SourcePeaks"];
type SceneSpan = Schemas["SceneSpan"];

const WAVE_HEIGHT = 72;
const RULER_HEIGHT = 16;
const WORDS_HEIGHT = 22;
const STRIP_HEIGHT = 36;
const REGION_ID = "clip";
/** Слова рисуем, когда на секунду приходится хотя бы столько пикселей; подпись — если слово шире 18 px. */
const WORDS_MIN_PPS = 8;
const WORD_LABEL_MIN_PX = 18;
const TICK_STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200, 1800, 3600];

interface WaveApi {
  zoom: (pps: number) => void;
  setScrollTime: (time: number) => void;
  getScroll: () => number;
  getWidth: () => number;
  getWrapper: () => HTMLElement;
  destroy: () => void;
}

interface RegionApi {
  start: number;
  end: number;
  setOptions: (options: { start?: number; end?: number; color?: string; resize?: boolean }) => void;
}

interface View {
  scroll: number;
  pps: number;
  width: number;
}

function cssVar(name: string, fallback: string): string {
  if (typeof document === "undefined") return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

function tickStep(pps: number): number {
  for (const step of TICK_STEPS) if (step * pps >= 64) return step;
  return TICK_STEPS[TICK_STEPS.length - 1];
}

/** Масштаб и начало окна под режим: момент ± поля, окно 5 минут или весь источник. */
function zoomTarget(zoom: TimelineZoom, width: number, duration: number, range: { start: number; end: number } | null) {
  if (!duration || width <= 0) return { pps: 0, scrollTo: 0 };
  if (zoom === "full" || !range) return { pps: width / duration, scrollTo: 0 };
  const len = Math.max(1, range.end - range.start);
  if (zoom === "wide") {
    const windowSec = Math.min(duration, Math.max(300, len * 4));
    const pps = width / windowSec;
    return { pps, scrollTo: Math.max(0, range.start - (windowSec - len) / 2) };
  }
  const pad = Math.max(4, len * 0.25);
  const pps = width / (len + pad * 2);
  return { pps, scrollTo: Math.max(0, range.start - pad) };
}

function Playhead({ clock, view }: { clock: ClipClock; view: View }) {
  const t = useStore(clock, (state) => state.t);
  const x = t * view.pps - view.scroll;
  if (x < -2 || x > view.width + 2) return null;
  return (
    <div
      className="pointer-events-none absolute inset-y-0 z-20 w-px bg-human"
      style={{ transform: `translateX(${x}px)` }}
      aria-hidden="true"
    />
  );
}

/** Следим, чтобы при проигрывании головка оставалась в окне. */
function useAutoScroll(clock: ClipClock, wave: WaveApi | null, view: View) {
  useEffect(() => {
    if (!wave || !view.pps) return;
    return clock.subscribe((state, prev) => {
      if (!state.playing || state.t === prev.t) return;
      const x = state.t * view.pps - view.scroll;
      if (x < 0 || x > view.width * 0.92) wave.setScrollTime(Math.max(0, state.t - (view.width * 0.1) / view.pps));
    });
  }, [clock, wave, view]);
}

/**
 * ClipTimeline (§7.3: «дорожка wavesurfer — волна, слова, склейки, регионы, прилипание, hover-кадр из
 * спрайта»). Волна — wavesurfer.js 7.12.12 по готовым peaks (без декодирования звука), регион клипа
 * тянется за края и прилипает к словам; слова, склейки, раскадровка и линейка — свои слои поверх,
 * синхронные с прокруткой и масштабом волны. wavesurfer грузится только на этом экране.
 */
export function ClipTimeline({
  peaks,
  duration,
  words,
  range,
  draftRange,
  scenes,
  storyboard,
  clock,
  controller,
  onRegionChange,
  onWordClick,
  editable,
}: {
  peaks: SourcePeaks | undefined;
  duration: number;
  words: readonly TranscriptWord[];
  /** Границы клипа в секундах (с учётом неприменённой правки). */
  range: { start: number; end: number } | null;
  draftRange: WordRange | null;
  scenes: readonly SceneSpan[];
  storyboard: StoryboardGrid | null;
  clock: ClipClock;
  controller: ClipPlayerController | null;
  onRegionChange: (start: number, end: number) => void;
  onWordClick: (index: number, extend: boolean) => void;
  editable: boolean;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const waveRef = useRef<WaveApi | null>(null);
  const regionRef = useRef<RegionApi | null>(null);
  const handlersRef = useRef({ onRegionChange, controller, editable });
  const [wave, setWave] = useState<WaveApi | null>(null);
  const [view, setView] = useState<View>({ scroll: 0, pps: 0, width: 0 });
  const [hover, setHover] = useState<{ x: number; t: number } | null>(null);
  const [failed, setFailed] = useState(false);
  const zoom = useClipEditor((state) => state.zoom);
  const setZoom = useClipEditor((state) => state.setZoom);
  const theme = useThemeStore((state) => state.theme);

  useEffect(() => {
    handlersRef.current = { onRegionChange, controller, editable };
  });

  // Создание волны: один раз на источник (peaks уже посчитаны при подготовке).
  useEffect(() => {
    const container = containerRef.current;
    if (!container || !peaks || !duration) return;
    let cancelled = false;
    let instance: { destroy: () => void } | null = null;
    (async () => {
      try {
        const [{ default: WaveSurfer }, { default: RegionsPlugin }] = await Promise.all([
          import("wavesurfer.js"),
          import("wavesurfer.js/plugins/regions"),
        ]);
        if (cancelled) return;
        const regions = RegionsPlugin.create();
        const muted = cssVar("--fg-muted", "#888");
        const ws = WaveSurfer.create({
          container,
          height: WAVE_HEIGHT,
          // Звук не декодируем и не играем: волна — только рисунок по peaks, время ведут часы студии.
          media: document.createElement("audio"),
          peaks: peaks.peaks,
          duration,
          waveColor: muted,
          progressColor: muted,
          cursorWidth: 0,
          normalize: true,
          interact: true,
          dragToSeek: false,
          autoScroll: false,
          autoCenter: false,
          hideScrollbar: false,
          minPxPerSec: 1,
          plugins: [regions],
        });
        instance = ws;
        const measure = () => {
          if (cancelled) return;
          const wrapper = ws.getWrapper();
          const width = ws.getWidth();
          const pps = wrapper.scrollWidth && duration ? wrapper.scrollWidth / duration : 0;
          setView({ scroll: ws.getScroll(), pps, width });
        };
        ws.on("interaction", (time) => handlersRef.current.controller?.seek(time));
        ws.on("scroll", (_start, _end, left) =>
          setView((prev) => (prev.scroll === left ? prev : { ...prev, scroll: left })),
        );
        ws.on("zoom", () => requestAnimationFrame(measure));
        ws.on("redraw", measure);
        ws.on("resize", measure);
        ws.on("error", () => {
          if (!cancelled) setFailed(true);
        });
        regions.on("region-updated", (region) => {
          if (region.id !== REGION_ID) return;
          handlersRef.current.onRegionChange(region.start, region.end);
        });
        // Масштаб и регион — только после того, как волна построена по peaks (иначе zoom бросает).
        ws.once("ready", () => {
          if (cancelled) return;
          regionRef.current = regions.addRegion({
            id: REGION_ID,
            start: 0,
            end: Math.min(duration, 1),
            drag: false,
            resize: handlersRef.current.editable,
            color: "rgba(0,0,0,0)",
            minLength: 1,
          }) as unknown as RegionApi;
          waveRef.current = ws as unknown as WaveApi;
          measure();
          setWave(ws as unknown as WaveApi);
        });
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => {
      cancelled = true;
      instance?.destroy();
      waveRef.current = null;
      regionRef.current = null;
      setWave(null);
    };
  }, [peaks, duration]);

  // Цвета из токенов темы (canvas не понимает var(--…)).
  useEffect(() => {
    const ws = waveRef.current as unknown as { setOptions?: (o: Record<string, string>) => void } | null;
    if (!ws?.setOptions) return;
    const muted = cssVar("--fg-muted", "#888");
    ws.setOptions({ waveColor: muted, progressColor: muted });
    const accent = cssVar("--accent", "#39c");
    regionRef.current?.setOptions({ color: `color-mix(in oklch, ${accent} 22%, transparent)` });
  }, [theme, wave]);

  // Регион клипа следует за границами (после прилипания к словам или применения на сервере).
  useEffect(() => {
    const region = regionRef.current;
    if (!region || !wave) return;
    const accent = cssVar("--accent", "#39c");
    if (!range) {
      region.setOptions({ start: 0, end: 0.01, color: "rgba(0,0,0,0)" });
      return;
    }
    if (Math.abs(region.start - range.start) > 0.005 || Math.abs(region.end - range.end) > 0.005) {
      region.setOptions({ start: range.start, end: range.end });
    }
    region.setOptions({ color: `color-mix(in oklch, ${accent} 22%, transparent)` });
  }, [range, wave]);

  useEffect(() => {
    regionRef.current?.setOptions({ resize: editable });
  }, [editable, wave]);

  // Масштаб под режим и под выбранный момент.
  const rangeKey = range ? `${range.start.toFixed(2)}-${range.end.toFixed(2)}` : "none";
  const lastFitRef = useRef<string>("");
  useEffect(() => {
    const ws = waveRef.current;
    const container = containerRef.current;
    if (!ws || !wave || !container) return;
    const fitKey = `${zoom}|${range ? Math.round(range.start) : "none"}`;
    // Правка края не должна дёргать масштаб: перестраиваемся на смену режима или момента.
    if (lastFitRef.current === fitKey) return;
    lastFitRef.current = fitKey;
    const width = container.clientWidth;
    const target = zoomTarget(zoom, width, duration, range);
    if (!target.pps) return;
    ws.zoom(target.pps);
    requestAnimationFrame(() => {
      ws.setScrollTime(target.scrollTo);
      setView({ scroll: ws.getScroll(), pps: ws.getWrapper().scrollWidth / duration, width: ws.getWidth() });
    });
  }, [zoom, rangeKey, wave, duration, range]);

  useAutoScroll(clock, wave, view);

  const visible = useMemo(() => {
    if (!view.pps) return { start: 0, end: 0 };
    return { start: view.scroll / view.pps, end: (view.scroll + view.width) / view.pps };
  }, [view]);

  const visibleWords = useMemo(() => {
    if (!view.pps || view.pps < WORDS_MIN_PPS) return [];
    const out: Array<{ index: number; word: TranscriptWord }> = [];
    // Слов десятки тысяч: ищем начало окна бинарным поиском.
    let lo = 0;
    let hi = words.length - 1;
    const from = visible.start - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (words[mid].e < from) lo = mid + 1;
      else hi = mid;
    }
    for (let i = lo; i < words.length && words[i].s <= visible.end + 1; i += 1) out.push({ index: i, word: words[i] });
    return out;
  }, [words, visible, view.pps]);

  const cuts = useMemo(
    () =>
      scenes
        .slice(1)
        .map((scene) => scene.start_sec)
        .filter((t) => t >= visible.start - 1 && t <= visible.end + 1),
    [scenes, visible],
  );

  const ticks = useMemo(() => {
    if (!view.pps) return [];
    const step = tickStep(view.pps);
    const out: number[] = [];
    for (let t = Math.floor(visible.start / step) * step; t <= visible.end; t += step) if (t >= 0) out.push(t);
    return out;
  }, [view.pps, visible]);

  const tiles = useMemo(() => {
    if (!storyboard || !view.pps || !storyboard.count) return [];
    const tileW = (STRIP_HEIGHT * storyboard.tile_w) / storyboard.tile_h;
    const span = storyboard.interval_sec * view.pps;
    const every = Math.max(1, Math.ceil(tileW / Math.max(span, 1)));
    const first = Math.max(0, Math.floor(visible.start / storyboard.interval_sec));
    const last = Math.min(storyboard.count - 1, Math.ceil(visible.end / storyboard.interval_sec));
    const out: Array<{ t: number; index: number }> = [];
    for (let i = first - (first % every); i <= last; i += every) out.push({ t: i * storyboard.interval_sec, index: i });
    return out;
  }, [storyboard, view.pps, visible]);

  const onHover = (event: ReactMouseEvent<HTMLDivElement>) => {
    if (!view.pps) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const x = event.clientX - rect.left;
    setHover({ x, t: (view.scroll + x) / view.pps });
  };

  const hoverTile = hover && storyboard ? storyboardTile(storyboard, hover.t) : null;
  const inRange = (index: number) => !!draftRange && index >= draftRange.w0 && index <= draftRange.w1;
  const x = (t: number) => t * view.pps - view.scroll;

  const zoomButton = (value: TimelineZoom, label: string, Icon: typeof ZoomIn, hint: string) => (
    <button
      type="button"
      aria-pressed={zoom === value}
      onClick={() => {
        lastFitRef.current = "";
        setZoom(value);
      }}
      title={hint}
      className={cn(
        "flex h-6 items-center gap-1 rounded-sm px-2 text-xs",
        zoom === value ? "bg-accent text-accent-fg" : "text-fg-muted hover:text-fg",
      )}
    >
      <Icon className="h-3 w-3" aria-hidden="true" />
      {label}
    </button>
  );

  return (
    <div className="flex flex-col gap-1" data-testid="clip-timeline">
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-2xs tracking-wider text-fg-muted uppercase">Дорожка</span>
        <span
          className="inline-flex items-center gap-0.5 rounded-md border border-border p-0.5"
          role="group"
          aria-label="Масштаб дорожки"
        >
          {zoomButton("clip", "Момент", ZoomIn, "Момент с полями (Z)")}
          {zoomButton("wide", "5 мин", ZoomOut, "Окно 5 минут вокруг момента")}
          {zoomButton("full", "Весь", Maximize2, "Весь источник")}
        </span>
      </div>
      <div className="relative select-none rounded-md border border-border bg-surface">
        {/* линейка */}
        <div
          className="relative overflow-hidden border-b border-border"
          style={{ height: RULER_HEIGHT }}
          aria-hidden="true"
        >
          {ticks.map((t) => (
            <span
              key={t}
              className="absolute top-0 h-full border-l border-border pl-1 font-mono text-[10px] leading-4 text-fg-muted"
              style={{ transform: `translateX(${x(t)}px)` }}
            >
              {formatTc(t, { withCs: false })}
            </span>
          ))}
        </div>
        {/* волна */}
        <div
          className="relative"
          onMouseMove={onHover}
          onMouseLeave={() => setHover(null)}
          style={{ minHeight: WAVE_HEIGHT }}
        >
          <div ref={containerRef} data-clip-wave="" />
          {!peaks ? (
            <div className="absolute inset-0 flex items-center justify-center">
              {failed ? (
                <span className="text-xs text-fg-muted">Волна не загрузилась</span>
              ) : (
                <Skeleton className="h-10 w-[96%]" />
              )}
            </div>
          ) : null}
          <div className="pointer-events-none absolute inset-0 overflow-hidden">
            {cuts.map((t) => (
              <div
                key={t}
                className="absolute inset-y-0 w-px bg-machine/70"
                style={{ transform: `translateX(${x(t)}px)` }}
                title={`склейка ${formatTc(t)}`}
              />
            ))}
            {range && view.pps ? (
              <>
                <div
                  className="absolute inset-y-0 w-0.5 bg-accent"
                  style={{ transform: `translateX(${x(range.start)}px)` }}
                />
                <div
                  className="absolute inset-y-0 w-0.5 bg-accent"
                  style={{ transform: `translateX(${x(range.end)}px)` }}
                />
              </>
            ) : null}
            <Playhead clock={clock} view={view} />
          </div>
          {hover && view.pps ? (
            <div
              className="pointer-events-none absolute bottom-full z-30 mb-1 flex -translate-x-1/2 flex-col items-center gap-0.5"
              style={{ left: Math.min(Math.max(hover.x, 70), view.width - 70) }}
            >
              {hoverTile ? (
                <span
                  className="block border border-border bg-video"
                  style={{
                    width: 128,
                    height: (128 * (storyboard?.tile_h ?? 9)) / (storyboard?.tile_w ?? 16),
                    backgroundImage: mediaSrc(hoverTile.sheet) ? `url(${mediaSrc(hoverTile.sheet)})` : undefined,
                    backgroundSize: `${(hoverTile.sheetW * 128) / (storyboard?.tile_w ?? 160)}px auto`,
                    backgroundPosition: `-${(hoverTile.x * 128) / (storyboard?.tile_w ?? 160)}px -${(hoverTile.y * 128) / (storyboard?.tile_w ?? 160)}px`,
                  }}
                />
              ) : null}
              <span className="rounded-sm bg-surface px-1 font-mono text-[10px] text-fg">{formatTc(hover.t)}</span>
            </div>
          ) : null}
        </div>
        {/* слова */}
        <div
          className="relative overflow-hidden border-t border-border"
          style={{ height: WORDS_HEIGHT }}
          data-testid="timeline-words"
        >
          {view.pps && view.pps < WORDS_MIN_PPS ? (
            <span className="absolute inset-0 flex items-center px-2 text-2xs text-fg-muted">
              Приблизьте, чтобы увидеть слова
            </span>
          ) : null}
          {visibleWords.map(({ index, word }) => (
            <button
              key={index}
              type="button"
              tabIndex={-1}
              onClick={(event) => {
                controller?.seek(word.s);
                onWordClick(index, event.shiftKey);
              }}
              className={cn(
                "absolute top-0.5 h-[18px] truncate rounded-sm border-l border-border px-0.5 text-left text-[11px] leading-[18px]",
                inRange(index) ? "bg-accent/20 text-fg" : "bg-surface-2/60 text-fg-muted hover:bg-surface-2",
              )}
              style={{
                transform: `translateX(${x(word.s)}px)`,
                width: Math.max(4, (word.e - word.s) * view.pps),
              }}
              title={`${word.w} · ${formatTc(word.s)}`}
            >
              {(word.e - word.s) * view.pps >= WORD_LABEL_MIN_PX ? word.w : ""}
            </button>
          ))}
        </div>
        {/* раскадровка */}
        {storyboard ? (
          <div
            className="relative overflow-hidden border-t border-border bg-video"
            style={{ height: STRIP_HEIGHT }}
            aria-hidden="true"
          >
            {tiles.map(({ t }) => {
              const tile = storyboardTile(storyboard, t);
              const sheet = tile ? mediaSrc(tile.sheet) : undefined;
              if (!tile || !sheet) return null;
              const scale = STRIP_HEIGHT / storyboard.tile_h;
              return (
                <span
                  key={tile.index}
                  className="absolute top-0 block border-r border-black"
                  style={{
                    transform: `translateX(${x(t)}px)`,
                    width: storyboard.tile_w * scale,
                    height: STRIP_HEIGHT,
                    backgroundImage: `url(${sheet})`,
                    backgroundSize: `${tile.sheetW * scale}px ${tile.sheetH * scale}px`,
                    backgroundPosition: `-${tile.x * scale}px -${tile.y * scale}px`,
                  }}
                />
              );
            })}
          </div>
        ) : null}
      </div>
      <p className="text-2xs text-fg-muted">
        Тяните края подсветки — границы прилипнут к словам. Клик по волне — перемотка, по слову — выделение (Shift — до
        этого слова).
      </p>
    </div>
  );
}
