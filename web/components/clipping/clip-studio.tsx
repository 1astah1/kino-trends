"use client";

import { ArrowLeft, Keyboard, RefreshCw, Scale, Trash } from "lucide-react";
import Link from "next/link";
import { parseAsFloat, parseAsInteger, useQueryStates } from "nuqs";
import { useCallback, useEffect, useEffectEvent, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { useCurrentUser } from "@/components/auth/auth-context";
import { SectionError } from "@/components/common/section-error";
import { isTypingTarget } from "@/components/review/review-logic";
import { Badge, Button, Callout, EmptyState, Skeleton, StatusBadge } from "@/components/ui";
import { errorMessage } from "@/lib/api/errors";
import { canOperate as canOperateRole } from "@/lib/auth";
import { routeHref } from "@/lib/nav";
import {
  type ClipCandidate,
  isSourceProcessing,
  type Source,
  useAuthorScript,
  useCandidates,
  useCreateCandidate,
  usePatchCandidate,
  usePeaks,
  useScenes,
  useSource,
  useStoryboard,
  useTranscript,
} from "@/lib/queries/clipping";
import { useJob } from "@/lib/queries/jobs";
import { type ClipPlayerController, createClipClock, type TimelineZoom, useClipEditor } from "@/lib/store/clip-editor";

import { ClipInspector } from "./clip-inspector";
import {
  activeWordAt,
  clampRange,
  draftFromBody,
  formatLength,
  nearestWordEnd,
  nearestWordStart,
  rangeTimes,
  sentencesInRange,
  type StoryboardGrid,
  type TranscriptWord,
  type WordRange,
  wordSpan,
} from "./clip-math";
import { ClipTimeline } from "./clip-timeline";
import { RIGHTS_TYPE_LABELS, SOURCE_STATUS_META, WATERMARK_META } from "./labels";
import { type MomentsFilter, MomentsPanel } from "./moments-panel";
import { DeleteSourceDialog, PipelineSteps, ReprocessDialog, RightsDialog } from "./source-dialogs";
import { SourcePlayer } from "./source-player";
import { TranscriptPanel } from "./transcript-panel";

const urlState = { id: parseAsInteger, clip: parseAsInteger, t: parseAsFloat };
const DEAD_JOB = new Set(["cancelled", "rejected", "failed"]);
const ZOOM_CYCLE: TimelineZoom[] = ["clip", "wide", "full"];

const HOTKEYS: Array<[string, string]> = [
  ["Пробел / K", "играть / пауза"],
  ["J / L", "−5 / +5 с"],
  ["← / →", "−1 / +1 с"],
  ["I / O", "вход / выход по головке (к слову)"],
  ["[ / ]", "к входу / к выходу"],
  ["P", "проиграть момент"],
  ["Enter", "применить границы"],
  ["Z", "масштаб дорожки"],
  ["Del", "отбросить момент"],
  ["Esc", "снять выделение"],
];

/** Границы кандидата индексами слов (с сервера или по секундам, если индексов нет). */
function candidateRange(candidate: ClipCandidate, words: readonly TranscriptWord[]): WordRange | null {
  if (!words.length) return null;
  if (candidate.start_word_idx !== null && candidate.end_word_idx !== null) {
    return clampRange({ w0: candidate.start_word_idx, w1: candidate.end_word_idx }, words.length);
  }
  return wordSpan(words, candidate.start_sec, candidate.end_sec);
}

function StudioHeader({
  source,
  canOperate,
  onRights,
  onReprocess,
  onDelete,
}: {
  source: Source;
  canOperate: boolean;
  onRights: () => void;
  onReprocess: () => void;
  onDelete: () => void;
}) {
  const rights = source.rights;
  return (
    <div
      className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-border px-4 py-2"
      data-testid="studio-header"
    >
      <Link
        href={routeHref("/clipping")}
        className="inline-flex items-center gap-1 text-xs text-fg-muted hover:text-fg"
      >
        <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
        Источники
      </Link>
      <h1 className="min-w-0 truncate text-base font-semibold text-fg" title={source.title}>
        {source.title}
      </h1>
      <StatusBadge status={source.status} map={SOURCE_STATUS_META} />
      <button type="button" onClick={onRights} className="inline-flex" title="Права на источник">
        <Badge variant={rights.gate_applies ? "warning" : "neutral"} outline>
          <Scale className="h-3 w-3" aria-hidden="true" />
          {RIGHTS_TYPE_LABELS[rights.rights_type]}
          {rights.gate_applies ? " · гейт 30%" : ""}
        </Badge>
      </button>
      {source.watermark && source.watermark.severity !== "ok" ? (
        <Badge variant={WATERMARK_META[source.watermark.severity].variant} title={source.watermark.detail ?? undefined}>
          {WATERMARK_META[source.watermark.severity].label}
        </Badge>
      ) : null}
      <span className="font-mono text-2xs text-fg-muted tabular">
        {formatLength(source.duration_sec)}
        {source.transcript
          ? ` · ${source.transcript.provider_key}${source.transcript.model ? `/${source.transcript.model}` : ""}`
          : ""}
      </span>
      <span className="ml-auto flex items-center gap-1">
        <details className="relative">
          <summary className="flex h-7 cursor-pointer list-none items-center gap-1 rounded-md px-2 text-xs text-fg-muted hover:bg-surface-2 hover:text-fg">
            <Keyboard className="h-3.5 w-3.5" aria-hidden="true" />
            Клавиши
          </summary>
          <div className="absolute right-0 z-40 mt-1 w-72 rounded-md border border-border bg-surface p-2 shadow-sm">
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
              {HOTKEYS.map(([key, label]) => (
                <div key={key} className="contents">
                  <dt className="font-mono text-fg">{key}</dt>
                  <dd className="text-fg-muted">{label}</dd>
                </div>
              ))}
            </dl>
          </div>
        </details>
        {canOperate ? (
          <>
            <Button variant="ghost" size="sm" onClick={onReprocess}>
              <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
              Повторить шаг
            </Button>
            <Button variant="ghost" size="icon-sm" onClick={onDelete} aria-label="Удалить источник">
              <Trash className="h-3.5 w-3.5" aria-hidden="true" />
            </Button>
          </>
        ) : null}
      </span>
    </div>
  );
}

/**
 * Студия нарезки (§7.3 `/clipping/source?id=&clip=&t=`): моменты слева; в центре плеер прокси
 * с рамкой 9:16, дорожка (волна, слова, склейки, раскадровка) и транскрипт с монтажом по тексту;
 * справа инспектор момента с авторским слоем, голосом, оценкой оригинальности и «В рендер».
 * Только десктоп.
 */
export function ClipStudio() {
  const [params] = useQueryStates(urlState, { history: "replace" });
  const source = useSource(params.id);

  if (!params.id) {
    return (
      <div className="p-4 md:p-6">
        <EmptyState
          title="Источник не выбран"
          action={
            <Link href={routeHref("/clipping")} className="text-sm text-accent hover:underline">
              К списку источников
            </Link>
          }
        />
      </div>
    );
  }
  if (source.isError) {
    return (
      <div className="p-4 md:p-6">
        <SectionError error={source.error} onRetry={() => void source.refetch()} title="Источник не загрузился" />
      </div>
    );
  }
  if (!source.data) {
    return (
      <div className="flex flex-col gap-3 p-4 md:p-6" aria-busy="true">
        <Skeleton className="h-6 w-72" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }
  return <StudioBody key={source.data.id} source={source.data} />;
}

function StudioBody({ source }: { source: Source }) {
  const [params, setParams] = useQueryStates(urlState, { history: "replace" });
  const user = useCurrentUser();
  const operator = canOperateRole(user.role);
  const [clock] = useState(createClipClock);
  const controllerRef = useRef<ClipPlayerController>(null);
  const [dialog, setDialog] = useState<"rights" | "reprocess" | "delete" | null>(null);
  const [filter, setFilter] = useState<MomentsFilter>("active");
  const [includeSuperseded, setIncludeSuperseded] = useState(false);

  useEffect(() => {
    useClipEditor.getState().hydrate();
  }, []);

  const transcript = useTranscript(source);
  const peaks = usePeaks(source);
  const storyboard = useStoryboard(source);
  const scenes = useScenes(source);
  const candidates = useCandidates(source.id, { includeSuperseded });
  const patchCandidate = usePatchCandidate();
  const createCandidate = useCreateCandidate(source.id);

  const words = useMemo(() => transcript.data?.words ?? [], [transcript.data]);
  const sentences = useMemo(() => transcript.data?.sentences ?? [], [transcript.data]);
  const items = useMemo(() => candidates.data?.items ?? [], [candidates.data]);

  // Выбранный момент: из URL, иначе лучший не отброшенный.
  const firstActive = items.find((c) => c.status !== "dismissed" && !c.superseded) ?? items[0] ?? null;
  const candidate = items.find((c) => c.id === params.clip) ?? (params.clip === null ? firstActive : null);
  // Момент из ссылки ушёл в прошлый прогон отбора — включим показ прошлых прогонов (один раз).
  const missingClip = !!candidates.data && params.clip !== null && !items.some((c) => c.id === params.clip);
  if (missingClip && !includeSuperseded) setIncludeSuperseded(true);
  useEffect(() => {
    if (candidates.data && params.clip === null && firstActive) void setParams({ clip: firstActive.id });
  }, [candidates.data, params.clip, firstActive, setParams]);

  // Стабильный контроллер: делегирует плееру, без плеера двигает часы.
  const controller = useMemo<ClipPlayerController>(
    () => ({
      seek: (sec) =>
        controllerRef.current ? controllerRef.current.seek(sec) : clock.setState({ t: Math.max(0, sec) }),
      seekBy: (delta) => controllerRef.current?.seekBy(delta),
      toggle: () => controllerRef.current?.toggle(),
      play: () => controllerRef.current?.play(),
      pause: () => controllerRef.current?.pause(),
      playRange: (start, end) => controllerRef.current?.playRange(start, end),
    }),
    [clock],
  );

  // Начальная позиция `?t=`.
  const initialT = useRef(params.t);
  useEffect(() => {
    if (initialT.current !== null && initialT.current !== undefined) controller.seek(initialT.current);
  }, [controller]);

  // Границы: неприменённые (стор) поверх серверных.
  const serverRange = useMemo(() => (candidate ? candidateRange(candidate, words) : null), [candidate, words]);
  const storedRange = useClipEditor((state) => (candidate ? state.bounds[candidate.id] : undefined));
  const setBounds = useClipEditor((state) => state.setBounds);
  const resetBoundsStore = useClipEditor((state) => state.resetBounds);
  const selectWord = useClipEditor((state) => state.selectWord);
  const clearSelection = useClipEditor((state) => state.clearSelection);
  const setZoom = useClipEditor((state) => state.setZoom);
  const reframe = useClipEditor((state) => state.reframe);
  const draft = storedRange ?? serverRange;
  const boundsDirty =
    !!storedRange && !!serverRange && (storedRange.w0 !== serverRange.w0 || storedRange.w1 !== serverRange.w1);
  const draftTimes = useMemo(
    () =>
      candidate
        ? boundsDirty && draft
          ? rangeTimes(words, draft)
          : { start: candidate.start_sec, end: candidate.end_sec }
        : { start: 0, end: 0 },
    [candidate, boundsDirty, draft, words],
  );

  // Ролик момента: пока он жив, границы зафиксированы.
  const job = useJob(candidate?.job_id ?? null);
  const jobLive = !!candidate?.job_id && !!job.data && !DEAD_JOB.has(job.data.status);
  const boundsEditable = operator && !!candidate && !jobLive && !(candidate.job_id && job.isPending);

  // Вставки авторского слоя — отметки в транскрипте.
  const liveJobId = jobLive ? (candidate?.job_id ?? null) : null;
  const serverScript = useAuthorScript(liveJobId);
  const localScript = useClipEditor((state) => (candidate ? state.scripts[candidate.id] : undefined));
  const jobScript = useClipEditor((state) => (liveJobId ? state.jobScripts[liveJobId]?.script : undefined));
  const insertMarks = useMemo(() => {
    const script = liveJobId ? (jobScript ?? draftFromBody(serverScript.data?.script)) : localScript;
    const marks: Record<number, string> = {};
    script?.inserts.forEach((insert, index) => {
      marks[insert.after_sid] = `вставка ${index + 1}`;
    });
    return marks;
  }, [liveJobId, jobScript, serverScript.data?.script, localScript]);

  const clipSentences = useMemo(() => (draft ? sentencesInRange(sentences, draft) : []), [sentences, draft]);

  const update = useCallback(
    (next: WordRange) => {
      if (!candidate || !boundsEditable || !words.length) return;
      setBounds(candidate.id, clampRange(next, words.length));
    },
    [candidate, boundsEditable, words.length, setBounds],
  );
  const setIn = useCallback((w: number) => draft && update({ w0: w, w1: Math.max(w, draft.w1) }), [draft, update]);
  const setOut = useCallback((w: number) => draft && update({ w0: Math.min(draft.w0, w), w1: w }), [draft, update]);
  const nudge = (edge: "in" | "out", delta: number) => {
    if (!draft) return;
    if (edge === "in") update({ w0: Math.min(draft.w0 + delta, draft.w1), w1: draft.w1 });
    else update({ w0: draft.w0, w1: Math.max(draft.w1 + delta, draft.w0) });
  };
  const playheadWord = (edge: "in" | "out") => {
    const t = clock.getState().t;
    const active = activeWordAt(words, t);
    if (active >= 0) return active;
    return edge === "in" ? nearestWordStart(words, t) : nearestWordEnd(words, t);
  };
  const inAtPlayhead = () => {
    const w = playheadWord("in");
    if (w >= 0) setIn(w);
  };
  const outAtPlayhead = () => {
    const w = playheadWord("out");
    if (w >= 0) setOut(w);
  };
  const onRegionChange = (start: number, end: number) => {
    if (!boundsEditable) return;
    const w0 = nearestWordStart(words, start);
    const w1 = nearestWordEnd(words, end);
    if (w0 < 0 || w1 < 0) return;
    update({ w0: Math.min(w0, w1), w1: Math.max(w0, w1) });
  };

  const applyBounds = useCallback(async () => {
    if (!candidate || !draft) return;
    const updated = await patchCandidate.mutateAsync({
      id: candidate.id,
      body: { start_word_idx: draft.w0, end_word_idx: draft.w1, snap: true },
    });
    resetBoundsStore(candidate.id);
    return updated;
  }, [candidate, draft, patchCandidate, resetBoundsStore]);

  const onApply = () => {
    if (!boundsDirty) return;
    void applyBounds()
      .then(() => toast.success("Границы применены: прилипание к тишине пересчитано"))
      .catch((error: unknown) => toast.error(errorMessage(error)));
  };

  const selectCandidate = (id: number) => {
    clearSelection();
    void setParams({ clip: id });
    const next = items.find((c) => c.id === id);
    if (next) controller.seek(next.start_sec);
  };

  const onNewMoment = async (range: WordRange) => {
    try {
      const created = await createCandidate.mutateAsync({
        start_word_idx: range.w0,
        end_word_idx: range.w1,
        snap: true,
      });
      clearSelection();
      setFilter("active");
      void setParams({ clip: created.id });
      toast.success("Новый момент создан");
    } catch (error) {
      toast.error(errorMessage(error));
    }
  };

  const dismiss = async () => {
    if (!candidate || !operator || candidate.status === "used" || candidate.status === "dismissed") return;
    try {
      await patchCandidate.mutateAsync({ id: candidate.id, body: { status: "dismissed", snap: true } });
      const id = candidate.id;
      toast.success("Момент отброшен", {
        action: {
          label: "Вернуть",
          onClick: () => void patchCandidate.mutateAsync({ id, body: { status: "proposed", snap: true } }),
        },
      });
    } catch (error) {
      toast.error(errorMessage(error));
    }
  };

  const clipRange = candidate ? draftTimes : null;
  const onKey = useEffectEvent((event: KeyboardEvent) => {
    if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return;
    if (isTypingTarget(event.target)) return;
    if (document.querySelector('[role="dialog"][data-state="open"]')) return;
    const key = event.key;
    const handled = () => event.preventDefault();
    switch (key) {
      case " ":
      case "k":
      case "K":
      case "л":
      case "Л":
        handled();
        controller.toggle();
        return;
      case "j":
      case "J":
      case "о":
      case "О":
        handled();
        controller.seekBy(-5);
        return;
      case "l":
      case "L":
      case "д":
      case "Д":
        handled();
        controller.seekBy(5);
        return;
      case "ArrowLeft":
        handled();
        controller.seekBy(event.shiftKey ? -0.1 : -1);
        return;
      case "ArrowRight":
        handled();
        controller.seekBy(event.shiftKey ? 0.1 : 1);
        return;
      case "i":
      case "I":
      case "ш":
      case "Ш":
        handled();
        inAtPlayhead();
        return;
      case "o":
      case "O":
      case "щ":
      case "Щ":
        handled();
        outAtPlayhead();
        return;
      case "[":
      case "х":
        handled();
        if (clipRange) controller.seek(clipRange.start);
        return;
      case "]":
      case "ъ":
        handled();
        if (clipRange) controller.seek(Math.max(clipRange.start, clipRange.end - 0.5));
        return;
      case "p":
      case "P":
      case "з":
      case "З":
        handled();
        if (clipRange) controller.playRange(clipRange.start, clipRange.end);
        return;
      case "Enter":
        if (boundsDirty) {
          handled();
          onApply();
        }
        return;
      case "z":
      case "Z":
      case "я":
      case "Я": {
        handled();
        const current = useClipEditor.getState().zoom;
        setZoom(ZOOM_CYCLE[(ZOOM_CYCLE.indexOf(current) + 1) % ZOOM_CYCLE.length]);
        return;
      }
      case "Delete":
        handled();
        void dismiss();
        return;
      case "Escape":
        clearSelection();
        return;
    }
  });
  useEffect(() => {
    const handler = (event: KeyboardEvent) => onKey(event);
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const storyboardGrid: StoryboardGrid | null = storyboard.data ?? null;
  const processing = isSourceProcessing(source.status);
  const scrollTarget = useMemo(
    () => (candidate && serverRange ? { index: serverRange.w0, key: `${candidate.id}` } : null),
    // только при смене момента
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [candidate?.id, serverRange?.w0],
  );

  return (
    <div className="flex flex-col xl:h-full xl:min-h-0" data-testid="clip-studio" data-source-id={source.id}>
      <StudioHeader
        source={source}
        canOperate={operator}
        onRights={() => setDialog("rights")}
        onReprocess={() => setDialog("reprocess")}
        onDelete={() => setDialog("delete")}
      />
      {dialog === "rights" ? <RightsDialog source={source} canEdit={operator} onClose={() => setDialog(null)} /> : null}
      {dialog === "reprocess" ? <ReprocessDialog source={source} onClose={() => setDialog(null)} /> : null}
      {dialog === "delete" ? <DeleteSourceDialog source={source} onClose={() => setDialog(null)} /> : null}

      {processing || source.status === "failed" ? (
        <div className="flex flex-col gap-2 border-b border-border px-4 py-3">
          {source.status === "failed" ? (
            <Callout variant="negative" title="Обработка источника упала">
              {source.error ?? "Посмотрите шаги ниже и повторите нужный."}
            </Callout>
          ) : (
            <p className="text-sm text-fg-muted">
              Источник ещё обрабатывается{source.progress !== null ? ` · ${Math.round(source.progress)}%` : ""}: студия
              заработает целиком, когда появятся транскрипт и моменты.
            </p>
          )}
          <PipelineSteps source={source} />
        </div>
      ) : null}
      <p className="px-4 py-2 text-xs text-warning xl:hidden">Студия нарезки рассчитана на экран шире 1280 px.</p>

      <div className="grid min-h-0 flex-1 grid-cols-1 xl:grid-cols-[272px_minmax(0,1fr)_392px]">
        <aside className="flex min-h-[320px] flex-col border-border xl:min-h-0 xl:border-r" aria-label="Моменты">
          <MomentsPanel
            candidates={items}
            selection={candidates.data?.selection ?? source.selection}
            isLoading={candidates.isPending}
            selectedId={candidate?.id ?? null}
            onSelect={selectCandidate}
            filter={filter}
            onFilter={setFilter}
            includeSuperseded={includeSuperseded}
            onIncludeSuperseded={setIncludeSuperseded}
            sourceId={source.id}
            canOperate={operator}
            sourceReady={source.status === "ready"}
          />
        </aside>

        <section
          className="flex min-w-0 flex-col gap-3 border-border p-3 xl:min-h-0 xl:border-r"
          aria-label="Плеер и транскрипт"
        >
          <SourcePlayer
            source={source}
            clock={clock}
            controllerRef={controllerRef}
            reframe={reframe}
            clipRange={clipRange}
            overlayTitle={localScript?.on_screen_title || jobScript?.on_screen_title || undefined}
          />
          <ClipTimeline
            peaks={peaks.data}
            duration={source.duration_sec ?? transcript.data?.duration_sec ?? 0}
            words={words}
            range={clipRange}
            draftRange={draft}
            scenes={scenes.data?.items ?? []}
            storyboard={storyboardGrid}
            clock={clock}
            controller={controller}
            onRegionChange={onRegionChange}
            onWordClick={selectWord}
            editable={boundsEditable}
          />
          <div className="flex min-h-[320px] flex-1 flex-col overflow-hidden rounded-md border border-border bg-surface xl:min-h-0">
            {transcript.isError ? (
              <SectionError
                error={transcript.error}
                onRetry={() => void transcript.refetch()}
                title="Транскрипт не загрузился"
              />
            ) : (
              <TranscriptPanel
                words={words}
                sentences={sentences}
                isLoading={transcript.isPending && !!source.media.transcript_url}
                clock={clock}
                controller={controller}
                range={draft}
                scrollToWord={scrollTarget}
                insertMarks={insertMarks}
                editable={boundsEditable}
                canCreate={operator && source.status === "ready"}
                onSetIn={setIn}
                onSetOut={setOut}
                onSetBounds={(range) => update(range)}
                onNewMoment={(range) => void onNewMoment(range)}
              />
            )}
          </div>
        </section>

        <aside className="flex min-h-[480px] flex-col xl:min-h-0" aria-label="Инспектор момента">
          {candidate ? (
            <ClipInspector
              key={candidate.id}
              source={source}
              candidate={candidate}
              words={words}
              sentences={clipSentences}
              draft={draft}
              boundsDirty={boundsDirty}
              draftTimes={draftTimes}
              canOperate={operator}
              applying={patchCandidate.isPending}
              onNudge={nudge}
              onInAtPlayhead={inAtPlayhead}
              onOutAtPlayhead={outAtPlayhead}
              applyBounds={applyBounds}
              resetBounds={() => candidate && resetBoundsStore(candidate.id)}
              onPlayClip={() => clipRange && controller.playRange(clipRange.start, clipRange.end)}
            />
          ) : (
            <div className="p-4">
              <EmptyState
                compact
                title={candidates.isPending ? "Загружаем моменты…" : "Выберите момент"}
                description="Слева — моменты от отбора. Можно выделить фрагмент в транскрипте и создать свой."
              />
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
