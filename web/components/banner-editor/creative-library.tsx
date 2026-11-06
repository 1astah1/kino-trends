"use client";

import { Film, ImagePlus, LoaderCircle, Trash2, Upload, X } from "lucide-react";
import { type DragEvent, useRef, useState } from "react";
import { toast } from "sonner";

import { Button, StatusBadge } from "@/components/ui";
import { ApiError, ConflictError, errorMessage } from "@/lib/api/errors";
import { mediaSrc } from "@/lib/api/media";
import { cn } from "@/lib/cn";
import {
  type BannerCreative,
  CREATIVE_ACCEPT,
  MAX_CREATIVE_BYTES,
  uploadCreative,
  type UploadProgress,
  uploadRejectCode,
  useCreatives,
  useDeleteCreative,
} from "@/lib/queries/creatives";
import { creativeKeys } from "@/lib/queries/creatives";
import { useQueryClient } from "@tanstack/react-query";

import { CREATIVE_FORMAT_LABELS, CREATIVE_STATUS_META, formatBytes, formatSec, rejectCodeLabel } from "./labels";

/** Сообщение об отказе загрузки по-русски: код файла (`asset_error`) важнее общего текста сервера. */
export function uploadErrorMessage(error: unknown): string {
  const code = uploadRejectCode(error);
  const byCode = rejectCodeLabel(code);
  if (byCode) return byCode;
  if (error instanceof ApiError) {
    if (error.status === 413) return "Файл больше 50 МБ";
    if (error.status === 415) return rejectCodeLabel("unsupported_format") ?? error.message;
    if (error.status === 507) return "На сервере кончается место — загрузки временно не принимаются";
    if (error.status === 403) return "Загружать креативы могут оператор и владелец";
  }
  return errorMessage(error);
}

interface UploadState {
  name: string;
  size: number;
  progress: UploadProgress | null;
  error: string | null;
  code: string | null;
  controller: AbortController | null;
}

/**
 * Загрузка креатива с прогрессом (≤ 50 МБ, статика — сразу «готов», анимация — задачей нормализации)
 * и выбор из библиотеки. `onPick` — поставить креатив в вариант.
 */
