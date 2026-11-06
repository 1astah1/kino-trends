"use client";

import { useVirtualizer } from "@tanstack/react-virtual";
import { ArrowLeftToLine, ArrowRightToLine, Plus, Search, SquareSplitHorizontal, X } from "lucide-react";
import { Fragment, memo, useEffect, useMemo, useRef, useState } from "react";
import { useStore } from "zustand";

import { Button, Input, Skeleton } from "@/components/ui";
import { cn } from "@/lib/cn";
import { type ClipClock, type ClipPlayerController, useClipEditor } from "@/lib/store/clip-editor";

import {
  activeWordAt,
  formatSeconds,
  formatTc,
  rangeTimes,
  sentenceIndexOfWord,
  type TranscriptSentence,
  type TranscriptWord,
  type WordRange,
} from "./clip-math";

interface RowProps {
  sentence: TranscriptSentence;
  words: readonly TranscriptWord[];
  clock: ClipClock;
  range: WordRange | null;
  selection: WordRange | null;
  insertLabel: string | null;
  highlighted: boolean;
  onWord: (index: number, extend: boolean) => void;
  onSeek: (sec: number) => void;
}

const SentenceRow = memo(function SentenceRow({
  sentence,
  words,
  clock,
  range,
  selection,
  insertLabel,
  highlighted,
  onWord,
  onSeek,
}: RowProps) {
  // Подписка только на «какое слово этой строки звучит»: строки не перерисовываются на каждый тик.
  const active = useStore(clock, (state) => {
    const i = activeWordAt(words, state.t);
    return i >= sentence.w0 && i <= sentence.w1 ? i : -1;
  });
  const last = Math.min(sentence.w1, words.length - 1);
  const indices = Array.from({ length: Math.max(0, last - sentence.w0 + 1) }, (_, k) => sentence.w0 + k);
  const items = indices.map((i) => {
    const word = words[i];
    const inClip = !!range && i >= range.w0 && i <= range.w1;
    const selected = !!selection && i >= selection.w0 && i <= selection.w1;
    const isEnd = !!range && i === range.w1;
    return (
      <Fragment key={i}>
        <span
          className={cn(
            "inline",
            inClip && "bg-accent/15",
            inClip && i === range?.w0 && "rounded-l-sm",
            isEnd && "rounded-r-sm",
          )}
        >
          {range && i === range.w0 ? (
            <span className="mr-0.5 font-mono font-bold text-accent" aria-label="вход">
              [
            </span>
          ) : null}
          <button
            type="button"
            tabIndex={-1}
            data-word={i}
            onClick={(event) => {
              onSeek(word.s);
              onWord(i, event.shiftKey);
            }}
            title={formatTc(word.s)}
            className={cn(
              "rounded-sm px-[1px] transition-colors duration-100",
              i === active
                ? "bg-human text-human-fg"
                : selected
                  ? "bg-machine/30 text-fg"
                  : inClip
                    ? "text-fg hover:bg-surface-2"
                    : "text-fg-muted hover:bg-surface-2 hover:text-fg",
            )}
          >
            {word.w}
          </button>
          {isEnd ? (
            <span className="ml-0.5 font-mono font-bold text-accent" aria-label="выход">
              ]
            </span>
          ) : null}
          {/* Пробел внутри подсветки клипа (сплошная заливка), после выхода — снаружи. */}
          {isEnd ? null : " "}
        </span>
        {isEnd ? " " : null}
      </Fragment>
    );
  });
  return (
    <div className={cn("flex gap-2 px-3 py-1.5", highlighted && "bg-warning/10")} data-sid={sentence.sid}>
      <button
        type="button"
        tabIndex={-1}
        onClick={() => onSeek(sentence.s)}
        className="mt-0.5 shrink-0 font-mono text-[10px] text-fg-muted tabular hover:text-accent"
        title="Перейти к предложению"
      >
        {formatTc(sentence.s, { withCs: false })}
      </button>
      <p className="min-w-0 text-sm leading-6">
        {items}
        {insertLabel ? (
          <span className="ml-1 rounded-sm bg-human/20 px-1 font-mono text-[10px] text-human">↳ {insertLabel}</span>
        ) : null}
      </p>
    </div>
  );
});

