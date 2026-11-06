"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  Keyboard,
  Maximize,
  Pause,
  Play,
  Repeat,
  ScanLine,
  Layers,
  LayoutTemplate,
  Volume2,
  VolumeX,
  Inbox,
  ArrowRight,
} from "lucide-react";
import Link from "next/link";
import { parseAsInteger, parseAsStringLiteral, useQueryState } from "nuqs";
import { type ReactNode, useEffect, useEffectEvent, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { useStore } from "zustand";

import { useCurrentUser } from "@/components/auth/auth-context";
import { PageHeader } from "@/components/common/page-header";
import { SectionError } from "@/components/common/section-error";
import { OverlaySvg } from "@/components/player/overlay-svg";
import { createPlayerTimeStore, type PlayerController } from "@/components/player/player-time";
import { VerticalPlayer } from "@/components/player/vertical-player";
import { JOB_STAGE_LABELS, JOB_STATUS_META, PLATFORM_LABELS } from "@/components/production/job-meta";
import { PlayerTimeline } from "@/components/timeline/player-timeline";
import { Button, Callout, EmptyState, PillToggle, Skeleton, StatusBadge } from "@/components/ui";
import type { Schemas } from "@/lib/api/client";
import { ApiError, ConflictError, errorMessage, ValidationError } from "@/lib/api/errors";
import { canOperate, isAdmin } from "@/lib/auth";
import { cn } from "@/lib/cn";
import { useNow } from "@/lib/hooks/use-now";
import { routeHref } from "@/lib/nav";
import { useJob } from "@/lib/queries/jobs";
import {
  prefetchReviewContext,
  type RejectReasonItem,
  useDecideReview,
  useOverlayZones,
  useRejectReasons,
  useReviewContext,
  useReviewQueue,
  useUndoReview,
} from "@/lib/queries/review";
import { useReviewPrefs } from "@/lib/store/review-prefs";
import { useShellStore } from "@/lib/store/shell";

import { DecisionBar, type ReviewAlert } from "./decision-bar";
import { FactsPanel } from "./facts-panel";
import { HotkeysHelp } from "./hotkeys-help";
import { CaptionsToggle } from "./captions-toggle";
import { LayoutEditor } from "./layout-editor";
import { ReviewQueueList } from "./review-queue-list";
import {
  DECISION_DONE_LABELS,
  decisionConflictMessage,
  DEFAULT_REWORK_STAGE,
  formatTimecode,
  isReviewable,
  isTypingTarget,
  JOB_STATUS_LABELS,
  neighbour,
  nextAfter,
  type ReviewMode,
  reviewKeyAction,
  STAGES_BY_KIND,
  UNDO_TOAST_MS,
  undoConflictMessage,
} from "./review-logic";
import { SessionCounter } from "./session-counter";

type Platform = Schemas["Platform"];
type JobStage = Schemas["JobStage"];
type RejectReason = Schemas["RejectReason"];
type Decision = "approve" | "override_qc" | "reject" | "rework";

const PLATFORMS = ["youtube", "vk"] as const satisfies readonly Platform[];

/** Если справочник причин не загрузился — те же коды и клавиши, что в api/routers/meta.py. */
const FALLBACK_REASONS: RejectReasonItem[] = [
  { code: "hook_slabyy", label: "Слабый хук", hotkey: "1" },
  { code: "vizual_artefakty", label: "Артефакты визуала", hotkey: "2" },
  { code: "subtitry_rassinhron", label: "Рассинхрон субтитров", hotkey: "3" },
  { code: "banner_perekryvaet", label: "Баннер перекрывает смысл", hotkey: "4" },
  { code: "zvuk_problema", label: "Проблема со звуком", hotkey: "5" },
  { code: "zapreshchennaya_fraza", label: "Запрещённая фраза", hotkey: "6" },
  { code: "dublikat", label: "Дубликат", hotkey: "7" },
  { code: "ne_ta_dlitelnost", label: "Не та длительность", hotkey: "8" },
  { code: "drugoe", label: "Другое", hotkey: "9" },
  { code: "avtorskiy_sloy_slabyy", label: "Слабый авторский слой", hotkey: null },
  { code: "prava_somnitelny", label: "Сомнительные права", hotkey: null },
  { code: "povtor_shablona", label: "Повтор шаблона", hotkey: "0" },
];

/** Статусы, из которых решение ещё можно отменить (§6.2: approved | rejected | rework, + processing после доработки). */
const UNDOABLE_STATUSES = new Set(["approved", "rejected", "rework", "processing"]);
/** 409 на undo, после которых повтор бессмыслен — запись снимается со стека. */
const UNDO_FINAL_REASONS = new Set(["nothing_to_undo", "undo_window_expired", "published", "package_downloaded"]);
const DECISION_ACTIONS = new Set([
  "approve",
  "open_rework",
  "open_reject",
  "reject_with_hotkey",
  "rework_with_index",
  "confirm",
  "undo",
]);

interface UndoEntry {
  jobId: number;
  uid: string;
  decision: Decision;
  at: number;
}

/** Скрытые до ответа сервера ролики: `settledAt = null` — решение в пути; иначе — время ответа. */
type HiddenMap = ReadonlyMap<number, { settledAt: number | null }>;

function errorText(error: unknown): string {
  if (error instanceof ValidationError) {
    const fields = error.fieldErrors;
    return fields.expected_status ?? fields.reject_reason ?? fields.rework_from_stage ?? error.message;
  }
  if (error instanceof ApiError && error.status === 403) return `Недостаточно прав: ${error.message}`;
  return errorMessage(error);
}

/** Ползунок громкости плеера приёмки, без подписи: 0–100%, шаг 5%; ↑/↓ меняют на 10% из любого места экрана. */
function VolumeSlider({ value, onChange }: { value: number; onChange: (value: number) => void }) {
  const percent = Math.round(value * 100);
  return (
    <label className="inline-flex h-7 items-center rounded-md px-1" title={`Громкость ${percent}% (↑ / ↓)`}>
      <input
        type="range"
        min={0}
        max={100}
        step={5}
        value={percent}
        onChange={(event) => onChange(Number(event.target.value) / 100)}
        aria-label="Громкость"
        aria-valuetext={`${percent}%`}
        aria-keyshortcuts="ArrowUp ArrowDown"
        className="h-1 w-16 cursor-pointer accent-[var(--accent)] sm:w-24"
      />
    </label>
  );
}

function IconToggle({
  pressed,
  onClick,
  label,
  hotkey,
  children,
}: {
  pressed?: boolean;
  onClick: () => void;
  label: string;
  hotkey: string;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={pressed}
      aria-label={label}
      aria-keyshortcuts={hotkey}
      title={`${label} (${hotkey})`}
      className={cn(
        "inline-flex h-7 min-w-7 items-center justify-center gap-1 rounded-md px-1.5 text-xs transition-colors duration-150",
        pressed === undefined
          ? "text-fg-muted hover:bg-surface-2 hover:text-fg"
          : pressed
            ? "bg-accent/15 text-accent"
            : "text-fg-muted hover:bg-surface-2 hover:text-fg",
      )}
    >
      {children}
    </button>
  );
}

/**
 * Экран «Приёмка» (§7.3): очередь / плеер 9:16 с SVG-оверлеем и дорожкой / факты и решение.
 * Решение — оптимистично: ролик сразу уходит из очереди, открывается следующий, тост «Отменить» 6 с.
 * 409 (уже решено в Telegram или в другом окне) — понятное сообщение, ролик остаётся вне очереди.
 */
export function ReviewScreen() {
  const user = useCurrentUser();
  const operate = canOperate(user.role);
  const admin = isAdmin(user.role);
  const queryClient = useQueryClient();
  const now = useNow(30_000);

  const [jobParam, setJobParam] = useQueryState("job", parseAsInteger.withOptions({ history: "replace" }));
  const [platformParam, setPlatformParam] = useQueryState(
    "platform",
    parseAsStringLiteral(PLATFORMS).withOptions({ history: "replace" }),
  );

  const overlay = useReviewPrefs((state) => state.overlay);
  const muted = useReviewPrefs((state) => state.muted);
  const volume = useReviewPrefs((state) => state.volume);
  const loop = useReviewPrefs((state) => state.loop);
  const autoplay = useReviewPrefs((state) => state.autoplay);
  const autoAdvance = useReviewPrefs((state) => state.autoAdvance);
  const prefs = useReviewPrefs.getState;
  useEffect(() => {
    useReviewPrefs.getState().hydrate();
  }, []);

  // ---------------------------------------------------------------- очередь и выбор
  const queue = useReviewQueue();
  const [hidden, setHidden] = useState<HiddenMap>(() => new Map());
  const queueUpdatedAt = queue.dataUpdatedAt;
  const items = useMemo(
    () =>
      (queue.data ?? []).filter((item) => {
        const entry = hidden.get(item.job_id);
        if (!entry) return true;
        // Решение в пути — прячем; ответ получен — прячем, пока список не перечитан после него.
        return entry.settledAt !== null && queueUpdatedAt > entry.settledAt;
      }),
    [queue.data, hidden, queueUpdatedAt],
  );
  const ids = useMemo(() => items.map((item) => item.job_id), [items]);
  const currentId = jobParam ?? ids[0] ?? null;
  const queueItem = items.find((item) => item.job_id === currentId) ?? null;

  // Открытый ролик закрепляется в URL: новый старый ролик в начале очереди не подменит его под рукой.
  useEffect(() => {
    if (jobParam === null && ids[0] !== undefined) void setJobParam(ids[0]);
  }, [jobParam, ids, setJobParam]);

  const context = useReviewContext(currentId);
  const job = useJob(currentId);
  const reasonsQuery = useRejectReasons();
  const reasons = reasonsQuery.data?.length ? reasonsQuery.data : FALLBACK_REASONS;

  const status = context.data?.job.status ?? queueItem?.status ?? job.data?.status ?? null;
  const kind = context.data?.job.kind ?? queueItem?.kind ?? job.data?.kind ?? "generate";
  const uid = context.data?.job.video_uid ?? queueItem?.video_uid ?? job.data?.video_uid ?? null;
  const reviewable = isReviewable(status);
  const platform: Platform = platformParam ?? job.data?.channel.platform ?? "youtube";
  const { zones } = useOverlayZones(platform);
  const stages = STAGES_BY_KIND[kind];
  const defaultReworkStage = DEFAULT_REWORK_STAGE[kind];
  const failedChecks = useMemo(
    () => (context.data?.qc?.checks ?? []).filter((check) => !check.ok).map((check) => check.code),
    [context.data],
  );
  // Чужие права + проваленный отчёт об оригинальности: пакет не соберётся и после «вопреки QC».
  const originalityBlock = useMemo(() => {
    const report = context.data?.originality;
    if (!report || report.verdict !== "fail" || !report.summary.gate_applies) return null;
    return report.checks.filter((check) => check.status === "fail").map((check) => check.title);
  }, [context.data]);

  // ---------------------------------------------------------------- состояние решения
  const [mode, setMode] = useState<ReviewMode>("idle");
  const [layoutMode, setLayoutMode] = useState(false);
  const [comment, setComment] = useState("");
  const [autoRestart, setAutoRestart] = useState(true);
  const [alert, setAlert] = useState<ReviewAlert | null>(null);
  const [trackedId, setTrackedId] = useState(currentId);
  if (trackedId !== currentId) {
    // Другой ролик: выбор причины/стадии и комментарий не переносятся.
    setTrackedId(currentId);
    setMode((prev) => (prev === "help" ? prev : "idle"));
    setLayoutMode(false);
    setComment("");
  }

  const undoStack = useRef<UndoEntry[]>([]);
  const inFlight = useRef(new Set<number>());
  const currentIdRef = useRef(currentId);
  useEffect(() => {
    currentIdRef.current = currentId;
  }, [currentId]);

  const [timeStore] = useState(createPlayerTimeStore);
  const playing = useStore(timeStore, (state) => state.playing);
  const playerRef = useRef<PlayerController | null>(null);

  const decide = useDecideReview();
  const undoMutation = useUndoReview();

  // Следующий ролик грузится заранее — после решения экран открывается мгновенно.
  const upcoming = currentId !== null ? nextAfter(ids, currentId) : null;
  useEffect(() => {
    if (upcoming !== null && upcoming !== currentId) void prefetchReviewContext(queryClient, upcoming);
  }, [upcoming, currentId, queryClient]);

  const select = (id: number | null) => {
    void setJobParam(id);
  };

  const markHidden = (id: number, settledAt: number | null) =>
    setHidden((prev) => new Map(prev).set(id, { settledAt }));
  const unhide = (id: number) =>
    setHidden((prev) => {
      if (!prev.has(id)) return prev;
      const next = new Map(prev);
      next.delete(id);
      return next;
    });

  // ---------------------------------------------------------------- отмена решения
  const runUndo = (targetId?: number) => {
    if (!operate) {
      setAlert({ kind: "info", text: "Роль «Наблюдатель»: отменять решения нельзя." });
      return;
    }
    const stack = undoStack.current;
    const entry =
      targetId !== undefined ? [...stack].reverse().find((item) => item.jobId === targetId) : stack[stack.length - 1];
    const fallback =
      currentId !== null && status && !isReviewable(status) && UNDOABLE_STATUSES.has(status) ? currentId : null;
    const jobId = targetId ?? entry?.jobId ?? fallback;
    if (jobId === null || jobId === undefined) {
      setAlert({ kind: "info", text: "Отменять нечего: в этой сессии решений ещё не было." });
      return;
    }
    if (inFlight.current.has(jobId)) return;
    inFlight.current.add(jobId);
    const subjectUid = entry?.uid ?? (jobId === currentId ? uid : null);
    const drop = () => {
      undoStack.current = undoStack.current.filter((item) => item.jobId !== jobId);
    };
    undoMutation.mutateAsync(jobId).then(
      (restored) => {
        inFlight.current.delete(jobId);
        drop();
        unhide(jobId);
        select(jobId);
        prefs().countUndo(entry?.decision ?? null);
        setMode("idle");
        setAlert(null);
        toast.success(`${restored.video_uid}: решение отменено`, {
          id: `review-${jobId}`,
          description: `Ролик снова в очереди: ${JOB_STATUS_LABELS[restored.status].toLowerCase()}.`,
          duration: 4000,
        });
      },
      (error: unknown) => {
        inFlight.current.delete(jobId);
        if (error instanceof ConflictError) {
          const reason = typeof error.details.reason === "string" ? error.details.reason : "";
          if (UNDO_FINAL_REASONS.has(reason)) drop();
          const text = undoConflictMessage(error.details, subjectUid);
          setAlert({ kind: "conflict", text });
          toast.warning("Отмена не выполнена", { id: `review-${jobId}`, description: text });
          return;
        }
        const text = errorText(error);
        setAlert({ kind: "error", text });
        toast.error(text, { id: `review-${jobId}` });
      },
    );
  };

  // Тост живёт дольше рендера: его «Отменить» зовёт свежую версию функции.
  const undoRef = useRef(runUndo);
  useEffect(() => {
    undoRef.current = runUndo;
  });

  // ---------------------------------------------------------------- решение
  const submit = (decision: Decision, extra: { reason?: RejectReason; stage?: JobStage } = {}) => {
    if (!operate) {
      setAlert({ kind: "info", text: "Роль «Наблюдатель»: решения недоступны." });
      return;
    }
    if (currentId === null || !status || !isReviewable(status)) {
      setAlert({
        kind: "info",
        text: status
          ? `Ролик не ждёт приёмки: статус «${JOB_STATUS_LABELS[status].toLowerCase()}».`
          : "Ролик ещё загружается.",
      });
      return;
    }
    const jobId = currentId;
    if (inFlight.current.has(jobId)) return;
    inFlight.current.add(jobId);
    const subjectUid = uid ?? String(jobId);
    const advanceTo = autoAdvance ? nextAfter(ids, jobId) : jobId;
    const body: Schemas["ReviewRequest"] = {
      decision,
      expected_status: status,
      auto_restart: decision === "rework" ? autoRestart : true,
      reject_reason: decision === "reject" ? (extra.reason ?? null) : null,
      rework_from_stage: decision === "rework" ? (extra.stage ?? defaultReworkStage) : null,
      comment: comment.trim() || null,
    };
    const reasonLabel = extra.reason ? reasons.find((item) => item.code === extra.reason)?.label : null;

    markHidden(jobId, null);
    setMode("idle");
    setComment("");
    setAlert(null);
    if (autoAdvance) select(advanceTo);

    decide.mutateAsync({ jobId, body }).then(
      () => {
        inFlight.current.delete(jobId);
        markHidden(jobId, Date.now());
        undoStack.current = [
          ...undoStack.current.filter((item) => item.jobId !== jobId),
          { jobId, uid: subjectUid, decision, at: Date.now() },
        ].slice(-20);
        prefs().countDecision(decision);
        toast.success(`${subjectUid}: ${DECISION_DONE_LABELS[decision]}`, {
          id: `review-${jobId}`,
          description:
            decision === "reject"
              ? `Причина: ${reasonLabel ?? extra.reason}`
              : decision === "rework"
                ? `Со стадии «${JOB_STAGE_LABELS[body.rework_from_stage ?? defaultReworkStage]}»${autoRestart ? ", перезапущен" : ", ждёт правок"}`
                : undefined,
          duration: UNDO_TOAST_MS,
          action: { label: "Отменить", onClick: () => undoRef.current(jobId) },
        });
      },
      (error: unknown) => {
        inFlight.current.delete(jobId);
        if (error instanceof ConflictError) {
          // Решено раньше (Telegram, другое окно): ролик из очереди ушёл, повторять нечего.
          markHidden(jobId, Date.now());
          const text = decisionConflictMessage(error.details, subjectUid);
          setAlert({ kind: "conflict", text });
          toast.warning("Уже решено", { id: `review-${jobId}`, description: text, duration: 8000 });
          return;
        }
        unhide(jobId);
        // Вернуть к ролику, только если оператор ещё не ушёл дальше сам.
        if (autoAdvance && currentIdRef.current === advanceTo) select(jobId);
        const text = errorText(error);
        setAlert({ kind: "error", text });
        toast.error(text, { id: `review-${jobId}` });
      },
    );
  };

  const onApprove = () => {
    if (status === "qc_failed") {
      if (!admin) {
        setAlert({
          kind: "info",
          text: "Ролик не прошёл QC. Принять вопреки QC может только владелец; отклоните (X) или отправьте на доработку (R).",
        });
        return;
      }
      setMode("override");
      return;
    }
    submit("approve");
  };

  const openMode = (next: "reject" | "rework") => {
    if (!operate || !reviewable) {
      submit(next); // покажет понятную причину, почему нельзя
      return;
    }
    setMode((prev) => (prev === next ? "idle" : next));
  };

  const rejectWithHotkey = (hotkey: string) => {
    const reason = reasons.find((item) => item.hotkey === hotkey);
    if (!reason) {
      setAlert({ kind: "info", text: `Под цифрой ${hotkey} причины нет — выберите из списка.` });
      return;
    }
    submit("reject", { reason: reason.code });
  };

  const togglePlatform = () => void setPlatformParam(platform === "youtube" ? "vk" : "youtube");

  // ---------------------------------------------------------------- клавиши
  const spaceHandled = useRef(false);
  const onKeyDown = useEffectEvent((event: KeyboardEvent) => {
    if (event.defaultPrevented) return;
    if (useShellStore.getState().paletteOpen) return;
    if (isTypingTarget(event.target)) {
      if (event.code === "Escape") (event.target as HTMLElement).blur();
      return;
    }
    if (mode !== "help" && document.querySelector('[role="dialog"][data-state="open"]')) return;
    const action = reviewKeyAction(event, mode);
    if (!action) return;
    if (mode === "help" && action.type !== "cancel_mode" && action.type !== "help") return;
    if (event.repeat && DECISION_ACTIONS.has(action.type)) {
      event.preventDefault();
      return;
    }
    event.preventDefault();
    if (action.type === "play_pause") spaceHandled.current = true;

    switch (action.type) {
      case "approve":
        onApprove();
        return;
      case "open_rework":
        openMode("rework");
        return;
      case "open_reject":
        openMode("reject");
        return;
      case "reject_with_hotkey":
        rejectWithHotkey(action.hotkey);
        return;
      case "rework_with_index": {
        const stage = stages[action.index];
        if (stage) submit("rework", { stage });
        return;
      }
      case "confirm":
        if (mode === "override") submit("override_qc");
        else if (mode === "rework") submit("rework", { stage: defaultReworkStage });
        return;
      case "cancel_mode":
        setMode("idle");
        return;
      case "undo":
        runUndo();
        return;
      case "next":
      case "prev": {
        const target = neighbour(ids, currentId, action.type === "next" ? 1 : -1);
        if (target !== null) select(target);
        return;
      }
      case "play_pause":
        playerRef.current?.togglePlay();
        return;
      case "seek":
        playerRef.current?.seekBy(action.deltaSec);
        return;
      case "frame":
        playerRef.current?.frame(action.delta);
        return;
      case "toggle_dead_zones":
        prefs().toggleOverlay("deadZones");
        return;
      case "toggle_banner":
        prefs().toggleOverlay("banner");
        return;
      case "toggle_platform":
        togglePlatform();
        return;
      case "fullscreen":
        playerRef.current?.fullscreen();
        return;
      case "toggle_loop":
        prefs().toggleLoop();
        return;
      case "toggle_mute":
        prefs().toggleMuted();
        return;
      case "volume":
        prefs().changeVolume(action.delta);
        return;
      case "help":
        setMode((prev) => (prev === "help" ? "idle" : "help"));
        return;
    }
  });
  // Пробел отпущен на кнопке: не даём браузеру «нажать» её после паузы плеера.
  const onKeyUp = useEffectEvent((event: KeyboardEvent) => {
    if (event.code !== "Space" || !spaceHandled.current) return;
    spaceHandled.current = false;
    if (!isTypingTarget(event.target)) event.preventDefault();
  });
  useEffect(() => {
    const down = (event: KeyboardEvent) => onKeyDown(event);
    const up = (event: KeyboardEvent) => onKeyUp(event);
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
    };
  }, []);

  // ---------------------------------------------------------------- разметка
  const ctx = context.data;
  const media = ctx?.media;
  const durationHint = media?.duration_sec ?? queueItem?.duration_sec ?? null;
  const remaining = ids.length;
  const pendingCurrent = currentId !== null && hidden.get(currentId)?.settledAt === null;

  const header = (
    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-2">
      <div className="flex min-w-0 items-center gap-3">
        <h1 className="text-lg font-semibold tracking-tight">Приёмка</h1>
        <SessionCounter remaining={remaining} />
      </div>
      <div className="flex items-center gap-2 text-xs text-fg-muted">
        <label className="inline-flex items-center gap-1.5">
          <input
            type="checkbox"
            checked={autoAdvance}
            onChange={(event) => prefs().setAutoAdvance(event.target.checked)}
            className="h-3.5 w-3.5"
          />
          следующий сразу
        </label>
        <label className="inline-flex items-center gap-1.5">
          <input
            type="checkbox"
            checked={autoplay}
            onChange={(event) => prefs().setAutoplay(event.target.checked)}
            className="h-3.5 w-3.5"
          />
          автозапуск
        </label>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setMode((prev) => (prev === "help" ? "idle" : "help"))}
          aria-keyshortcuts="Shift+Slash"
        >
          <Keyboard className="h-3.5 w-3.5" aria-hidden="true" />
          <span className="hidden sm:inline">Клавиши</span>
          <kbd className="rounded-sm border border-border px-1 font-mono text-2xs">?</kbd>
        </Button>
      </div>
    </div>
  );

  const helpDialog = <HotkeysHelp open={mode === "help"} onClose={() => setMode("idle")} />;

  if (queue.isError && !queue.data) {
    return (
      <div className="flex flex-col gap-4 p-4 md:p-6">
        <PageHeader title="Приёмка" />
        <SectionError error={queue.error} onRetry={() => void queue.refetch()} />
      </div>
    );
  }

  if (!queue.isPending && currentId === null) {
    return (
      <div className="flex flex-col lg:h-full">
        {header}
        <div className="flex flex-1 items-center justify-center p-6">
          <EmptyState
            icon={Inbox}
            title="Очередь приёмки пуста"
            description={
              <>
                Всё решено. Новые ролики появятся здесь сами, когда пройдут QC; бейдж в меню обновится без перезагрузки.
              </>
            }
            action={
              <Link
                href={routeHref("/production")}
                className="inline-flex items-center gap-1.5 text-sm text-accent hover:underline"
              >
                Открыть конвейер <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
              </Link>
            }
            className="w-full max-w-lg"
          />
        </div>
        {helpDialog}
      </div>
    );
  }

  const notInQueueCallout =
    currentId !== null && status && !reviewable && !pendingCurrent ? (
      <Callout variant="info" title={`Ролик не ждёт приёмки: ${JOB_STATUS_LABELS[status].toLowerCase()}`}>
        {UNDOABLE_STATUSES.has(status)
          ? "Если решение принято здесь или в Telegram меньше 10 минут назад, его можно отменить: U."
          : "Решения по нему недоступны."}{" "}
        {ids.length ? (
          <button type="button" className="text-accent hover:underline" onClick={() => select(ids[0])}>
            К первому в очереди
          </button>
        ) : null}
      </Callout>
    ) : null;

  return (
    <div className="flex flex-col lg:h-full lg:min-h-0" data-testid="review-screen" data-mode={mode}>
      {header}
      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[248px_minmax(0,1fr)_340px] xl:grid-cols-[272px_minmax(0,1fr)_392px]">
        {/* очередь */}
        <aside
          className="order-3 min-h-0 border-t border-border lg:order-1 lg:overflow-y-auto lg:border-t-0 lg:border-r"
          aria-label="Очередь"
        >
          <ReviewQueueList
            items={items}
            currentId={currentId}
            onSelect={(id) => select(id)}
            isLoading={queue.isPending}
            now={now}
          />
        </aside>

        {/* плеер, дорожка, решение */}
        <section
          className="order-1 flex min-h-0 flex-col gap-2 p-2 md:p-3 lg:order-2 lg:overflow-y-auto"
          aria-label="Ролик"
        >
          <div className="flex items-center justify-between gap-2">
            <div className="flex min-w-0 items-center gap-2">
              {uid ? <span className="truncate font-mono text-sm">{uid}</span> : <Skeleton className="h-4 w-28" />}
              {status ? <StatusBadge status={status} map={JOB_STATUS_META} /> : null}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {currentId !== null && kind === "clip" ? <CaptionsToggle jobId={currentId} canOperate={operate} /> : null}
              <PillToggle
                label="Площадка для мёртвых зон"
                options={PLATFORMS.map((value) => ({ value, label: PLATFORM_LABELS[value] }))}
                value={platform}
                onChange={(value) => void setPlatformParam(value)}
              />
            </div>
          </div>

          {layoutMode && currentId !== null && kind === "clip" ? (
            <LayoutEditor
              jobId={currentId}
              poster={media?.poster_url ?? queueItem?.poster_url}
              canOperate={operate}
              showDeadZones={overlay.deadZones}
              onClose={() => setLayoutMode(false)}
            />
          ) : (
            <>
              <div className="relative mx-auto aspect-[9/16] w-full max-w-[min(100%,calc((100dvh_-_16rem)_*_0.5625))] min-w-[220px] lg:max-w-[min(100%,calc((100dvh_-_21rem)_*_0.5625))]">
                {context.isPending ? (
                  <Skeleton className="absolute inset-0 rounded-none bg-video" />
                ) : context.isError ? (
                  <div className="absolute inset-0 flex items-center bg-video p-3">
                    <SectionError error={context.error} onRetry={() => void context.refetch()} className="w-full" />
                  </div>
                ) : (
                  <VerticalPlayer
                    label={`Плеер ролика ${uid ?? ""}`}
                    mediaKey={currentId ?? undefined}
                    src={media?.preview_url ?? media?.final_url ?? queueItem?.preview_url}
                    poster={media?.poster_url ?? queueItem?.poster_url}
                    muted={muted}
                    volume={volume}
                    loop={loop}
                    autoPlay={autoplay}
                    durationHint={durationHint}
                    timeStore={timeStore}
                    controllerRef={playerRef}
                    overlay={
                      <OverlaySvg
                        zones={zones}
                        showDeadZones={overlay.deadZones}
                        boxes={ctx?.banner.boxes}
                        windows={ctx?.banner.windows}
                        captionsZone={ctx?.banner.captions_zone}
                        showBanner={overlay.banner}
                        timeStore={timeStore}
                      />
                    }
                  />
                )}
              </div>

              <div className="flex flex-wrap items-center justify-between gap-1">
                <div className="flex items-center gap-1">
                  <IconToggle
                    onClick={() => playerRef.current?.togglePlay()}
                    label={playing ? "Пауза" : "Воспроизвести"}
                    hotkey="Space"
                  >
                    {playing ? (
                      <Pause className="h-4 w-4" aria-hidden="true" />
                    ) : (
                      <Play className="h-4 w-4" aria-hidden="true" />
                    )}
                  </IconToggle>
                  <IconToggle pressed={muted} onClick={() => prefs().toggleMuted()} label="Без звука" hotkey="M">
                    {muted ? (
                      <VolumeX className="h-4 w-4" aria-hidden="true" />
                    ) : (
                      <Volume2 className="h-4 w-4" aria-hidden="true" />
                    )}
                  </IconToggle>
                  <VolumeSlider value={muted ? 0 : volume} onChange={(value) => prefs().setVolume(value)} />
                  <IconToggle pressed={loop} onClick={() => prefs().toggleLoop()} label="Повтор по кругу" hotkey="L">
                    <Repeat className="h-4 w-4" aria-hidden="true" />
                  </IconToggle>
                  <IconToggle onClick={() => playerRef.current?.fullscreen()} label="Во весь экран" hotkey="F">
                    <Maximize className="h-4 w-4" aria-hidden="true" />
                  </IconToggle>
                </div>
                <div className="flex items-center gap-1">
                  <IconToggle
                    pressed={overlay.deadZones}
                    onClick={() => prefs().toggleOverlay("deadZones")}
                    label={`Мёртвые зоны ${PLATFORM_LABELS[platform]}`}
                    hotkey="S"
                  >
                    <ScanLine className="h-4 w-4" aria-hidden="true" />
                    <span className="hidden sm:inline">зоны</span>
                  </IconToggle>
                  <IconToggle
                    pressed={overlay.banner}
                    onClick={() => prefs().toggleOverlay("banner")}
                    label="Баннер и субтитры"
                    hotkey="B"
                  >
                    <Layers className="h-4 w-4" aria-hidden="true" />
                    <span className="hidden sm:inline">баннер</span>
                  </IconToggle>
                  {kind === "clip" ? (
                    <button
                      type="button"
                      onClick={() => setLayoutMode(true)}
                      title="Раскладка баннера и текста: подвинуть, чтобы ничего не налезало"
                      className="inline-flex h-7 items-center gap-1 rounded-md px-1.5 text-xs text-fg-muted transition-colors duration-150 hover:bg-surface-2 hover:text-fg"
                      data-testid="review-layout-open"
                    >
                      <LayoutTemplate className="h-4 w-4" aria-hidden="true" />
                      <span className="hidden sm:inline">раскладка</span>
                    </button>
                  ) : null}
                </div>
              </div>
              {zones && overlay.deadZones ? (
                <p className="text-2xs text-fg-muted">
                  Зоны: {zones.label}
                  {zones.source === "meta_presets" ? " (пресет, каталог профилей недоступен)" : ""}
                </p>
              ) : null}

              <PlayerTimeline
                timeStore={timeStore}
                durationHint={durationHint}
                sceneCuts={ctx?.scene_cuts_sec}
                bannerWindows={ctx?.banner.windows}
                onSeek={(sec) => playerRef.current?.seekTo(sec)}
              />
            </>
          )}

          {notInQueueCallout}

          <div className="sticky bottom-0 z-10 -mx-2 border-t border-border bg-bg/95 px-2 pt-2 pb-2 backdrop-blur-sm md:-mx-3 md:px-3 lg:static lg:mx-0 lg:border-0 lg:bg-transparent lg:p-0 lg:backdrop-blur-none">
            <DecisionBar
              mode={mode}
              canDecide={operate && reviewable && !pendingCurrent}
              canOperate={operate}
              qcFailed={status === "qc_failed"}
              failedChecks={failedChecks}
              originalityBlock={originalityBlock}
              pending={pendingCurrent}
              reasons={reasons}
              stages={stages}
              defaultReworkStage={defaultReworkStage}
              autoRestart={autoRestart}
              comment={comment}
              alert={alert}
              onDismissAlert={() => setAlert(null)}
              onCommentChange={setComment}
              onAutoRestartChange={setAutoRestart}
              onApprove={onApprove}
              onConfirmOverride={() => submit("override_qc")}
              onOpenReject={() => openMode("reject")}
              onReject={(reason) => submit("reject", { reason })}
              onOpenRework={() => openMode("rework")}
              onRework={(stage) => submit("rework", { stage })}
              onCancelMode={() => setMode("idle")}
              onUndo={() => runUndo()}
            />
          </div>
        </section>

        {/* факты */}
        <aside
          className="order-2 min-h-0 border-t border-border lg:order-3 lg:overflow-y-auto lg:border-t-0 lg:border-l"
          aria-label="Факты о ролике"
        >
          {ctx ? (
            <FactsPanel
              context={ctx}
              job={job.data}
              canOperate={operate}
              reasons={reasons}
              timeStore={timeStore}
              onSeek={(sec) => playerRef.current?.seekTo(sec)}
            />
          ) : context.isPending ? (
            <div className="flex flex-col gap-3 p-3" aria-busy="true">
              <Skeleton className="h-5 w-40" />
              <Skeleton className="h-20 w-full" />
              <Skeleton className="h-28 w-full" />
            </div>
          ) : null}
          {ctx && media?.duration_sec ? (
            <p className="px-3 pb-3 font-mono text-2xs text-fg-muted tabular">
              длительность {formatTimecode(media.duration_sec)}
            </p>
          ) : null}
        </aside>
      </div>
      {helpDialog}
    </div>
  );
}
