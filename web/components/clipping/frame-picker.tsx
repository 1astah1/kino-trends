"use client";

/**
 * «Оформление» ролика при ручной нарезке: форма кадра (как у канала, авто, круг, скруглённая рамка,
 * обычный кадр без рамки), субтитры и тонкая настройка. Выбор уходит в `author_layer.frame` и
 * перекрывает профиль канала только у этого ролика; пустые поля — как у канала.
 */
import { RotateCcw } from "lucide-react";
import { type ReactNode, useId } from "react";

import { Select } from "@/components/ui";
import type { Schemas } from "@/lib/api/client";
import { cn } from "@/lib/cn";
import { useClipEditor } from "@/lib/store/clip-editor";

type Frame = Schemas["FrameOptions"];
type Shape = Frame["shape"];

const SHAPES: ReadonlyArray<{ value: Shape; label: string; hint: string }> = [
  { value: "channel", label: "Как у канала", hint: "Оформление из профиля канала" },
  { value: "auto", label: "Авто", hint: "Круг, если в кадре одно лицо; иначе скруглённая рамка" },
  { value: "circle", label: "Круг", hint: "Кадр в круге на живом фоне" },
  { value: "rounded", label: "Скруглённый", hint: "Кадр в рамке со скруглёнными углами на живом фоне" },
  { value: "none", label: "Обычный", hint: "Полный кадр 9:16 без рамки и живого фона" },
];

const ASPECTS: ReadonlyArray<{ value: NonNullable<Frame["aspect"]>; label: string }> = [
  { value: "square", label: "1:1 квадрат" },
  { value: "landscape", label: "4:3 горизонтальная" },
  { value: "portrait", label: "3:4 вертикальная" },
  { value: "wide", label: "16:9 широкая" },
];

const BACKGROUNDS: ReadonlyArray<{ value: NonNullable<Frame["bg_set"]>; label: string }> = [
  { value: "nature", label: "Природа" },
  { value: "night", label: "Ночь" },
];

/** Значения для превью и ползунков, пока поле не тронуто (у канала может быть своё). */
const PREVIEW = { size: 880, cy: 1090, radius: 56, ring_px: 8, ring_color: "#FFFFFF", accent: "#FFD84D", caption_size: 76 };
const ASPECT_RATIO: Record<NonNullable<Frame["aspect"]>, [number, number]> = {
  square: [1, 1],
  landscape: [4, 3],
  portrait: [3, 4],
  wide: [16, 9],
};

function frameBox(frame: Frame): { w: number; h: number } {
  const size = frame.size ?? PREVIEW.size;
  if (frame.shape === "rounded" && frame.aspect) {
    const [a, b] = ASPECT_RATIO[frame.aspect];
    return a >= b ? { w: size, h: Math.round((size * b) / a) } : { w: Math.round((size * a) / b), h: size };
  }
  if (frame.shape === "auto") return { w: 1000, h: 750 };
  return { w: size, h: size };
}

/** Схема кадра 9:16: где встанет фигура, какого размера и цвета обводка. */
function FramePreview({ frame, captions }: { frame: Frame; captions: boolean }) {
  const plain = frame.shape === "none";
  const { w, h } = frameBox(frame);
  const cy = frame.cy ?? PREVIEW.cy;
  const x = 540 - w / 2;
  const y = cy - h / 2;
  const ring = frame.ring_px ?? PREVIEW.ring_px;
  const color = frame.ring_color ?? PREVIEW.ring_color;
  const round = frame.shape === "circle" ? Math.min(w, h) / 2 : frame.shape === "auto" ? 56 : (frame.radius ?? PREVIEW.radius);
  const capY = plain ? 1500 : y + h - 60;
  return (
    <svg
      viewBox="0 0 1080 1920"
      className="h-40 w-auto shrink-0 rounded-sm border border-border"
      role="img"
      aria-label={plain ? "Схема: обычный полный кадр" : "Схема кадра: фигура на живом фоне"}
    >
      <rect width="1080" height="1920" fill={plain ? "#3a3f4b" : frame.bg_set === "night" ? "#141a33" : "#1f3a2a"} />
      {plain ? null : (
        <>
          <rect x="60" y="120" width="960" height="150" rx="24" fill="#ffffff" opacity="0.12" />
          <rect x={x} y={y} width={w} height={h} rx={round} fill="#6b7280" stroke={color} strokeWidth={ring * 2} />
        </>
      )}
      {captions ? (
        <rect x="200" y={capY} width="680" height="44" rx="10" fill={frame.accent ?? PREVIEW.accent} opacity="0.85" />
      ) : null}
    </svg>
  );
}

