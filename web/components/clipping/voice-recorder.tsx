"use client";

import { Mic, MicOff, RotateCcw, Send, Square, Trash } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Badge, Button, StatusBadge } from "@/components/ui";
import type { Schemas } from "@/lib/api/client";
import { errorMessage, isApiError } from "@/lib/api/errors";
import { mediaSrc } from "@/lib/api/media";
import { cn } from "@/lib/cn";
import { AUTHOR_VOICE_MAX_BYTES, useDeleteAuthorVoice, useUploadAuthorVoice } from "@/lib/queries/clipping";

import { estimateSpeechSec, formatSeconds } from "./clip-math";
import { SEGMENT_LABELS, VOICE_KIND_LABELS, VOICE_STATUS_META, VOICE_TIMING_LABELS } from "./labels";

type Segment = Schemas["AuthorVoiceSegment"];
type SegmentState = Schemas["AuthorVoiceSegmentState"];

/** Запись длиннее не нужна: реплика хука — до 160 символов (~12 с), вставки — до 220. */
const MAX_RECORD_SEC = 60;
const MIME_CANDIDATES = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/webm", "audio/ogg"];

function pickMime(): string | undefined {
  if (typeof MediaRecorder === "undefined") return undefined;
  return MIME_CANDIDATES.find((mime) => {
    try {
      return MediaRecorder.isTypeSupported(mime);
    } catch {
      return false;
    }
  });
}

export function recorderSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof MediaRecorder !== "undefined" &&
    !!navigator.mediaDevices &&
    typeof navigator.mediaDevices.getUserMedia === "function"
  );
}

type Phase = "idle" | "recording" | "recorded";

/**
 * Запись своего голоса на реплику (§6.3.6 `author_voice`, MediaRecorder). Живой голос автора —
 * лучший ответ на «синтетический голос» в отчёте об оригинальности. Порядок: записать → послушать →
 * перезаписать или отправить; сервер срезает тишину по краям и считает пословные тайминги (ASR).
 * Удаление записи возвращает реплику синтезу (TTS).
 */
