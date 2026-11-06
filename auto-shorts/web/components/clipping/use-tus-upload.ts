"use client";

/**
 * Возобновляемая загрузка tus 1.0.0 (docs/ARCHITECTURE.md §5.2 «Загрузки», api/schemas/uploads.py).
 *
 * - tus-js-client грузится только по требованию (`import()`), в оболочку не попадает;
 * - чанк 16 МБ (`TUS_CHUNK_MAX_BYTES`, nginx `client_max_body_size 17m`);
 * - cookie-сессия требует `X-CSRF-Token` на POST/PATCH/DELETE — ставим на все запросы;
 * - отпечаток файла хранится в localStorage (так делает tus-js-client): тот же файл после
 *   перезагрузки вкладки продолжается с того же смещения (HEAD);
 * - 409 с `details.locked=true` — другой PATCH ещё идёт: повторяем через HEAD; 412/413/415/422
 *   не повторяем; 507 — нет места на диске.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { CSRF_HEADER, csrfToken } from "@/lib/api/client";

export const TUS_ENDPOINT = "/api/v1/uploads/tus";
export const TUS_CHUNK_SIZE = 16 * 1024 * 1024;
/** `Tus-Max-Size` по умолчанию (settings.upload_max_gb = 6). */
export const TUS_MAX_BYTES = 6 * 1024 ** 3;
const UPLOAD_ID_RE = /^[a-f0-9]{32}$/;

export type UploadPurpose = "source" | "rights_doc" | "asset";
export type UploadPhase = "idle" | "starting" | "uploading" | "paused" | "complete" | "error";

export interface UploadState {
  phase: UploadPhase;
  sent: number;
  total: number;
  uploadId: string | null;
  /** Загрузка продолжена с прошлого раза (найден отпечаток). */
  resumed: boolean;
  error: string | null;
  /** Скорость, байт/с (скользящая), для оценки оставшегося времени. */
  rate: number | null;
}

const IDLE: UploadState = { phase: "idle", sent: 0, total: 0, uploadId: null, resumed: false, error: null, rate: null };

interface TusResponseLike {
  getStatus(): number;
  getBody(): string;
}

function describeTusError(error: unknown): string {
  const response = (error as { originalResponse?: TusResponseLike | null })?.originalResponse ?? null;
  const status = response?.getStatus() ?? 0;
  let serverMessage: string | null = null;
  try {
    const body = response?.getBody();
    const parsed = body ? (JSON.parse(body) as { error?: { message?: string } }) : null;
    serverMessage = parsed?.error?.message ?? null;
  } catch {
    serverMessage = null;
  }
  switch (status) {
    case 0:
      return "Нет связи с сервером. Загрузка продолжится с того же места, когда связь вернётся.";
    case 401:
      return "Сессия истекла: войдите снова и выберите тот же файл — загрузка продолжится.";
    case 403:
      return serverMessage ?? "Недостаточно прав для загрузки";
    case 409:
      return serverMessage ?? "Смещение загрузки разошлось с сервером";
    case 410:
      return "Сессия загрузки истекла (24 ч без чанков). Начните заново.";
    case 412:
      return "Сервер не поддерживает эту версию tus";
    case 413:
      return serverMessage ?? "Файл больше допустимого размера";
    case 415:
      return "Сервер не принял тип содержимого чанка";
    case 507:
      return serverMessage ?? "Нет места на диске сервера";
    default:
      return serverMessage ?? (error instanceof Error ? error.message : "Ошибка загрузки");
  }
}

function uploadIdFromUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  const last = url.split("?")[0].replace(/\/+$/, "").split("/").pop() ?? "";
  return UPLOAD_ID_RE.test(last) ? last : null;
}

interface TusUploadLike {
  url: string | null;
  start(): void;
  abort(shouldTerminate?: boolean): Promise<void>;
  findPreviousUploads(): Promise<Array<{ uploadUrl: string | null; creationTime: string }>>;
  resumeFromPreviousUpload(previous: { uploadUrl: string | null; creationTime: string }): void;
}