export function CreativeUploader({
  productId,
  onUploaded,
  compact,
  disabled,
}: {
  productId?: string | null;
  onUploaded: (creative: BannerCreative, deduplicated: boolean) => void;
  compact?: boolean;
  disabled?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [state, setState] = useState<UploadState | null>(null);
  const queryClient = useQueryClient();

  const start = async (file: File) => {
    if (disabled) return;
    if (file.size > MAX_CREATIVE_BYTES) {
      setState({
        name: file.name,
        size: file.size,
        progress: null,
        error: "Файл больше 50 МБ",
        code: "file_too_large",
        controller: null,
      });
      return;
    }
    const controller = new AbortController();
    setState({
      name: file.name,
      size: file.size,
      progress: { loaded: 0, total: file.size, pct: 0 },
      error: null,
      code: null,
      controller,
    });
    try {
      const result = await uploadCreative({
        file,
        productId: productId ?? null,
        signal: controller.signal,
        onProgress: (progress) => setState((prev) => (prev ? { ...prev, progress } : prev)),
      });
      queryClient.setQueryData(creativeKeys.detail(result.creative.sha256), result.creative);
      void queryClient.invalidateQueries({ queryKey: creativeKeys.all });
      setState(null);
      if (result.deduplicated) toast.info("Такой файл уже есть в библиотеке", { description: result.creative.name });
      else if (result.creative.status === "processing")
        toast.info("Анимация принята", { description: "Перекодирую в WebM с альфой — это займёт немного времени." });
      onUploaded(result.creative, result.deduplicated);
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        setState(null);
        return;
      }
      setState((prev) =>
        prev ? { ...prev, error: uploadErrorMessage(error), code: uploadRejectCode(error), controller: null } : prev,
      );
    }
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragOver(false);
    const file = event.dataTransfer.files?.[0];
    if (file) void start(file);
  };

  const uploading = !!state && !state.error && !!state.controller;

  return (
    <div className="flex flex-col gap-1.5" data-testid="creative-uploader">
      <div
        role="button"
        tabIndex={disabled ? -1 : 0}
        aria-disabled={disabled || undefined}
        aria-label="Загрузить креатив: перетащите файл или нажмите"
        onClick={() => !disabled && !uploading && inputRef.current?.click()}
        onKeyDown={(event) => {
          if ((event.key === "Enter" || event.key === " ") && !disabled && !uploading) {
            event.preventDefault();
            inputRef.current?.click();
          }
        }}
        onDragOver={(event) => {
          event.preventDefault();
          if (!disabled) setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        className={cn(
          "safe-frame flex cursor-pointer flex-col items-center justify-center gap-1.5 rounded-md border border-dashed text-center transition-colors duration-150",
          compact ? "px-3 py-3" : "px-4 py-8",
          dragOver ? "border-accent bg-accent/10" : "border-border hover:border-accent/60 hover:bg-surface-2/60",
          disabled && "cursor-not-allowed opacity-60",
        )}
      >
        <Upload className={cn("text-accent", compact ? "h-4 w-4" : "h-6 w-6")} aria-hidden="true" />
        <span className={cn("font-medium text-fg", compact ? "text-xs" : "text-sm")}>
          {compact ? "Загрузить креатив" : "Перетащите креатив сюда или нажмите"}
        </span>
        <span className="text-2xs text-fg-muted">
          PNG, JPG, WebP (с прозрачностью) · GIF, APNG, WebM с альфой, MP4 · до 50 МБ
        </span>
        <input
          ref={inputRef}
          type="file"
          accept={CREATIVE_ACCEPT}
          className="sr-only"
          tabIndex={-1}
          data-testid="creative-file-input"
          disabled={disabled}
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = "";
            if (file) void start(file);
          }}
        />
      </div>
      {state ? (
        <div
          className={cn(
            "flex flex-col gap-1 rounded-md border p-2 text-xs",
            state.error ? "border-negative/40 bg-negative/10" : "border-border bg-surface-2/50",
          )}
          data-testid="upload-status"
          role={state.error ? "alert" : "status"}
        >
          <div className="flex items-center gap-2">
            {state.error ? null : <LoaderCircle className="h-3.5 w-3.5 animate-spin text-machine" aria-hidden="true" />}
            <span className="min-w-0 flex-1 truncate font-medium text-fg" title={state.name}>
              {state.name}
            </span>
            <span className="tabular font-mono text-2xs text-fg-muted">{formatBytes(state.size)}</span>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={state.error ? "Скрыть" : "Отменить загрузку"}
              onClick={() => {
                state.controller?.abort();
                setState(null);
              }}
            >
              <X className="h-3.5 w-3.5" aria-hidden="true" />
            </Button>
          </div>
          {state.error ? (
            <p className="text-negative" data-testid="upload-error" data-code={state.code ?? undefined}>
              {state.error}
            </p>
          ) : (
            <>
              <div
                className="relative h-1.5 overflow-hidden rounded-full bg-surface-2"
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={Math.round(state.progress?.pct ?? 0)}
                aria-label="Загрузка креатива"
              >
                <span
                  className="absolute inset-y-0 left-0 rounded-full bg-accent transition-[width] duration-150"
                  style={{ width: `${state.progress?.pct ?? 0}%` }}
                />
              </div>
              <span className="tabular font-mono text-2xs text-fg-muted">
                {Math.round(state.progress?.pct ?? 0)}%
                {state.progress && state.progress.pct >= 100 ? " · сервер проверяет файл…" : ""}
              </span>
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}

export function CreativeThumb({ creative, className }: { creative: BannerCreative; className?: string }) {
  const src = mediaSrc(creative.thumb_url ?? creative.first_frame_url);
  return (
    <span
      className={cn(
        "relative flex items-center justify-center overflow-hidden bg-[repeating-conic-gradient(#2b2d31_0_25%,#3a3d42_0_50%)] bg-[length:12px_12px]",
        className,
      )}
    >
      {src ? (
        // eslint-disable-next-line @next/next/no-img-element -- миниатюра из API (immutable)
        <img src={src} alt="" className="max-h-full max-w-full object-contain" loading="lazy" />
      ) : creative.status === "processing" ? (
        <LoaderCircle className="h-4 w-4 animate-spin text-machine" aria-hidden="true" />
      ) : (
        <ImagePlus className="h-4 w-4 text-fg-muted" aria-hidden="true" />
      )}
      {creative.kind === "animated" ? (
        <Film className="absolute right-0.5 bottom-0.5 h-3 w-3 text-white drop-shadow" aria-label="анимация" />
      ) : null}
    </span>
  );
}

/** Библиотека креативов: выбор для варианта, удаление неиспользуемых. */
export function CreativeLibrary({
  productId,
  currentShas,
  onPick,
  readOnly,
}: {
  productId?: string | null;
  currentShas: readonly string[];
  onPick: (creative: BannerCreative) => void;
  readOnly: boolean;
}) {
  const [onlyProduct, setOnlyProduct] = useState(false);
  const creatives = useCreatives({ productId: onlyProduct ? productId : null });
  const remove = useDeleteCreative();
  const items = creatives.data?.items ?? [];

  const onDelete = async (creative: BannerCreative) => {
    try {
      await remove.mutateAsync(creative.sha256);
      toast.success("Креатив удалён из библиотеки", { description: creative.name });
    } catch (error) {
      if (error instanceof ConflictError) {
        const ids = Array.isArray(error.details.variant_ids)
          ? error.details.variant_ids.length
          : creative.used_by_variants;
        toast.warning("Креатив стоит в вариантах", { description: `Сначала замените его в вариантах (${ids}).` });
        return;
      }
      toast.error(errorMessage(error));
    }
  };

  return (
    <div className="flex flex-col gap-2" data-testid="creative-library">
      {productId ? (
        <label className="flex items-center gap-1.5 text-2xs text-fg-muted">
          <input
            type="checkbox"
            className="h-3 w-3 accent-[var(--accent)]"
            checked={onlyProduct}
            onChange={(event) => setOnlyProduct(event.target.checked)}
          />
          Только креативы этого продукта
        </label>
      ) : null}
      {creatives.isPending ? (
        <p className="text-xs text-fg-muted">Загрузка библиотеки…</p>
      ) : creatives.isError ? (
        <p className="text-xs text-negative">{errorMessage(creatives.error)}</p>
      ) : items.length === 0 ? (
        <p className="text-xs text-fg-muted">Библиотека пуста — загрузите первый креатив.</p>
      ) : (
        <ul className="grid grid-cols-2 gap-1.5">
          {items.map((creative) => {
            const current = currentShas.includes(creative.sha256);
            const ready = creative.status === "ready";
            return (
              <li
                key={creative.sha256}
                className={cn(
                  "group flex flex-col overflow-hidden rounded-md border bg-surface",
                  current ? "border-accent" : "border-border",
                )}
                data-testid="creative-card"
                data-sha={creative.sha256}
                data-status={creative.status}
              >
                <button
                  type="button"
                  className="flex flex-col text-left disabled:cursor-not-allowed"
                  disabled={readOnly || !ready || current}
                  onClick={() => onPick(creative)}
                  title={
                    current
                      ? "Уже в варианте"
                      : ready
                        ? "Поставить в вариант"
                        : creative.status === "processing"
                          ? "Ещё обрабатывается"
                          : (creative.error_message ?? rejectCodeLabel(creative.error_code) ?? "Не принят")
                  }
                >
                  <CreativeThumb creative={creative} className="h-16 w-full" />
                  <span className="flex flex-col gap-0.5 p-1.5">
                    <span className="truncate text-2xs font-medium text-fg" title={creative.name}>
                      {creative.name}
                    </span>
                    <span className="tabular truncate font-mono text-[10px] text-fg-muted">
                      {CREATIVE_FORMAT_LABELS[creative.source_format]} {creative.width ?? "?"}×{creative.height ?? "?"}
                      {creative.kind === "animated" && creative.duration_sec
                        ? ` · ${formatSec(creative.duration_sec)}`
                        : ""}
                    </span>
                    {creative.status !== "ready" ? (
                      <StatusBadge status={creative.status} map={CREATIVE_STATUS_META} className="self-start" />
                    ) : current ? (
                      <span className="text-[10px] text-accent">в варианте</span>
                    ) : null}
                  </span>
                </button>
                {!readOnly && creative.used_by_variants === 0 && !current ? (
                  <Button
                    variant="ghost"
                    size="xs"
                    className="h-5 rounded-none border-t border-border text-[10px] text-fg-muted opacity-70 hover:text-negative hover:opacity-100"
                    onClick={() => void onDelete(creative)}
                    aria-label={`Удалить креатив ${creative.name}`}
                  >
                    <Trash2 className="h-3 w-3" aria-hidden="true" />
                    Удалить
                  </Button>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