function Slider({
  label,
  value,
  fallback,
  min,
  max,
  step,
  unit = "",
  onChange,
  disabled,
}: {
  label: string;
  value: number | null | undefined;
  fallback: number;
  min: number;
  max: number;
  step: number;
  unit?: string;
  onChange: (value: number) => void;
  disabled?: boolean;
}) {
  const id = useId();
  const shown = value ?? fallback;
  return (
    <div className="flex flex-col gap-1 text-2xs text-fg-muted">
      <div className="flex items-center justify-between gap-2">
        <label htmlFor={id}>{label}</label>
        <span className="tabular-nums text-fg">{value === null || value === undefined ? "как у канала" : `${shown}${unit}`}</span>
      </div>
      <input
        id={id}
        type="range"
        min={min}
        max={max}
        step={step}
        value={shown}
        disabled={disabled}
        onChange={(event) => onChange(Number(event.target.value))}
        className="h-1 w-full cursor-pointer accent-[var(--accent)] disabled:cursor-not-allowed"
      />
    </div>
  );
}

function ColorField({
  label,
  value,
  fallback,
  onChange,
  disabled,
}: {
  label: string;
  value: string | null | undefined;
  fallback: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <label htmlFor={id} className="flex items-center justify-between gap-2 text-2xs text-fg-muted">
      {label}
      <span className="flex items-center gap-1.5">
        <span className="text-fg">{value ? value.toUpperCase() : "как у канала"}</span>
        <input
          id={id}
          type="color"
          value={value ?? fallback}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value.toUpperCase())}
          className="h-6 w-8 cursor-pointer rounded-sm border border-border bg-transparent p-0 disabled:cursor-not-allowed"
        />
      </span>
    </label>
  );
}

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <fieldset className="flex flex-col gap-2 border-t border-border pt-2">
      <legend className="pr-1 text-2xs font-medium text-fg">{title}</legend>
      {children}
    </fieldset>
  );
}