/**
 * TranscriptPanel (§7.3: «виртуализированный транскрипт с монтажом по тексту»). Клик по слову —
 * перемотка и выделение, Shift+клик — выделение до этого слова. Выделение ставит вход, выход,
 * обе границы или создаёт ручной момент. Подсвечены: клип (циан), выделение (фиолет), звучащее
 * слово (янтарь), вставки авторского слоя.
 */
export function TranscriptPanel({
  words,
  sentences,
  isLoading,
  clock,
  controller,
  range,
  scrollToWord,
  insertMarks,
  editable,
  canCreate,
  onSetIn,
  onSetOut,
  onSetBounds,
  onNewMoment,
}: {
  words: readonly TranscriptWord[];
  sentences: readonly TranscriptSentence[];
  isLoading: boolean;
  clock: ClipClock;
  controller: ClipPlayerController | null;
  range: WordRange | null;
  /** Прокрутить к слову (смена момента): ключ меняется — прокрутка повторяется. */
  scrollToWord: { index: number; key: string } | null;
  insertMarks: Record<number, string>;
  editable: boolean;
  canCreate: boolean;
  onSetIn: (index: number) => void;
  onSetOut: (index: number) => void;
  onSetBounds: (range: WordRange) => void;
  onNewMoment: (range: WordRange) => void;
}) {
  const parentRef = useRef<HTMLDivElement>(null);
  const selection = useClipEditor((state) => state.selection);
  const selectWord = useClipEditor((state) => state.selectWord);
  const clearSelection = useClipEditor((state) => state.clearSelection);
  const [follow, setFollow] = useState(true);
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<number | null>(null);
  const userScrollAt = useRef(0);

  // eslint-disable-next-line react-hooks/incompatible-library -- useVirtualizer возвращает изменяемый объект; так задумано TanStack Virtual
  const virtualizer = useVirtualizer({
    count: sentences.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 44,
    overscan: 8,
  });

  // Смена момента: к его началу.
  const scrollKey = scrollToWord?.key ?? "";
  useEffect(() => {
    if (!scrollToWord || !sentences.length) return;
    const idx = sentenceIndexOfWord(sentences, scrollToWord.index);
    if (idx >= 0) virtualizer.scrollToIndex(Math.max(0, idx - 1), { align: "start" });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- только по смене ключа
  }, [scrollKey, sentences.length]);

  // Следование за воспроизведением (пока оператор сам не крутит список).
  useEffect(() => {
    if (!follow || !sentences.length) return;
    let lastIdx = -1;
    return clock.subscribe((state) => {
      if (!state.playing) return;
      if (Date.now() - userScrollAt.current < 4_000) return;
      const w = activeWordAt(words, state.t);
      if (w < 0) return;
      const idx = sentenceIndexOfWord(sentences, w);
      if (idx < 0 || idx === lastIdx) return;
      lastIdx = idx;
      const items = virtualizer.getVirtualItems();
      const first = items[0]?.index ?? 0;
      const last = items[items.length - 1]?.index ?? 0;
      if (idx <= first + 1 || idx >= last - 2) virtualizer.scrollToIndex(idx, { align: "center" });
    });
  }, [clock, follow, sentences, words, virtualizer]);

  const lowerSentences = useMemo(() => sentences.map((s) => s.text.toLowerCase()), [sentences]);
  const findNext = () => {
    const needle = query.trim().toLowerCase();
    if (!needle) return;
    const start = found === null ? 0 : found + 1;
    for (let step = 0; step < lowerSentences.length; step += 1) {
      const i = (start + step) % lowerSentences.length;
      if (lowerSentences[i].includes(needle)) {
        setFound(i);
        virtualizer.scrollToIndex(i, { align: "center" });
        return;
      }
    }
    setFound(null);
  };

  const onSeek = (sec: number) => controller?.seek(sec);
  const selTimes = selection ? rangeTimes(words, selection) : null;

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid="transcript-panel">
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-1.5">
        <span className="font-mono text-2xs tracking-wider text-fg-muted uppercase">Транскрипт</span>
        <span className="font-mono text-2xs text-fg-muted tabular">
          {words.length.toLocaleString("ru-RU")} слов · {sentences.length.toLocaleString("ru-RU")} предл.
        </span>
        <form
          className="relative ml-auto w-44"
          role="search"
          onSubmit={(event) => {
            event.preventDefault();
            findNext();
          }}
        >
          <Search
            className="pointer-events-none absolute top-1/2 left-2 h-3 w-3 -translate-y-1/2 text-fg-muted"
            aria-hidden="true"
          />
          <Input
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setFound(null);
            }}
            placeholder="Найти (Enter)"
            aria-label="Поиск по транскрипту"
            className="h-7 pl-6 text-xs"
          />
        </form>
        <label className="flex items-center gap-1 text-2xs text-fg-muted">
          <input type="checkbox" checked={follow} onChange={(event) => setFollow(event.target.checked)} />
          следовать
        </label>
      </div>
      <div
        ref={parentRef}
        className="min-h-0 flex-1 overflow-y-auto"
        onWheel={() => {
          userScrollAt.current = Date.now();
        }}
        data-testid="transcript-scroll"
      >
        {isLoading ? (
          <div className="flex flex-col gap-2 p-3" aria-busy="true">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-5 w-full" />
            ))}
          </div>
        ) : sentences.length === 0 ? (
          <p className="p-4 text-sm text-fg-muted">Транскрипта ещё нет: распознавание не закончено.</p>
        ) : (
          <div className="relative w-full" style={{ height: virtualizer.getTotalSize() }}>
            {virtualizer.getVirtualItems().map((item) => {
              const sentence = sentences[item.index];
              return (
                <div
                  key={item.key}
                  data-index={item.index}
                  ref={virtualizer.measureElement}
                  className="absolute top-0 left-0 w-full"
                  style={{ transform: `translateY(${item.start}px)` }}
                >
                  <SentenceRow
                    sentence={sentence}
                    words={words}
                    clock={clock}
                    range={range && range.w1 >= sentence.w0 && range.w0 <= sentence.w1 ? range : null}
                    selection={
                      selection && selection.w1 >= sentence.w0 && selection.w0 <= sentence.w1 ? selection : null
                    }
                    insertLabel={insertMarks[sentence.sid] ?? null}
                    highlighted={found === item.index}
                    onWord={selectWord}
                    onSeek={onSeek}
                  />
                </div>
              );
            })}
          </div>
        )}
      </div>
      {selection && selTimes ? (
        <div
          className="flex flex-wrap items-center gap-1.5 border-t border-border bg-surface-2 px-3 py-1.5"
          data-testid="selection-bar"
        >
          <span className="font-mono text-2xs text-fg-muted tabular">
            {formatTc(selTimes.start)}–{formatTc(selTimes.end)} · {formatSeconds(selTimes.end - selTimes.start)} ·{" "}
            {selection.w1 - selection.w0 + 1} сл.
          </span>
          {editable ? (
            <>
              <Button
                variant="outline"
                size="xs"
                onClick={() => onSetIn(selection.w0)}
                data-testid="set-in"
                title="Вход с первого выделенного слова (I)"
              >
                <ArrowLeftToLine className="h-3 w-3" aria-hidden="true" />
                Вход
              </Button>
              <Button
                variant="outline"
                size="xs"
                onClick={() => onSetOut(selection.w1)}
                data-testid="set-out"
                title="Выход на последнем выделенном слове (O)"
              >
                <ArrowRightToLine className="h-3 w-3" aria-hidden="true" />
                Выход
              </Button>
              <Button variant="outline" size="xs" onClick={() => onSetBounds(selection)} data-testid="set-bounds">
                <SquareSplitHorizontal className="h-3 w-3" aria-hidden="true" />
                Границы = выделение
              </Button>
            </>
          ) : null}
          {canCreate ? (
            <Button variant="outline" size="xs" onClick={() => onNewMoment(selection)} data-testid="new-moment">
              <Plus className="h-3 w-3" aria-hidden="true" />
              Новый момент
            </Button>
          ) : null}
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={clearSelection}
            aria-label="Снять выделение (Esc)"
            className="ml-auto"
          >
            <X className="h-3.5 w-3.5" aria-hidden="true" />
          </Button>
        </div>
      ) : null}
    </div>
  );
}
