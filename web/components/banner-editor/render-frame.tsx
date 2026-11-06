"use client";

import { Camera, Download, Layers, LoaderCircle, Maximize2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Badge, Button, Dialog, PillToggle } from "@/components/ui";
import type { Schemas } from "@/lib/api/client";
import { ApiError, errorMessage } from "@/lib/api/errors";
import { type BannerPreviewRequest, fetchPreviewFrame, type PreviewFrame } from "@/lib/queries/banners";

import { describeValidationError } from "./draft-issues";
import { CheckField, PanelSection, RangeField } from "./fields";
import { formatSec, PREVIEW_BACKGROUND_LABELS, PREVIEW_MOMENT_LABELS } from "./labels";

type Moment = Schemas["PreviewMoment"];
type Background = keyof typeof PREVIEW_BACKGROUND_LABELS;

const MOMENTS = (Object.keys(PREVIEW_MOMENT_LABELS) as Moment[]).map((value) => ({
  value,
  label: PREVIEW_MOMENT_LABELS[value],
}));
const BACKGROUNDS = (Object.keys(PREVIEW_BACKGROUND_LABELS) as Background[]).map((value) => ({
  value,
  label: PREVIEW_BACKGROUND_LABELS[value],
}));

/**
 * «Кадр из рендера»: сервер собирает PNG тем же кодом, что финальный ролик (`banner_overlay`), и
 * отдаёт фактическое время кадра в `X-Preview-T-Sec`. Кадр можно наложить на холст для сверки.
 */