export function FramePicker({ disabled }: { disabled: boolean }) {
  const frame = useClipEditor((state) => state.frame);
  const captions = useClipEditor((state) => state.captions);
  const setFrame = useClipEditor((state) => state.setFrame);
  const resetFrame = useClipEditor((state) => state.resetFrame);
  const setCaptions = useClipEditor((state) => state.setCaptions);
  const shape = frame.shape;
  const tuned = Object.keys(frame).some((key) => key !== "shape");
  const framed = shape !== "none";

  return (
    <section className="flex flex-col gap-2 border-b border-border px-3 py-3" data-testid="clip-frame">
      <div className="flex items-center justify-between gap-2">
        <h2 className="font-mono text-2xs tracking-wider text-fg-muted uppercase">Оформление</h2>
        {tuned ? (
          <button
            type="button"
            onClick={resetFrame}
            disabled={disabled}
            className="inline-flex items-center gap-1 rounded-sm px-1 text-2xs text-fg-muted hover:text-fg"
            title="Сбросить тонкую настройку к оформлению канала"
          >
            <RotateCcw className="h-3 w-3" aria-hidden="true" />
            сбросить
          </button>
        ) : null}
      </div>

      <div className="flex gap-3">
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <div role="radiogroup" aria-label="Форма кадра" className="flex flex-wrap gap-1">
            {SHAPES.map((option) => (
              <button
                key={option.value}
                type="button"
                role="radio"
                aria-checked={shape === option.value}
                title={option.hint}
                disabled={disabled}
                onClick={() => setFrame({ shape: option.value })}
                className={cn(
                  "rounded-md border px-2 py-1 text-xs transition-colors duration-150",
                  shape === option.value
                    ? "border-accent bg-accent/15 text-accent"
                    : "border-border text-fg-muted hover:bg-surface-2 hover:text-fg",
                )}
              >
                {option.label}
              </button>
            ))}
          </div>
          <label className="inline-flex items-center gap-2 text-xs text-fg">
            <input
              type="checkbox"
              className="h-4 w-4 accent-[var(--accent)]"
              checked={captions}
              disabled={disabled}
              onChange={(event) => setCaptions(event.target.checked)}
            />
            Субтитры
          </label>
          <p className="text-2xs text-fg-muted">{SHAPES.find((option) => option.value === shape)?.hint}</p>
        </div>
        <FramePreview frame={frame} captions={captions} />
      </div>

      {framed ? (
        <details className="group flex flex-col gap-2">
          <summary className="cursor-pointer select-none text-xs text-fg-muted hover:text-fg">Тонкая настройка</summary>
          <div className="mt-2 flex flex-col gap-3">
            <Group title="Фигура">
              <Slider
                label={shape === "circle" ? "Диаметр круга" : "Размер рамки"}
                value={frame.size}
                fallback={PREVIEW.size}
                min={480}
                max={1040}
                step={20}
                unit=" px"
                disabled={disabled}
                onChange={(size) => setFrame({ size })}
              />
              <Slider
                label="Положение по высоте"
                value={frame.cy}
                fallback={PREVIEW.cy}
                min={700}
                max={1400}
                step={10}
                unit=" px"
                disabled={disabled}
                onChange={(cy) => setFrame({ cy })}
              />
              {shape === "rounded" ? (
                <>
                  <label className="flex flex-col gap-1 text-2xs text-fg-muted">
                    Пропорции рамки
                    <Select
                      value={frame.aspect ?? ""}
                      disabled={disabled}
                      aria-label="Пропорции рамки"
                      onChange={(event) =>
                        setFrame({ aspect: (event.target.value || null) as Frame["aspect"] })
                      }
                    >
                      <option value="">Как у канала</option>
                      {ASPECTS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </Select>
                  </label>
                  <Slider
                    label="Скругление углов"
                    value={frame.radius}
                    fallback={PREVIEW.radius}
                    min={0}
                    max={240}
                    step={4}
                    unit=" px"
                    disabled={disabled}
                    onChange={(radius) => setFrame({ radius })}
                  />
                </>
              ) : null}
            </Group>
            <Group title="Обводка">
              <Slider
                label="Толщина"
                value={frame.ring_px}
                fallback={PREVIEW.ring_px}
                min={0}
                max={24}
                step={1}
                unit=" px"
                disabled={disabled}
                onChange={(ring_px) => setFrame({ ring_px })}
              />
              <ColorField
                label="Цвет обводки"
                value={frame.ring_color}
                fallback={PREVIEW.ring_color}
                disabled={disabled}
                onChange={(ring_color) => setFrame({ ring_color })}
              />
            </Group>
            <Group title="Фон">
              <label className="flex flex-col gap-1 text-2xs text-fg-muted">
                Живой фон
                <Select
                  value={frame.bg_set ?? ""}
                  disabled={disabled}
                  aria-label="Живой фон"
                  onChange={(event) => setFrame({ bg_set: (event.target.value || null) as Frame["bg_set"] })}
                >
                  <option value="">Как у канала</option>
                  {BACKGROUNDS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </Select>
              </label>
              <Slider
                label="Притемнение фона"
                value={frame.bg_dim === null || frame.bg_dim === undefined ? null : Math.round(frame.bg_dim * 100)}
                fallback={5}
                min={0}
                max={50}
                step={5}
                unit="%"
                disabled={disabled}
                onChange={(dim) => setFrame({ bg_dim: dim / 100 })}
              />
            </Group>
            {captions ? (
              <Group title="Субтитры">
                <Slider
                  label="Размер"
                  value={frame.caption_size}
                  fallback={PREVIEW.caption_size}
                  min={48}
                  max={110}
                  step={2}
                  disabled={disabled}
                  onChange={(caption_size) => setFrame({ caption_size })}
                />
                <ColorField
                  label="Цвет выделенного слова"
                  value={frame.accent}
                  fallback={PREVIEW.accent}
                  disabled={disabled}
                  onChange={(accent) => setFrame({ accent })}
                />
              </Group>
            ) : null}
          </div>
        </details>
      ) : null}
    </section>
  );
}
