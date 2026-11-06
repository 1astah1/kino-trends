"use client";

import { type KeyboardEvent, type PointerEvent as ReactPointerEvent, useEffect, useId, useRef, useState } from "react";

import { mediaSrc } from "@/lib/api/media";
import { cn } from "@/lib/cn";
import type { JobLayout, LayoutRect } from "@/lib/queries/layout";

import {
  CANVAS_H,
  CANVAS_W,
  conflictIds,
  conflictLabel,
  type Draft,
  type DraftLayout,
  nudge,
  SCALE_STEP,
  type Selection,
  setScale,
  type ZoneId,
  ZONE_IDS,
} from "./layout-logic";

const LABEL_PX = 30;
const SUB_LABEL_PX = 26;

type Tone = { stroke: string; fill: string; text: string };
const TONES: Record<"banner" | "captions" | "text" | "conflict" | "static", Tone> = {
  banner: { stroke: "stroke-human", fill: "fill-human/15", text: "fill-human" },
  captions: { stroke: "stroke-machine", fill: "fill-machine/15", text: "fill-machine" },
  text: { stroke: "stroke-accent", fill: "fill-accent/15", text: "fill-accent" },
  conflict: { stroke: "stroke-negative", fill: "fill-negative/25", text: "fill-negative" },
  static: { stroke: "stroke-positive", fill: "fill-positive/10", text: "fill-positive" },
};

function toneOf(rect: LayoutRect, conflict: boolean): Tone {
  if (conflict) return TONES.conflict;
  if (rect.kind === "banner") return rect.movable ? TONES.banner : TONES.static;
  if (rect.kind === "captions") return TONES.captions;
  return TONES.text;
}

function selectionOf(rect: LayoutRect): Selection | null {
  if (rect.kind === "banner") return rect.movable ? "banner" : null;
  return rect.kind as ZoneId;
}

interface DragState {
  pointerId: number;
  selection: Selection;
  start: { x: number; y: number };
  startDraft: Draft;
  moved: boolean;
}

export interface LayoutStageProps {
  layout: JobLayout;
  draft: Draft;
  draftLayout: DraftLayout;
  selection: Selection | null;
  poster: string | null | undefined;
  readOnly: boolean;
  showDeadZones: boolean;
  onSelect: (selection: Selection | null) => void;
  onDraftChange: (draft: Draft) => void;
}

/**
 * Кадр 1080×1920 с постером ролика и перетаскиваемыми прямоугольниками: блок баннера (целиком, масштаб
 * колесом), полоса субтитров (только по вертикали), заголовок и тезисы. Пересечения — красным с подписью
 * «налезает на …». Стрелки — 1 px (Shift — 10), +/− — масштаб баннера, Esc — снять выбор.
 */