export function RenderFramePanel({
  buildRequest,
  t,
  disabled,
  disabledReason,
  overlay,
  onOverlayChange,
  overlayOpacity,
  onOverlayOpacity,
}: {
  buildRequest: (opts: {
    moment: Moment;
    background: Background;
    size: Schemas["PreviewSize"];
    t: number;
  }) => BannerPreviewRequest | null;
  t: number;
  disabled: boolean;
  disabledReason?: string;
  overlay: boolean;
  onOverlayChange: (on: boolean, url: string | null) => void;
  overlayOpacity: number;
  onOverlayOpacity: (value: number) => void;
}) {
  const [moment, setMoment] = useState<Moment>("impulse");
  const [background, setBackground] = useState<Background>("sample");
  const [full, setFull] = useState(false);
  const [frame, setFrame] = useState<PreviewFrame | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [zoomed, setZoomed] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      abortRef.current?.abort();
      if (retryRef.current) clearTimeout(retryRef.current);
    },
    [],
  );
  useEffect(() => {
    const url = frame?.url;
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [frame?.url]);

  const render = async (attempt = 0) => {
    const request = buildRequest({ moment, background, size: full ? "full" : "half", t });
    if (!request) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setPending(true);
    setError(null);
    try {
      const next = await fetchPreviewFrame(request, controller.signal);
      setFrame(next);
      if (overlay) onOverlayChange(true, next.url);
    } catch (cause) {
      if (cause instanceof DOMException && cause.name === "AbortError") return;
      if (cause instanceof ApiError && cause.status === 429 && attempt < 2) {
        const wait = Number(cause.details.retry_after_sec) || 3;
        setError(`Другой кадр ещё рендерится — повторю через ${wait} с`);
        retryRef.current = setTimeout(() => void render(attempt + 1), wait * 1000);
        return;
      }
      const message = describeValidationError(cause) ?? errorMessage(cause);
      setError(message);
      toast.error("Кадр не получился", { description: message });
    } finally {
      if (abortRef.current === controller) setPending(false);
    }
  };

  return (
    <PanelSection
      title="Кадр из рендера"
      testId="render-frame"
      actions={
        frame?.tSec !== null && frame?.tSec !== undefined ? (
          <Badge variant="machine" outline>
            t = {formatSec(frame.tSec, 2)}
          </Badge>
        ) : null
      }
    >
      <div className="flex flex-col gap-1.5">
        <PillToggle label="Момент кадра" options={MOMENTS} value={moment} onChange={setMoment} />
        <PillToggle label="Фон" options={BACKGROUNDS} value={background} onChange={setBackground} />
        <CheckField label="Полный размер 1080×1920" hint="Иначе 540×960, быстрее" checked={full} onChange={setFull} />
      </div>
      <Button
        onClick={() => void render()}
        disabled={disabled || pending}
        data-testid="render-frame-button"
        title={disabled ? disabledReason : "Собрать PNG тем же кодом, что финальный ролик"}
      >
        {pending ? (
          <LoaderCircle className="h-4 w-4 animate-spin" aria-hidden="true" />
        ) : (
          <Camera className="h-4 w-4" aria-hidden="true" />
        )}
        {pending ? "Рендерю кадр…" : "Кадр из рендера"}
      </Button>
      {disabled && disabledReason ? <p className="text-2xs text-fg-muted">{disabledReason}</p> : null}
      {error ? (
        <p role="alert" className="text-xs text-negative" data-testid="render-frame-error">
          {error}
        </p>
      ) : null}
      {frame ? (
        <div className="flex flex-col gap-1.5">
          <button
            type="button"
            className="group relative mx-auto block w-40 overflow-hidden rounded-none border border-border bg-video"
            onClick={() => setZoomed(true)}
            title="Открыть в полный размер"
          >
            {/* eslint-disable-next-line @next/next/no-img-element -- blob: из ответа API, next/image не нужен */}
            <img
              src={frame.url}
              alt={`Кадр из рендера, ${PREVIEW_MOMENT_LABELS[frame.request.moment]}`}
              className="block h-auto w-full"
              data-testid="render-frame-image"
              data-width={frame.width ?? undefined}
              data-height={frame.height ?? undefined}
              data-t={frame.tSec ?? undefined}
            />
            <Maximize2
              className="absolute top-1 right-1 h-3.5 w-3.5 text-white opacity-0 transition-opacity group-hover:opacity-80"
              aria-hidden="true"
            />
          </button>
          <p className="tabular text-center font-mono text-2xs text-fg-muted">
            PNG {frame.width ?? "?"}×{frame.height ?? "?"} · {PREVIEW_MOMENT_LABELS[frame.request.moment]}
          </p>
          <div className="flex items-center justify-between gap-2">
            <CheckField
              label={
                <span className="inline-flex items-center gap-1">
                  <Layers className="h-3 w-3" aria-hidden="true" />
                  Наложить на холст
                </span>
              }
              checked={overlay}
              onChange={(on) => onOverlayChange(on, on ? frame.url : null)}
            />
            <a
              href={frame.url}
              download={`banner-frame-${frame.request.moment}.png`}
              className="inline-flex items-center gap-1 text-2xs text-accent hover:underline"
            >
              <Download className="h-3 w-3" aria-hidden="true" />
              PNG
            </a>
          </div>
          {overlay ? (
            <RangeField
              label="Прозрачность наложения"
              value={overlayOpacity}
              min={0.1}
              max={1}
              step={0.05}
              format={(value) => `${Math.round(value * 100)}%`}
              onChange={onOverlayOpacity}
            />
          ) : null}
          <Dialog
            open={zoomed}
            onClose={() => setZoomed(false)}
            title="Кадр из рендера"
            description={`${PREVIEW_MOMENT_LABELS[frame.request.moment]} · t = ${formatSec(frame.tSec, 2)} · ${frame.width ?? "?"}×${frame.height ?? "?"}`}
            className="max-w-md"
          >
            {/* eslint-disable-next-line @next/next/no-img-element -- blob: из ответа API */}
            <img src={frame.url} alt="Кадр из рендера" className="mx-auto block max-h-[70vh] w-auto bg-video" />
          </Dialog>
        </div>
      ) : null}
    </PanelSection>
  );
}
