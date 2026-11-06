"use client";

import {
  type KeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";

import type { Schemas } from "@/lib/api/client";
import type { BannerCreative } from "@/lib/queries/creatives";
import {
  type Box,
  CANVAS_H,
  CANVAS_W,
  type CreativeSize,
  isVisibleAt,
  type LocalPlacement,
  placeVariant,
  type PlatformView,
  type SafeZoneProfile,
  slotBox,
  type TimeSpan,
} from "@/lib/safe-zones";

import {
  CanvasBackdrop,
  type CanvasBackground,
  CanvasDefs,
  CaptionsZoneOverlay,
  PlatformUiSilhouette,
  SafeZoneOverlay,
} from "./canvas-overlays";
import { type BannerLayer, type BannerSlot, type EditorDraft, normalizeSlot, type TextContext } from "./draft";
import { layerKindLabel } from "./labels";
import { CreativeVisual, TextVisual } from "./layer-visuals";

export interface CanvasOverlays {
  safe: boolean;
  captions: boolean;
  ui: boolean;
  ghosts: boolean;
}

type Handle = "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";
const HANDLES: Handle[] = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];
const HANDLE_CURSOR: Record<Handle, string> = {
  n: "ns-resize",
  s: "ns-resize",
  e: "ew-resize",
  w: "ew-resize",
  ne: "nesw-resize",
  sw: "nesw-resize",
  nw: "nwse-resize",
  se: "nwse-resize",
};

interface DragState {
  layerId: string;
  mode: "move" | "resize";
  handle?: Handle;
  pointerId: number;
  start: { x: number; y: number };
  startSlot: BannerSlot;
  slot: BannerSlot;
  guides: { x: number[]; y: number[] };
  moved: boolean;
}

const SNAP_PX = 12;

function snapValue(value: number, targets: number[]): { value: number; target: number | null } {
  let best: number | null = null;
  let bestDist = SNAP_PX + 1;
  for (const target of targets) {
    const dist = Math.abs(target - value);
    if (dist < bestDist) {
      best = target;
      bestDist = dist;
    }
  }
  return best === null ? { value, target: null } : { value: best, target: best };
}

/** Можно ли таскать слой по холсту: прикреплённый к креативу текст едет вместе с креативом. */
export function isMovable(layer: BannerLayer | undefined): boolean {
  if (!layer) return false;
  return !(layer.attach !== "none" && (layer.kind === "text_code" || layer.kind === "text_erid"));
}

export interface CanvasStageProps {
  draft: EditorDraft;
  creatives: Record<string, BannerCreative>;
  textContext: TextContext;
  profile: SafeZoneProfile | undefined;
  secondaryProfile: SafeZoneProfile | undefined;
  platformView: PlatformView;
  captionsZone: Schemas["CaptionsZoneInfo"] | null;
  windows: TimeSpan[];
  t: number;
  overlays: CanvasOverlays;
  background: CanvasBackground;
  selectedId: string | null;
  readOnly: boolean;
  fontsLoaded: number;
  /** PNG «Кадра из рендера» поверх холста — сверить WYSIWYG. */
  frameOverlay?: string | null;
  frameOverlayOpacity?: number;
  onSelect: (id: string | null) => void;
  /** Итог перетаскивания или шага клавиатурой: новый слот слоя. */
  onSlotCommit: (layerId: string, slot: BannerSlot, source: "drag" | "keyboard") => void;
  onBlockedMove?: (layer: BannerLayer) => void;
}