export function LayoutStage({
  layout,
  draft,
  draftLayout,
  selection,
  poster,
  readOnly,
  showDeadZones,
  onSelect,
  onDraftChange,
}: LayoutStageProps) {
  const idPrefix = `lay${useId().replace(/[^A-Za-z0-9]/g, "")}`;
  const svgRef = useRef<SVGSVGElement>(null);
  const [drag, setDrag] = useState<DragState | null>(null);
  const safePoster = mediaSrc(poster);
  const safe = layout.safe;
  const conflicted = conflictIds(draftLayout.conflicts);

  // Актуальные значения для колеса: слушатель вешается один раз и должен быть non-passive.
  const latest = useRef({ draft, selection, readOnly, onDraftChange, onSelect });
  useEffect(() => {
    latest.current = { draft, selection, readOnly, onDraftChange, onSelect };
  });
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const onWheel = (event: WheelEvent) => {
      const state = latest.current;
      if (state.readOnly) return;
      const target = event.target as Element | null;
      const overBanner = Boolean(target?.closest?.('[data-selection="banner"]'));
      if (!overBanner && state.selection !== "banner") return;
      event.preventDefault();
      const delta = event.deltaY < 0 ? SCALE_STEP : -SCALE_STEP;
      state.onDraftChange(setScale(state.draft, state.draft.scale + delta));
      if (state.selection !== "banner") state.onSelect("banner");
    };
    svg.addEventListener("wheel", onWheel, { passive: false });
    return () => svg.removeEventListener("wheel", onWheel);
  }, []);

  const toCanvas = (clientX: number, clientY: number) => {
    const svg = svgRef.current;
    const ctm = svg?.getScreenCTM();
    if (!svg || !ctm) return { x: 0, y: 0 };
    const point = new DOMPoint(clientX, clientY).matrixTransform(ctm.inverse());
    return { x: point.x, y: point.y };
  };

  const beginDrag = (event: ReactPointerEvent, rect: LayoutRect) => {
    if (event.button !== 0) return;
    const target = selectionOf(rect);
    event.stopPropagation();
    svgRef.current?.focus({ preventScroll: true });
    if (!target) return;
    onSelect(target);
    if (readOnly) return;
    event.preventDefault();
    svgRef.current?.setPointerCapture(event.pointerId);
    setDrag({
      pointerId: event.pointerId,
      selection: target,
      start: toCanvas(event.clientX, event.clientY),
      startDraft: draft,
      moved: false,
    });
  };

  const onPointerMove = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (!drag || event.pointerId !== drag.pointerId) return;
    const point = toCanvas(event.clientX, event.clientY);
    const dx = Math.round(point.x - drag.start.x);
    const dy = Math.round(point.y - drag.start.y);
    if (dx === 0 && dy === 0 && !drag.moved) return;
    onDraftChange(nudge(drag.startDraft, drag.selection, dx, dy));
    if (!drag.moved) setDrag({ ...drag, moved: true });
  };

  const endDrag = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (!drag || event.pointerId !== drag.pointerId) return;
    svgRef.current?.releasePointerCapture(event.pointerId);
    setDrag(null);
  };

  const onKeyDown = (event: KeyboardEvent<SVGSVGElement>) => {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key === "Escape") {
      if (selection) {
        event.preventDefault();
        onSelect(null);
      }
      return;
    }
    if (event.key === "Tab") return;
    if (!selection) {
      if (event.key.startsWith("Arrow")) {
        event.preventDefault();
        onSelect(draftLayout.block ? "banner" : "captions");
      }
      return;
    }
    const step = event.shiftKey ? 10 : 1;
    if (event.key.startsWith("Arrow")) {
      event.preventDefault();
      if (readOnly) return;
      const dx = event.key === "ArrowLeft" ? -step : event.key === "ArrowRight" ? step : 0;
      const dy = event.key === "ArrowUp" ? -step : event.key === "ArrowDown" ? step : 0;
      onDraftChange(nudge(draft, selection, dx, dy));
      return;
    }
    if ((event.key === "+" || event.key === "=" || event.key === "-" || event.key === "_") && selection === "banner") {
      event.preventDefault();
      if (readOnly) return;
      const delta = event.key === "-" || event.key === "_" ? -SCALE_STEP : SCALE_STEP;
      onDraftChange(setScale(draft, draft.scale + (event.shiftKey ? delta * 2 : delta)));
    }
  };

  const deadZones = [
    { id: "top", x: 0, y: 0, w: CANVAS_W, h: safe.y1 },
    { id: "bottom", x: 0, y: safe.y2, w: CANVAS_W, h: CANVAS_H - safe.y2 },
    { id: "left", x: 0, y: safe.y1, w: safe.x1, h: safe.y2 - safe.y1 },
    { id: "right", x: safe.x2, y: safe.y1, w: CANVAS_W - safe.x2, h: safe.y2 - safe.y1 },
  ].filter((z) => z.w > 0 && z.h > 0);

  // Порядок: неподвижные слои, баннер, субтитры, текст; выбранный — поверх.
  const ordered = [...draftLayout.rects].sort((a, b) => {
    const rank = (r: LayoutRect) => (selectionOf(r) === selection ? 3 : r.kind === "banner" ? (r.movable ? 1 : 0) : 2);
    return rank(a) - rank(b);
  });

  return (
    <svg
      ref={svgRef}
      viewBox={`0 0 ${CANVAS_W} ${CANVAS_H}`}
      preserveAspectRatio="xMidYMid meet"
      className={cn(
        "block h-full w-full touch-none bg-video outline-none select-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        drag ? "cursor-grabbing" : undefined,
      )}
      role="application"
      aria-roledescription="редактор раскладки"
      aria-label={`Кадр ${CANVAS_W}×${CANVAS_H}. Перетащите баннер или текстовую зону; стрелки — сдвиг на 1 px (Shift — 10), плюс и минус — масштаб баннера, Esc — снять выбор`}
      tabIndex={0}
      data-testid="layout-stage"
      data-selection={selection ?? ""}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onPointerDown={() => onSelect(null)}
      onKeyDown={onKeyDown}
    >
      <defs>
        <pattern
          id={`${idPrefix}-hatch`}
          width="28"
          height="28"
          patternUnits="userSpaceOnUse"
          patternTransform="rotate(45)"
        >
          <line x1="0" y1="0" x2="0" y2="28" className="stroke-negative/60" strokeWidth="6" />
        </pattern>
      </defs>

      {safePoster ? (
        <image href={safePoster} width={CANVAS_W} height={CANVAS_H} preserveAspectRatio="xMidYMid slice" />
      ) : (
        <rect width={CANVAS_W} height={CANVAS_H} fill="#0b0c10" />
      )}
      <rect width={CANVAS_W} height={CANVAS_H} fill="#000" opacity={0.18} />

      {showDeadZones ? (
        <g data-layer="dead-zones" pointerEvents="none">
          {deadZones.map((z) => (
            <g key={z.id}>
              <rect x={z.x} y={z.y} width={z.w} height={z.h} className="fill-negative/20" />
              <rect x={z.x} y={z.y} width={z.w} height={z.h} fill={`url(#${idPrefix}-hatch)`} opacity={0.5} />
            </g>
          ))}
        </g>
      ) : null}
      <g pointerEvents="none">
        <rect
          x={safe.x1}
          y={safe.y1}
          width={safe.x2 - safe.x1}
          height={safe.y2 - safe.y1}
          fill="none"
          className="stroke-accent"
          strokeWidth={4}
          strokeDasharray="18 12"
        />
        <text x={safe.x1 + 10} y={safe.y1 + 36} className="fill-accent font-mono" fontSize={LABEL_PX} fontWeight={600}>
          safe
        </text>
        {layout.offer_area ? (
          <rect
            x={layout.offer_area.x1}
            y={layout.offer_area.y1}
            width={layout.offer_area.x2 - layout.offer_area.x1}
            height={layout.offer_area.y2 - layout.offer_area.y1}
            fill="none"
            className="stroke-human/40"
            strokeWidth={3}
            strokeDasharray="6 10"
          />
        ) : null}
      </g>

      <g data-layer="rects">
        {ordered.map((rect) => {
          const sel = selectionOf(rect);
          const isSelected = sel !== null && sel === selection;
          const isConflict = conflicted.has(rect.id);
          const tone = toneOf(rect, isConflict);
          const w = Math.max(0, rect.x2 - rect.x1);
          const h = Math.max(0, rect.y2 - rect.y1);
          const empty = rect.has_text === false;
          const note = draftLayout.conflicts
            .map((c) => conflictLabel(c, draftLayout.rects, rect.id))
            .find((text): text is string => text !== null);
          const interactive = sel !== null;
          return (
            <g
              key={rect.id}
              data-rect={rect.id}
              data-selection={sel ?? undefined}
              data-conflict={isConflict ? "true" : undefined}
              opacity={empty ? 0.6 : 1}
              className={
                interactive
                  ? readOnly
                    ? "cursor-default"
                    : sel === "captions"
                      ? "cursor-ns-resize"
                      : sel === "banner"
                        ? "cursor-grab"
                        : "cursor-ns-resize"
                  : undefined
              }
              onPointerDown={interactive ? (event) => beginDrag(event, rect) : undefined}
            >
              <rect
                x={rect.x1}
                y={rect.y1}
                width={w}
                height={h}
                className={cn(tone.stroke, tone.fill)}
                strokeWidth={isSelected ? 9 : interactive ? 5 : 3}
                strokeDasharray={empty ? "14 10" : rect.movable ? undefined : "14 10"}
                rx={4}
              />
              {isSelected ? (
                <rect
                  x={rect.x1 - 6}
                  y={rect.y1 - 6}
                  width={w + 12}
                  height={h + 12}
                  fill="none"
                  className="stroke-fg/70"
                  strokeWidth={2}
                  strokeDasharray="8 8"
                  pointerEvents="none"
                />
              ) : null}
              <text
                x={rect.x1 + 10}
                y={Math.min(rect.y2 - 10, rect.y1 + LABEL_PX + 6)}
                className={cn("font-mono", tone.text)}
                fontSize={LABEL_PX}
                fontWeight={600}
                pointerEvents="none"
              >
                {rect.label}
                {empty ? " · нет текста" : ""}
              </text>
              {note ? (
                <text
                  x={rect.x1 + 10}
                  y={Math.min(rect.y2 - 10, rect.y1 + LABEL_PX + SUB_LABEL_PX + 12)}
                  className="fill-negative font-mono"
                  fontSize={SUB_LABEL_PX}
                  fontWeight={600}
                  pointerEvents="none"
                >
                  {note}
                </text>
              ) : null}
            </g>
          );
        })}
      </g>

      {draftLayout.block && selection === "banner" ? (
        <text
          x={draftLayout.block.x1}
          y={Math.max(LABEL_PX + 8, draftLayout.block.y1 - 14)}
          className="fill-human font-mono"
          fontSize={SUB_LABEL_PX}
          pointerEvents="none"
        >
          {`y ${draftLayout.block.y1}–${draftLayout.block.y2} · ×${draft.scale.toFixed(2)}`}
        </text>
      ) : null}
      {ZONE_IDS.includes(selection as ZoneId)
        ? (() => {
            const zone = draftLayout[selection as ZoneId];
            return (
              <text
                x={zone.x1}
                y={Math.max(LABEL_PX + 8, zone.y1 - 14)}
                className={cn("font-mono", selection === "captions" ? "fill-machine" : "fill-accent")}
                fontSize={SUB_LABEL_PX}
                pointerEvents="none"
              >
                {`y ${zone.y1}–${zone.y2}`}
              </text>
            );
          })()
        : null}
    </svg>
  );
}