export function VoiceSegmentRecorder({
  jobId,
  segment,
  text,
  state,
  disabled,
  onBeforeUpload,
}: {
  jobId: number | null;
  segment: Segment;
  text: string;
  state: SegmentState | undefined;
  disabled?: boolean;
  /** Сохранить текст сценария перед отправкой записи (чтобы запись совпадала с текстом). */
  onBeforeUpload?: () => Promise<void>;
}) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [blob, setBlob] = useState<Blob | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const upload = useUploadAuthorVoice();
  const remove = useDeleteAuthorVoice();

  const stopTracks = () => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = null;
  };

  useEffect(
    () => () => {
      const recorder = recorderRef.current;
      if (recorder && recorder.state === "recording") recorder.stop();
      streamRef.current?.getTracks().forEach((track) => track.stop());
      if (timerRef.current) clearInterval(timerRef.current);
    },
    [],
  );

  useEffect(
    () => () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    },
    [previewUrl],
  );

  const start = async () => {
    setError(null);
    if (!recorderSupported()) {
      setError("Браузер не умеет записывать звук (нужен HTTPS и MediaRecorder)");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      streamRef.current = stream;
      const mimeType = pickMime();
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      const chunks: BlobPart[] = [];
      recorder.ondataavailable = (event) => {
        if (event.data.size) chunks.push(event.data);
      };
      recorder.onstop = () => {
        stopTracks();
        const result = new Blob(chunks, { type: recorder.mimeType || mimeType || "audio/webm" });
        setBlob(result);
        setPreviewUrl((old) => {
          if (old) URL.revokeObjectURL(old);
          return URL.createObjectURL(result);
        });
        setPhase("recorded");
      };
      recorderRef.current = recorder;
      recorder.start(250);
      setElapsed(0);
      setPhase("recording");
      const started = Date.now();
      timerRef.current = setInterval(() => {
        const sec = (Date.now() - started) / 1000;
        setElapsed(sec);
        if (sec >= MAX_RECORD_SEC && recorder.state === "recording") recorder.stop();
      }, 200);
    } catch (err) {
      stopTracks();
      const name = err instanceof DOMException ? err.name : "";
      setError(
        name === "NotAllowedError"
          ? "Нет доступа к микрофону: разрешите его в браузере"
          : name === "NotFoundError"
            ? "Микрофон не найден"
            : "Не удалось начать запись",
      );
      setPhase("idle");
    }
  };

  const stop = () => {
    if (recorderRef.current?.state === "recording") recorderRef.current.stop();
  };

  const retake = () => {
    setBlob(null);
    setPreviewUrl((old) => {
      if (old) URL.revokeObjectURL(old);
      return null;
    });
    setPhase("idle");
    void start();
  };

  const send = async () => {
    if (!blob || !jobId) return;
    if (blob.size > AUTHOR_VOICE_MAX_BYTES) {
      setError("Запись больше 20 МБ — сократите реплику");
      return;
    }
    setError(null);
    try {
      await onBeforeUpload?.();
      const ext = blob.type.includes("ogg") ? "ogg" : blob.type.includes("wav") ? "wav" : "webm";
      await upload.mutateAsync({ jobId, segment, audio: blob, filename: `${segment}.${ext}` });
      toast.success(`${SEGMENT_LABELS[segment]}: запись принята`, {
        description: "Срезаем тишину и считаем тайминги.",
      });
      setBlob(null);
      setPreviewUrl((old) => {
        if (old) URL.revokeObjectURL(old);
        return null;
      });
      setPhase("idle");
    } catch (err) {
      if (isApiError(err) && err.status === 413) setError("Запись больше допустимого размера (20 МБ)");
      else if (isApiError(err) && err.status === 415)
        setError("Сервер не принял формат записи (нужен webm, ogg или wav)");
      else setError(errorMessage(err));
    }
  };

  const drop = async () => {
    if (!jobId) return;
    try {
      await remove.mutateAsync({ jobId, segment });
      toast.success(`${SEGMENT_LABELS[segment]}: запись удалена, реплика озвучится синтезом`);
    } catch (err) {
      toast.error(errorMessage(err));
    }
  };

  const serverAudio = state?.status === "ready" ? mediaSrc(state.audio_url) : undefined;
  const operatorReady = state?.kind === "operator" && state.status === "ready";
  const expected = estimateSpeechSec(text);
  const noText = !text.trim();

  return (
    <div
      className={cn("flex flex-col gap-1.5 rounded-md border border-border px-2.5 py-2", noText && "opacity-60")}
      data-testid={`voice-${segment}`}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-xs font-medium text-fg">{SEGMENT_LABELS[segment]}</span>
        {state && state.status !== "missing" ? <StatusBadge status={state.status} map={VOICE_STATUS_META} /> : null}
        {state?.kind ? (
          <Badge variant={state.kind === "operator" ? "positive" : "neutral"}>{VOICE_KIND_LABELS[state.kind]}</Badge>
        ) : null}
        {state?.duration_sec ? (
          <span className="font-mono text-2xs text-fg-muted">{formatSeconds(state.duration_sec)}</span>
        ) : null}
        {state?.timing ? <span className="text-2xs text-fg-muted">{VOICE_TIMING_LABELS[state.timing]}</span> : null}
        {!noText && expected ? (
          <span className="ml-auto font-mono text-2xs text-fg-muted" title="Оценка по длине текста">
            ≈ {formatSeconds(expected)}
          </span>
        ) : null}
      </div>
      {noText ? <p className="text-2xs text-fg-muted">Сначала напишите текст реплики.</p> : null}
      {state?.error ? <p className="text-2xs text-negative">{state.error}</p> : null}
      {serverAudio && phase === "idle" ? (
        <audio controls preload="none" src={serverAudio} className="h-8 w-full" />
      ) : null}
      {previewUrl && phase === "recorded" ? (
        <audio controls src={previewUrl} className="h-8 w-full" data-testid={`voice-preview-${segment}`} />
      ) : null}
      <div className="flex flex-wrap items-center gap-1.5">
        {phase === "recording" ? (
          <>
            <span className="flex items-center gap-1 text-xs text-negative" role="status">
              <span className="tally-pulse h-2 w-2 rounded-full bg-negative" aria-hidden="true" />
              Запись {formatSeconds(elapsed)}
            </span>
            <Button variant="destructive" size="xs" onClick={stop}>
              <Square className="h-3 w-3" aria-hidden="true" />
              Стоп
            </Button>
          </>
        ) : phase === "recorded" ? (
          <>
            <Button variant="outline" size="xs" onClick={retake} disabled={disabled}>
              <RotateCcw className="h-3 w-3" aria-hidden="true" />
              Перезаписать
            </Button>
            <Button size="xs" onClick={() => void send()} disabled={disabled || upload.isPending || !jobId}>
              <Send className="h-3 w-3" aria-hidden="true" />
              {upload.isPending ? "Отправляем…" : "Отправить"}
            </Button>
          </>
        ) : (
          <Button
            variant="outline"
            size="xs"
            onClick={() => void start()}
            disabled={disabled || noText || !jobId || state?.status === "processing"}
            data-testid={`record-${segment}`}
          >
            <Mic className="h-3 w-3" aria-hidden="true" />
            {operatorReady ? "Записать заново" : "Записать своим голосом"}
          </Button>
        )}
        {operatorReady && phase === "idle" ? (
          <Button variant="ghost" size="xs" onClick={() => void drop()} disabled={disabled || remove.isPending}>
            <Trash className="h-3 w-3" aria-hidden="true" />
            Удалить запись
          </Button>
        ) : null}
        {!recorderSupported() ? (
          <span className="flex items-center gap-1 text-2xs text-fg-muted">
            <MicOff className="h-3 w-3" aria-hidden="true" />
            запись недоступна в этом браузере
          </span>
        ) : null}
      </div>
      {error ? (
        <p role="alert" className="text-2xs text-negative">
          {error}
        </p>
      ) : null}
    </div>
  );
}
