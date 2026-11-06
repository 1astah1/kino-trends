"use client";

/**
 * Работа над моментом в студии: состояние clip-job кандидата, авторский текст (локальный до ролика,
 * серверный после), автосохранение с `expected_version`, черновик модели, запись голоса, оценка
 * оригинальности и «В рендер» (docs/ARCHITECTURE.md §6.3.6, §6.3.8; api/schemas/clipping.py).
 *
 * Фазы:
 * - `local` — ролика нет (или прошлый отменён/отклонён/упал): текст живёт в браузере, «В рендер»
 *   создаёт clip-job сразу со стартом;
 * - `draft` — черновик ролика (создан для черновика модели или записи голоса): текст на сервере,
 *   голос пишется, «В рендер» = сохранить и `POST /jobs/{id}/start`;
 * - `active` — ролик в работе или на приёмке: текст правится, пока сервер говорит `editable`.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { Schemas } from "@/lib/api/client";
import { ApiError, ConflictError, errorMessage, ValidationError } from "@/lib/api/errors";
import {
  type ClipCandidate,
  type OriginalityEstimateRequest,
  type Source,
  useAuthorScript,
  useAuthorScriptDraft,
  useAuthorVoice,
  useCreateJobFromCandidate,
  useOriginalityEstimate,
  usePutAuthorScript,
} from "@/lib/queries/clipping";
import { useCancelJob, useJob, useStartJob } from "@/lib/queries/jobs";
import { useClipEditor } from "@/lib/store/clip-editor";

import {
  type AuthorDraft,
  bodyFromDraft,
  draftFromBody,
  draftLimitErrors,
  draftsEqual,
  emptyAuthorDraft,
  finalDurationBlocker,
  isDraftEmpty,
} from "./clip-math";
import { SEGMENT_LABELS } from "./labels";

type JobStatus = Schemas["JobStatus"];
type Job = Schemas["Job"];

export type ClipPhase = "loading" | "local" | "draft" | "active";
export type SaveState = "saved" | "dirty" | "saving" | "error" | null;

const DEAD: ReadonlySet<JobStatus> = new Set(["cancelled", "rejected", "failed"]);
const EMPTY_DRAFT = emptyAuthorDraft();

/** JSON-значение, которое меняется не чаще раза в `ms` (сравнение по JSON, не по ссылке). */
export function useDebounced<T>(value: T, ms: number): T {
  const key = JSON.stringify(value);
  const [state, setState] = useState<{ key: string; value: T }>({ key, value });
  useEffect(() => {
    if (key === state.key) return;
    const timer = setTimeout(() => setState({ key, value: JSON.parse(key) as T }), ms);
    return () => clearTimeout(timer);
  }, [key, ms, state.key]);
  return state.value;
}

export interface ClipTarget {
  channelId: string | null;
  campaignId: number | null;
  variantId: number | null;
  reframe: Schemas["ReframeMode"];
  voice: Schemas["AuthorVoiceSource"];
  /** Оформление ролика поверх канала; `shape: "channel"` без правок — как у канала. */
  frame: Schemas["FrameOptions"];
  /** Субтитры в ролике. */
  captions: boolean;
}

/** Оформление для запроса: без пустых полей; «как у канала» без правок — null (берётся профиль канала). */
export function frameRequest(frame: Schemas["FrameOptions"]): Schemas["FrameOptions"] | null {
  const out = Object.fromEntries(
    Object.entries(frame).filter(([, value]) => value !== null && value !== undefined && value !== ""),
  ) as Schemas["FrameOptions"];
  const tuned = Object.keys(out).some((key) => key !== "shape");
  return out.shape === "channel" && !tuned ? null : out;
}