/** Хук загрузки одного файла. `start(file)` — новая или продолженная загрузка. */
export function useTusUpload(purpose: UploadPurpose) {
  const [state, setState] = useState<UploadState>(IDLE);
  const uploadRef = useRef<TusUploadLike | null>(null);
  const rateRef = useRef<{ at: number; bytes: number } | null>(null);

  useEffect(
    () => () => {
      // Уход со страницы — пауза, не удаление: файл можно будет дозагрузить.
      void uploadRef.current?.abort(false);
      uploadRef.current = null;
    },
    [],
  );

  const start = useCallback(
    async (file: File) => {
      void uploadRef.current?.abort(false);
      uploadRef.current = null;
      if (file.size <= 0) {
        setState({ ...IDLE, phase: "error", error: "Файл пустой" });
        return;
      }
      if (file.size > TUS_MAX_BYTES) {
        setState({
          ...IDLE,
          phase: "error",
          total: file.size,
          error: "Файл больше 6 ГБ — такой источник не принимается",
        });
        return;
      }
      setState({ ...IDLE, phase: "starting", total: file.size });
      let tus: typeof import("tus-js-client");
      try {
        tus = await import("tus-js-client");
      } catch {
        setState({ ...IDLE, phase: "error", error: "Не удалось загрузить модуль загрузки" });
        return;
      }
      rateRef.current = null;
      const upload = new tus.Upload(file, {
        endpoint: TUS_ENDPOINT,
        chunkSize: TUS_CHUNK_SIZE,
        retryDelays: [0, 1_000, 3_000, 5_000, 10_000, 20_000],
        metadata: { filename: file.name, filetype: file.type || "application/octet-stream", purpose },
        removeFingerprintOnSuccess: true,
        // CSRF — только здесь, не в `headers`: XHR склеивает повторный заголовок в «токен, токен».
        // Токен читаем на каждый запрос: он мог смениться после повторного входа.
        onBeforeRequest: (req) => {
          const token = csrfToken();
          if (token) req.setHeader(CSRF_HEADER, token);
        },
        onUploadUrlAvailable: () => {
          setState((prev) => ({ ...prev, uploadId: uploadIdFromUrl(upload.url) }));
        },
        onProgress: (sent, total) => {
          const now = Date.now();
          const last = rateRef.current;
          let rate: number | null = null;
          if (last && now > last.at) {
            const instant = ((sent - last.bytes) * 1000) / (now - last.at);
            rate = instant > 0 ? instant : null;
          }
          if (!last || now - last.at > 1_000) rateRef.current = { at: now, bytes: sent };
          setState((prev) => ({
            ...prev,
            phase: prev.phase === "paused" ? "paused" : "uploading",
            sent,
            total,
            rate: rate ?? prev.rate,
          }));
        },
        onSuccess: () => {
          setState((prev) => ({
            ...prev,
            phase: "complete",
            sent: prev.total,
            uploadId: uploadIdFromUrl(upload.url) ?? prev.uploadId,
            error: null,
          }));
        },
        onError: (error) => {
          setState((prev) => ({ ...prev, phase: "error", error: describeTusError(error) }));
        },
        onShouldRetry: (error) => {
          const status = error.originalResponse?.getStatus() ?? 0;
          if (status === 409) {
            try {
              const body = JSON.parse(error.originalResponse?.getBody() ?? "{}") as {
                error?: { details?: { locked?: boolean } };
              };
              return body.error?.details?.locked === true;
            } catch {
              return false;
            }
          }
          if (status === 423 || status === 429) return true;
          return status === 0 || status >= 500 ? status !== 507 : false;
        },
      });
      uploadRef.current = upload as unknown as TusUploadLike;
      try {
        const previous = await upload.findPreviousUploads();
        const latest = previous
          .filter((item) => item.uploadUrl)
          .sort((a, b) => b.creationTime.localeCompare(a.creationTime))[0];
        if (latest) {
          upload.resumeFromPreviousUpload(latest);
          setState((prev) => ({ ...prev, resumed: true, uploadId: uploadIdFromUrl(latest.uploadUrl) }));
        }
      } catch {
        // нет доступа к localStorage — просто новая загрузка
      }
      setState((prev) => ({ ...prev, phase: "uploading" }));
      upload.start();
    },
    [purpose],
  );

  const pause = useCallback(() => {
    const upload = uploadRef.current;
    if (!upload) return;
    void upload.abort(false);
    setState((prev) => (prev.phase === "uploading" ? { ...prev, phase: "paused", rate: null } : prev));
  }, []);

  const resume = useCallback(() => {
    const upload = uploadRef.current;
    if (!upload) return;
    rateRef.current = null;
    setState((prev) => ({ ...prev, phase: "uploading", error: null }));
    upload.start();
  }, []);

  /** Отменить и удалить с сервера (tus termination). */
  const cancel = useCallback(async () => {
    const upload = uploadRef.current;
    uploadRef.current = null;
    setState(IDLE);
    if (upload) {
      try {
        await upload.abort(true);
      } catch {
        // сессия уже истекла или удалена
      }
    }
  }, []);

  const reset = useCallback(() => {
    void uploadRef.current?.abort(false);
    uploadRef.current = null;
    setState(IDLE);
  }, []);

  return { state, start, pause, resume, cancel, reset };
}

/** Проценты загрузки 0–100. */
export function uploadPct(state: UploadState): number {
  if (!state.total) return 0;
  return Math.min(100, Math.round((state.sent / state.total) * 1000) / 10);
}

/** «≈ 3 мин» до конца загрузки. */
export function uploadEta(state: UploadState): string | null {
  if (state.phase !== "uploading" || !state.rate || state.rate <= 0) return null;
  const left = (state.total - state.sent) / state.rate;
  if (!Number.isFinite(left) || left <= 0) return null;
  if (left < 60) return `≈ ${Math.ceil(left)} с`;
  if (left < 3600) return `≈ ${Math.ceil(left / 60)} мин`;
  return `≈ ${(left / 3600).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} ч`;
}