export function CanvasStage({
  draft,
  creatives,
  textContext,
  profile,
  secondaryProfile,
  platformView,
  captionsZone,
  windows,
  t,
  overlays,
  background,
  selectedId,
  readOnly,
  fontsLoaded,
  frameOverlay,
  frameOverlayOpacity = 0.5,
  onSelect,
  onSlotCommit,
  onBlockedMove,
}: CanvasStageProps) {
  const idPrefix = `bc${useId().replace(/[^A-Za-z0-9]/g, "")}`;
  const svgRef = useRef<SVGSVGElement>(null);
  const [drag, setDrag] = useState<DragState | null>(null);
  const [zoom, setZoom] = useState(0.4);

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      const height = svg.getBoundingClientRect().height;
      if (height > 0) setZoom(height / CANVAS_H);
    });
    observer.observe(svg);
    return () => observer.disconnect();
  }, []);

  const sizes = useMemo(() => {
    const out: Record<string, CreativeSize> = {};
    for (const [sha, creative] of Object.entries(creatives)) {
      if (creative.status === "ready" && creative.width && creative.height) {
        out[sha] = { width: creative.width, height: creative.height };
      }
    }
    return out;
  }, [creatives]);

  const renderLayers = useMemo(() => {
    if (!drag) return draft.layers;
    return draft.layers.map((layer) => (layer.id === drag.layerId ? { ...layer, slot: drag.slot } : layer));
  }, [draft.layers, drag]);

  const layout = useMemo(() => placeVariant(renderLayers, sizes), [renderLayers, sizes]);
  const layersById = useMemo(() => new Map(renderLayers.map((layer) => [layer.id, layer])), [renderLayers]);

  const snapTargets = useMemo(() => {
    const xs = [0, CANVAS_W / 2, CANVAS_W];
    const ys = [0, CANVAS_H];
    if (profile) {
      xs.push(profile.safe.x1, profile.safe.x2);
      ys.push(profile.safe.y1, profile.safe.y2);
      if (profile.offer_area) {
        xs.push(profile.offer_area.x1, profile.offer_area.x2);
        ys.push(profile.offer_area.y1, profile.offer_area.y2);
      }
    }
    return { xs, ys };
  }, [profile]);

  const toCanvas = useCallback((clientX: number, clientY: number) => {
    const svg = svgRef.current;
    const ctm = svg?.getScreenCTM();
    if (!svg || !ctm) return { x: 0, y: 0 };
    const point = new DOMPoint(clientX, clientY).matrixTransform(ctm.inverse());
    return { x: point.x, y: point.y };
  }, []);

  const beginDrag = (event: ReactPointerEvent, layerId: string, mode: "move" | "resize", handle?: Handle) => {
    if (event.button !== 0) return;
    event.stopPropagation();
    onSelect(layerId);
    svgRef.current?.focus({ preventScroll: true });
    if (readOnly) return;
    const layer = draft.layers.find((item) => item.id === layerId);
    if (!layer) return;
    if (mode === "move" && !isMovable(layer)) {
      onBlockedMove?.(layer);
      return;
    }
    event.preventDefault();
    svgRef.current?.setPointerCapture(event.pointerId);
    setDrag({
      layerId,
      mode,
      handle,
      pointerId: event.pointerId,
      start: toCanvas(event.clientX, event.clientY),
      startSlot: layer.slot,
      slot: layer.slot,
      guides: { x: [], y: [] },
      moved: false,
    });
  };

  const onPointerMove = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (!drag || event.pointerId !== drag.pointerId) return;
    const point = toCanvas(event.clientX, event.clientY);
    const dx = Math.round(point.x - drag.start.x);
    const dy = Math.round(point.y - drag.start.y);
    const snap = !event.altKey;
    const s = drag.startSlot;
    const guides = { x: [] as number[], y: [] as number[] };
    let next: BannerSlot;
    if (drag.mode === "move") {
      let x = s.x + dx;
      let y = s.y + dy;
      if (snap) {
        const cands: Array<{ edge: number; offset: number }> = [
          { edge: x, offset: 0 },
          { edge: x + s.w, offset: s.w },
          { edge: x + s.w / 2, offset: s.w / 2 },
        ];
        for (const cand of cands) {
          const hit = snapValue(cand.edge, snapTargets.xs);
          if (hit.target !== null) {
            x = Math.round(hit.value - cand.offset);
            guides.x.push(hit.target);
            break;
          }
        }
        for (const cand of [
          { edge: y, offset: 0 },
          { edge: y + s.h, offset: s.h },
        ]) {
          const hit = snapValue(cand.edge, snapTargets.ys);
          if (hit.target !== null) {
            y = Math.round(hit.value - cand.offset);
            guides.y.push(hit.target);
            break;
          }
        }
      }
      next = { ...s, x, y };
    } else {
      const handle = drag.handle ?? "se";
      let x1 = s.x;
      let y1 = s.y;
      let x2 = s.x + s.w;
      let y2 = s.y + s.h;
      if (handle.includes("w")) x1 += dx;
      if (handle.includes("e")) x2 += dx;
      if (handle.includes("n")) y1 += dy;
      if (handle.includes("s")) y2 += dy;
      if (event.shiftKey && s.w > 0 && s.h > 0 && handle.length === 2) {
        // Shift — сохранить пропорции слота (угловые ручки).
        const ratio = s.w / s.h;
        const w = Math.max(8, x2 - x1);
        const h = Math.round(w / ratio);
        if (handle.includes("n")) y1 = y2 - h;
        else y2 = y1 + h;
      }
      if (snap) {
        const snapEdge = (value: number, targets: number[], axis: "x" | "y") => {
          const hit = snapValue(value, targets);
          if (hit.target !== null) guides[axis].push(hit.target);
          return hit.value;
        };
        if (handle.includes("w")) x1 = snapEdge(x1, snapTargets.xs, "x");
        if (handle.includes("e")) x2 = snapEdge(x2, snapTargets.xs, "x");
        if (handle.includes("n")) y1 = snapEdge(y1, snapTargets.ys, "y");
        if (handle.includes("s")) y2 = snapEdge(y2, snapTargets.ys, "y");
      }
      const w = Math.max(8, Math.round(x2 - x1));
      const h = Math.max(8, Math.round(y2 - y1));
      next = {
        ...s,
        x: handle.includes("w") ? Math.round(x2 - w) : Math.round(x1),
        y: handle.includes("n") ? Math.round(y2 - h) : Math.round(y1),
        w,
        h,
      };
    }
    setDrag({ ...drag, slot: normalizeSlot(next), guides, moved: drag.moved || dx !== 0 || dy !== 0 });
  };

  const endDrag = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (!drag || event.pointerId !== drag.pointerId) return;
    svgRef.current?.releasePointerCapture(event.pointerId);
    if (drag.moved) onSlotCommit(drag.layerId, drag.slot, "drag");
    setDrag(null);
  };

  const onKeyDown = (event: KeyboardEvent<SVGSVGElement>) => {
    const ordered = [...layout.placements].sort((a, b) => b.z - a.z);
    if (event.key === "Escape") {
      onSelect(null);
      return;
    }
    if (event.key === "[" || event.key === "]") {
      event.preventDefault();
      const ids = draft.layers.map((layer) => layer.id);
      const index = selectedId ? ids.indexOf(selectedId) : -1;
      const nextIndex = (index + (event.key === "]" ? 1 : -1) + ids.length) % ids.length;
      onSelect(ids[nextIndex] ?? null);
      return;
    }
    const layer = selectedId ? draft.layers.find((item) => item.id === selectedId) : undefined;
    if (!layer || readOnly) {
      if (!layer && event.key.startsWith("Arrow") && ordered[0]) onSelect(ordered[0].layerId);
      return;
    }
    const step = event.shiftKey ? 10 : 1;
    const s = layer.slot;
    let next: BannerSlot | null = null;
    if (event.key.startsWith("Arrow")) {
      const dx = event.key === "ArrowLeft" ? -step : event.key === "ArrowRight" ? step : 0;
      const dy = event.key === "ArrowUp" ? -step : event.key === "ArrowDown" ? step : 0;
      if (event.altKey) next = { ...s, w: s.w + dx, h: s.h + dy };
      else {
        if (!isMovable(layer)) {
          onBlockedMove?.(layer);
          event.preventDefault();
          return;
        }
        next = { ...s, x: s.x + dx, y: s.y + dy };
      }
    } else if (event.key === "+" || event.key === "=") {
      next = { ...s, scale: s.scale + (event.shiftKey ? 0.1 : 0.05) };
    } else if (event.key === "-" || event.key === "_") {
      next = { ...s, scale: s.scale - (event.shiftKey ? 0.1 : 0.05) };
    }
    if (next) {
      event.preventDefault();
      onSlotCommit(layer.id, normalizeSlot(next), "keyboard");
    }
  };

  const handleSize = Math.max(10, 11 / zoom);
  const selectedPlacement = layout.placements.find((item) => item.layerId === selectedId);
  const selectedLayer = selectedId ? layersById.get(selectedId) : undefined;

  const visual = (placement: LocalPlacement) => {
    const layer = layersById.get(placement.layerId);
    if (!layer) return null;
    const visible = isVisibleAt(placement.show, windows, t);
    if (!visible && !overlays.ghosts) return null;
    const opacity = visible ? 1 : 0.22;
    if (placement.creativeSha256) {
      return (
        <CreativeVisual
          key={placement.layerId}
          placement={placement}
          layer={layer}
          creative={creatives[placement.creativeSha256]}
          idPrefix={idPrefix}
          opacity={opacity}
        />
      );
    }
    if (layer.kind === "creative") {
      // креатив без готового файла
      return (
        <CreativeVisual
          key={placement.layerId}
          placement={placement}
          layer={layer}
          creative={layer.creative_sha256 ? creatives[layer.creative_sha256] : undefined}
          idPrefix={idPrefix}
          opacity={opacity}
        />
      );
    }
    return (
      <TextVisual
        key={placement.layerId}
        placement={placement}
        layer={layer}
        ctx={textContext}
        opacity={opacity}
        fontsLoaded={fontsLoaded}
      />
    );
  };

  // Слои, которых нет в раскладке (выключенный или неготовый креатив): рамка слота, чтобы их было видно.
  const unplaced = draft.layers.filter(
    (layer) => layer.enabled && layer.kind === "creative" && !layout.placements.some((p) => p.layerId === layer.id),
  );

  return (
    <svg
      ref={svgRef}
      viewBox={`0 0 ${CANVAS_W} ${CANVAS_H}`}
      className="block h-full w-auto max-w-full touch-none bg-video outline-none select-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      style={{ aspectRatio: `${CANVAS_W} / ${CANVAS_H}` }}
      role="application"
      aria-roledescription="холст баннера"
      aria-label={`Кадр ${CANVAS_W}×${CANVAS_H}. Стрелки — сдвиг на 1 px (Shift — 10), Alt+стрелки — размер слота, плюс и минус — масштаб, квадратные скобки — соседний слой, Esc — снять выбор`}
      tabIndex={0}
      data-testid="banner-canvas"
      data-zoom={zoom.toFixed(3)}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onPointerDown={(event) => {
        if (event.target === svgRef.current) onSelect(null);
      }}
      onKeyDown={onKeyDown}
    >
      <CanvasDefs idPrefix={idPrefix} />
      <CanvasBackdrop kind={background} idPrefix={idPrefix} />

      {overlays.ui && profile ? <PlatformUiSilhouette view={platformView} profile={profile} /> : null}
      {overlays.safe && profile ? (
        <SafeZoneOverlay profile={profile} secondary={secondaryProfile} idPrefix={idPrefix} />
      ) : null}
      {overlays.captions && captionsZone ? <CaptionsZoneOverlay zone={captionsZone} /> : null}

      <g data-testid="banner-layers">{layout.placements.map(visual)}</g>

      {frameOverlay ? (
        <image
          href={frameOverlay}
          x={0}
          y={0}
          width={CANVAS_W}
          height={CANVAS_H}
          opacity={frameOverlayOpacity}
          pointerEvents="none"
          preserveAspectRatio="none"
        />
      ) : null}

      {/* Зоны захвата: сверху всех визуалов, в порядке z. */}
      <g>
        {[...layout.placements]
          .sort((a, b) => a.z - b.z)
          .map((placement) => {
            const layer = layersById.get(placement.layerId);
            if (!layer) return null;
            const box = slotBox(placement.slot);
            return (
              <rect
                key={placement.layerId}
                x={box.x}
                y={box.y}
                width={Math.max(1, box.w)}
                height={Math.max(1, box.h)}
                fill="transparent"
                stroke={placement.layerId === selectedId ? "none" : "var(--fg)"}
                strokeOpacity="0.18"
                strokeWidth="1"
                vectorEffect="non-scaling-stroke"
                style={{ cursor: readOnly ? "pointer" : isMovable(layer) ? "move" : "not-allowed" }}
                data-layer-hit={placement.layerId}
                aria-label={`${layerKindLabel(layer.kind)}: ${layer.id}`}
                onPointerDown={(event) => beginDrag(event, placement.layerId, "move")}
              />
            );
          })}
        {unplaced.map((layer) => (
          <rect
            key={`unplaced-${layer.id}`}
            x={layer.slot.x}
            y={layer.slot.y}
            width={layer.slot.w}
            height={layer.slot.h}
            fill="var(--machine)"
            fillOpacity="0.06"
            stroke="var(--machine)"
            strokeDasharray="6 6"
            strokeWidth="1.5"
            vectorEffect="non-scaling-stroke"
            data-layer-hit={layer.id}
            style={{ cursor: readOnly ? "pointer" : "move" }}
            onPointerDown={(event) => beginDrag(event, layer.id, "move")}
          />
        ))}
      </g>

      {selectedLayer ? (
        <SelectionChrome
          layer={selectedLayer}
          placement={selectedPlacement}
          handleSize={handleSize}
          readOnly={readOnly}
          onHandleDown={(event, handle) => beginDrag(event, selectedLayer.id, "resize", handle)}
        />
      ) : null}

      {drag
        ? [
            ...drag.guides.x.map((x, i) => (
              <line
                key={`gx${i}`}
                x1={x}
                y1={0}
                x2={x}
                y2={CANVAS_H}
                stroke="var(--human)"
                strokeWidth="1.5"
                vectorEffect="non-scaling-stroke"
                pointerEvents="none"
              />
            )),
            ...drag.guides.y.map((y, i) => (
              <line
                key={`gy${i}`}
                x1={0}
                y1={y}
                x2={CANVAS_W}
                y2={y}
                stroke="var(--human)"
                strokeWidth="1.5"
                vectorEffect="non-scaling-stroke"
                pointerEvents="none"
              />
            )),
          ]
        : null}
    </svg>
  );
}