export function useClipWork({
  source,
  candidate,
  canOperate,
  boundsDirty,
  draftTimes,
  applyBounds,
  target,
}: {
  source: Source;
  candidate: ClipCandidate;
  canOperate: boolean;
  boundsDirty: boolean;
  /** Время клипа с неприменённой правкой (для оценки до применения). */
  draftTimes: { start: number; end: number };
  applyBounds: () => Promise<unknown>;
  target: ClipTarget;
}) {
  const jobId = candidate.job_id ?? null;
  const job = useJob(jobId);
  const status = job.data?.status ?? null;
  const dead = status !== null && DEAD.has(status);
  const phase: ClipPhase = !jobId || dead ? "local" : !job.data ? "loading" : status === "draft" ? "draft" : "active";
  const liveJobId = phase === "draft" || phase === "active" ? jobId : null;

  const authorScript = useAuthorScript(liveJobId);
  const authorVoice = useAuthorVoice(liveJobId);
  const putScript = usePutAuthorScript();
  const draftRequest = useAuthorScriptDraft();
  const createJob = useCreateJobFromCandidate();
  const startJob = useStartJob();
  const cancelJob = useCancelJob();

  const localDraft = useClipEditor((state) => state.scripts[candidate.id]) ?? EMPTY_DRAFT;
  const jobDraft = useClipEditor((state) => (liveJobId ? state.jobScripts[liveJobId] : undefined));
  const setScript = useClipEditor((state) => state.setScript);
  const setJobScript = useClipEditor((state) => state.setJobScript);
  const dropJobScript = useClipEditor((state) => state.dropJobScript);

  const serverScript = authorScript.data?.script;
  const serverDraft = useMemo(() => draftFromBody(serverScript), [serverScript]);
  const value: AuthorDraft = phase === "local" ? localDraft : (jobDraft?.script ?? serverDraft);

  const editable =
    canOperate &&
    (phase === "local" ||
      phase === "draft" ||
      (phase === "active" && !!authorScript.data?.editable && !authorScript.data?.drafting_task_id));

  const [saveState, setSaveState] = useState<SaveState>(null);
  const [saveError, setSaveError] = useState<{ message: string; conflict: boolean } | null>(null);

  const onChange = useCallback(
    (next: AuthorDraft) => {
      if (phase === "local") {
        setScript(candidate.id, next);
        return;
      }
      if (!liveJobId) return;
      const base = useClipEditor.getState().jobScripts[liveJobId]?.baseVersion ?? authorScript.data?.version ?? 0;
      setJobScript(liveJobId, { script: next, baseVersion: base });
      setSaveState("dirty");
    },
    [phase, liveJobId, candidate.id, setScript, setJobScript, authorScript.data?.version],
  );

  // ---------------------------------------------------------------- автосохранение текста ролика

  const savingRef = useRef<Promise<void> | null>(null);
  const save = useCallback(
    async (force = false): Promise<void> => {
      if (!liveJobId) return;
      const snapshot = useClipEditor.getState().jobScripts[liveJobId];
      if (!snapshot) return;
      if (Object.keys(draftLimitErrors(snapshot.script)).length) {
        setSaveState("error");
        setSaveError({ message: "Текст длиннее лимита — сократите, чтобы сохранить", conflict: false });
        throw new Error("Текст длиннее лимита");
      }
      setSaveState("saving");
      try {
        const saved = await putScript.mutateAsync({
          jobId: liveJobId,
          body: { script: bodyFromDraft(snapshot.script), expected_version: force ? null : snapshot.baseVersion },
        });
        const current = useClipEditor.getState().jobScripts[liveJobId];
        if (current && draftsEqual(current.script, snapshot.script)) dropJobScript(liveJobId);
        else if (current) setJobScript(liveJobId, { ...current, baseVersion: saved.version });
        setSaveState("saved");
        setSaveError(null);
      } catch (error) {
        setSaveState("error");
        if (error instanceof ConflictError && error.code === "version_conflict") {
          setSaveError({
            message: "Текст ролика изменился на сервере (черновик модели или другой оператор)",
            conflict: true,
          });
          void authorScript.refetch();
        } else if (error instanceof ConflictError) {
          setSaveError({
            message:
              error.details.hint === "rework"
                ? "Ролик уже озвучен: править текст можно после «На доработку» в Приёмке"
                : error.message,
            conflict: false,
          });
        } else if (error instanceof ValidationError) {
          setSaveError({ message: Object.values(error.fieldErrors)[0] ?? error.message, conflict: false });
        } else {
          setSaveError({ message: errorMessage(error), conflict: false });
        }
        throw error;
      }
    },
    [liveJobId, putScript, dropJobScript, setJobScript, authorScript],
  );

  useEffect(() => {
    if (!liveJobId || !jobDraft) return;
    if (Object.keys(draftLimitErrors(jobDraft.script)).length) return;
    if (saveError?.conflict) return;
    const timer = setTimeout(() => {
      savingRef.current = save().catch(() => undefined);
    }, 1_200);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- сохраняем по изменению текста
  }, [liveJobId, jobDraft]);

  /** Дописать несохранённое перед записью голоса или запуском. */
  const flush = useCallback(async () => {
    if (savingRef.current) await savingRef.current;
    if (liveJobId && useClipEditor.getState().jobScripts[liveJobId]) await save();
  }, [liveJobId, save]);

  const resolveConflict = useCallback(
    async (keepMine: boolean) => {
      if (!liveJobId) return;
      setSaveError(null);
      if (!keepMine) {
        dropJobScript(liveJobId);
        setSaveState(null);
        return;
      }
      await save(true).catch(() => undefined);
    },
    [liveJobId, dropJobScript, save],
  );

  // ---------------------------------------------------------------- проверки перед рендером

  const limitErrors = draftLimitErrors(value);
  const blockers: string[] = [];
  if (!canOperate) blockers.push("Нужны права оператора");
  if (source.status !== "ready") blockers.push("Источник ещё не готов");
  if (phase === "local" && !target.channelId) blockers.push("Выберите канал");
  if (phase === "local" && candidate.blocked)
    blockers.push("Момент заблокирован флагами — снимите блок или смените момент");
  if (Object.keys(limitErrors).length) blockers.push("Авторский текст длиннее лимита");

  const jobOptions = useCallback(
    (start: boolean): Schemas["ClipJobOptions"] => {
      const local = useClipEditor.getState().scripts[candidate.id] ?? EMPTY_DRAFT;
      const empty = isDraftEmpty(local);
      return {
        channel_id: target.channelId as string,
        campaign_id: target.campaignId,
        banner_variant_id: target.variantId,
        author_layer: empty
          ? {
              mode: "llm_draft",
              voice: target.voice,
              captions: target.captions ? "both" : "none",
              frame: frameRequest(target.frame),
            }
          : {
              mode: "operator_text",
              voice: target.voice,
              captions: target.captions ? "both" : "none",
              frame: frameRequest(target.frame),
              script: bodyFromDraft(local),
              title_overlay: local.on_screen_title.trim() || null,
            },
        reframe: { mode: target.reframe },
        priority: 100,
        start,
      };
    },
    [candidate.id, target],
  );

  /** Черновик ролика (без запуска): нужен для черновика модели и записи своего голоса. */
  const ensureDraftJob = useCallback(async (): Promise<number> => {
    if (liveJobId) return liveJobId;
    if (!target.channelId) throw new ApiError(422, { code: "channel_required", message: "Сначала выберите канал" });
    if (candidate.blocked)
      throw new ApiError(409, { code: "clip_candidate_blocked", message: "Момент заблокирован флагами" });
    if (boundsDirty) await applyBounds();
    const created = await createJob.mutateAsync({ candidateId: candidate.id, body: jobOptions(false) });
    return created.id;
  }, [liveJobId, target.channelId, candidate.blocked, candidate.id, boundsDirty, applyBounds, createJob, jobOptions]);

  const requestDraft = useCallback(async () => {
    const id = await ensureDraftJob();
    await draftRequest.mutateAsync({ jobId: id, body: { mode: "auto", keep_operator_edits: false } });
    dropJobScript(id);
    setSaveError(null);
    setSaveState(null);
  }, [ensureDraftJob, draftRequest, dropJobScript]);

  /** «В рендер»: создать и запустить, либо сохранить текст и запустить черновик / доработку. */
  const sendToRender = useCallback(async (): Promise<Job> => {
    if (phase === "local") {
      if (boundsDirty) await applyBounds();
      return createJob.mutateAsync({ candidateId: candidate.id, body: jobOptions(true) });
    }
    if (!liveJobId) throw new Error("Ролик ещё грузится");
    await flush();
    return startJob.mutateAsync(liveJobId);
  }, [phase, boundsDirty, applyBounds, createJob, candidate.id, jobOptions, liveJobId, flush, startJob]);

  /** Отменить черновик ролика: текст возвращается в браузер, границы снова правятся. */
  const cancelDraftJob = useCallback(async () => {
    if (!liveJobId) return;
    await flush().catch(() => undefined);
    setScript(candidate.id, useClipEditor.getState().jobScripts[liveJobId]?.script ?? serverDraft);
    await cancelJob.mutateAsync(liveJobId);
    dropJobScript(liveJobId);
  }, [liveJobId, flush, setScript, candidate.id, serverDraft, cancelJob, dropJobScript]);

  // ---------------------------------------------------------------- оценка оригинальности

  const sameRange = !boundsDirty;
  const estimateTarget =
    phase === "local"
      ? { kind: "candidate" as const, id: candidate.id }
      : liveJobId
        ? { kind: "job" as const, id: liveJobId }
        : null;
  const estimateBody: OriginalityEstimateRequest = useMemo(() => {
    if (phase === "local") {
      return {
        script: isDraftEmpty(value) ? null : bodyFromDraft(value),
        voice: target.voice,
        start_sec: sameRange ? null : Math.round(draftTimes.start * 1000) / 1000,
        end_sec: sameRange ? null : Math.round(draftTimes.end * 1000) / 1000,
        channel_id: target.channelId,
      };
    }
    return { script: isDraftEmpty(value) ? null : bodyFromDraft(value) };
  }, [phase, value, target.voice, target.channelId, sameRange, draftTimes.start, draftTimes.end]);
  const debouncedBody = useDebounced(estimateBody, 700);
  const estimate = useOriginalityEstimate(estimateTarget, debouncedBody, {
    enabled: canOperate && source.status === "ready" && phase !== "loading",
  });

  // QC меряет финал (исходник + авторский слой): 20–58 с, иначе провал после тяжёлого рендера.
  const finalBlock = finalDurationBlocker(estimate.data?.summary.duration_sec);
  if (finalBlock) blockers.push(finalBlock);

  // «Мой голос» без записей: рендер озвучит недостающие реплики синтезом (stage_author_voice).
  const notices: string[] = [];
  const voiceMode = phase === "local" ? target.voice : (authorVoice.data?.voice ?? target.voice);
  if (voiceMode === "operator_recording") {
    if (phase === "local") {
      notices.push(
        "«Мой голос»: записей ещё нет — рендер озвучит реплики синтезом (TTS), в пакете будет AI-метка. " +
          "Создайте черновик ролика и запишите реплики.",
      );
    } else if (authorVoice.data) {
      const missing = authorVoice.data.segments
        .filter((item) => !!item.text && !(item.kind === "operator" && item.status === "ready"))
        .map((item) => SEGMENT_LABELS[item.segment]);
      if (missing.length)
        notices.push(
          `«Мой голос»: не записаны ${missing.join(", ")} — рендер озвучит их синтезом (TTS), в пакете будет AI-метка.`,
        );
    }
  }

  return {
    phase,
    job: job.data ?? null,
    jobLoading: job.isPending && !!jobId,
    deadJob: dead ? (job.data ?? null) : null,
    liveJobId,
    authorScript: authorScript.data ?? null,
    authorVoice: authorVoice.data ?? null,
    value,
    onChange,
    editable,
    saveState:
      phase === "local"
        ? null
        : jobDraft
          ? saveState === "saving" || saveState === "error"
            ? saveState
            : "dirty"
          : saveState,
    saveError,
    resolveConflict,
    flush,
    drafting: draftRequest.isPending || !!authorScript.data?.drafting_task_id,
    requestDraft,
    ensureDraftJob,
    creatingJob: createJob.isPending,
    sendToRender,
    sending: createJob.isPending || startJob.isPending,
    cancelDraftJob,
    cancelling: cancelJob.isPending,
    blockers,
    notices,
    limitErrors,
    estimate: estimate.data ?? null,
    estimateError: estimate.error,
    estimateLoading: estimate.isFetching,
  };
}