function SelectionChrome({
  layer,
  placement,
  handleSize,
  readOnly,
  onHandleDown,
}: {
  layer: BannerLayer;
  placement: LocalPlacement | undefined;
  handleSize: number;
  readOnly: boolean;
  onHandleDown: (event: ReactPointerEvent, handle: Handle) => void;
}) {
  const slot = placement?.slot ?? layer.slot;
  const box = slotBox(slot);
  const content: Box | null = placement?.rect ?? null;
  const movable = isMovable(layer);
  const handlePos = (handle: Handle) => {
    const x = handle.includes("w") ? box.x : handle.includes("e") ? box.x + box.w : box.x + box.w / 2;
    const y = handle.includes("n") ? box.y : handle.includes("s") ? box.y + box.h : box.y + box.h / 2;
    return { x, y };
  };
  const label = `${layerKindLabel(layer.kind)} · ${box.w}×${box.h}${placement?.scale ? ` · ×${placement.scale.toFixed(2).replace(".", ",")}` : ""}`;
  const labelY = box.y > 60 ? box.y - 14 : box.y + box.h + 40;
  return (
    <g data-testid="selection" data-layer-id={layer.id} data-slot={`${box.x},${box.y},${box.w},${box.h}`}>
      <rect
        x={box.x}
        y={box.y}
        width={box.w}
        height={box.h}
        fill="none"
        stroke="var(--accent)"
        strokeDasharray="8 5"
        strokeWidth="1.5"
        vectorEffect="non-scaling-stroke"
        pointerEvents="none"
      />
      {content && placement?.creativeSha256 ? (
        <rect
          x={content.x}
          y={content.y}
          width={content.w}
          height={content.h}
          fill="none"
          stroke="var(--accent)"
          strokeWidth="2"
          vectorEffect="non-scaling-stroke"
          pointerEvents="none"
        />
      ) : null}
      <text
        x={box.x}
        y={labelY}
        fontSize={26}
        fill="var(--accent)"
        fontFamily="var(--font-mono)"
        pointerEvents="none"
        paintOrder="stroke"
        stroke="#000"
        strokeWidth={5}
        strokeOpacity={0.6}
      >
        {label}
      </text>
      {readOnly || !movable
        ? null
        : HANDLES.map((handle) => {
            const pos = handlePos(handle);
            return (
              <rect
                key={handle}
                x={pos.x - handleSize / 2}
                y={pos.y - handleSize / 2}
                width={handleSize}
                height={handleSize}
                fill="var(--bg)"
                stroke="var(--accent)"
                strokeWidth="1.5"
                vectorEffect="non-scaling-stroke"
                style={{ cursor: HANDLE_CURSOR[handle] }}
                data-handle={handle}
                onPointerDown={(event) => onHandleDown(event, handle)}
              />
            );
          })}
    </g>
  );
}
