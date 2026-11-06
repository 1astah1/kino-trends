"use client";

import { useStore } from "zustand";

import { BANNER_LAYER_KIND_LABELS } from "@/components/production/job-meta";
import { inWindow } from "@/components/review/review-logic";
import type { Schemas } from "@/lib/api/client";
import type { OverlayZones } from "@/lib/queries/review";

import type { PlayerTimeStore } from "./player-time";

type BannerBox = Schemas["BannerBox"];
type BannerWindow = Schemas["BannerWindow"];
type BannerLayerKind = Schemas["BannerLayerKind"];
type Rect = Schemas["Rect"];

export const CANVAS_W = 1080;
export const CANVAS_H = 1920;

const BOX_STYLE: Record<BannerLayerKind, { stroke: string; fill: string; text: string }> = {
  creative: { stroke: "stroke-human", fill: "fill-human/15", text: "fill-human" },
  fallback_plate: { stroke: "stroke-warning", fill: "fill-warning/15", text: "fill-warning" },
  text_code: { stroke: "stroke-accent", fill: "fill-accent/15", text: "fill-accent" },
  text_erid: { stroke: "stroke-accent", fill: "fill-accent/15", text: "fill-accent" },
  micro_brand: { stroke: "stroke-positive", fill: "fill-positive/15", text: "fill-positive" },
};

/**
 * Группа показа слоя для подсветки «сейчас в кадре». В `BannerBox` нет `show`, поэтому как у
 * `default_banner_variant`: микробренд — весь ролик, остальные слои — в окнах креатива.
 */
function groupOf(kind: BannerLayerKind): BannerWindow["group"] {
  return kind === "micro_brand" ? "persistent" : "with_creative";
}

function Label({ x, y, text, className }: { x: number; y: number; text: string; className: string }) {
  return (
    <text x={x} y={y} className={`font-mono ${className}`} fontSize={30} fontWeight={600}>
      {text}
    </text>
  );
}

function rectProps(r: Rect) {
  return { x: r.x1, y: r.y1, width: Math.max(0, r.x2 - r.x1), height: Math.max(0, r.y2 - r.y1) };
}

export interface OverlaySvgProps {
  zones: OverlayZones | null;
  showDeadZones: boolean;
  boxes?: readonly BannerBox[];
  windows?: readonly BannerWindow[];
  captionsZone?: Rect | null;
  showBanner: boolean;
  timeStore?: PlayerTimeStore;
}

/**
 * SVG-оверлей кадра `viewBox 0 0 1080 1920` (§7.3 «Приёмка»): мёртвые зоны площадки со штриховкой,
 * циановая безопасная рамка, боксы слоёв баннера (яркие, пока слой в кадре) и зона субтитров.
 */
export function OverlaySvg({
  zones,
  showDeadZones,
  boxes = [],
  windows = [],
  captionsZone,
  showBanner,
  timeStore,
}: OverlaySvgProps) {
  const active = useActiveGroups(windows, timeStore);
  const canvasW = zones?.canvas.w ?? CANVAS_W;
  const canvasH = zones?.canvas.h ?? CANVAS_H;

  return (
    <svg
      viewBox={`0 0 ${canvasW} ${canvasH}`}
      preserveAspectRatio="xMidYMid meet"
      className="h-full w-full"
      role="img"
      aria-label="Оверлей кадра: мёртвые зоны, баннер, субтитры"
      data-testid="review-overlay"
    >
      <defs>
        <pattern id="as-dead-hatch" width="28" height="28" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2="28" className="stroke-negative/60" strokeWidth="6" />
        </pattern>
      </defs>

      {showDeadZones && zones ? (
        <g data-layer="dead-zones">
          {zones.deadZones.map((zone) => (
            <g key={zone.id}>
              <rect {...rectProps(zone)} className="fill-negative/20" />
              <rect {...rectProps(zone)} fill="url(#as-dead-hatch)" opacity={0.5} />
            </g>
          ))}
          {zones.safe ? (
            <rect
              {...rectProps(zones.safe)}
              fill="none"
              className="stroke-accent"
              strokeWidth={4}
              strokeDasharray="18 12"
            />
          ) : null}
          {zones.safe ? (
            <Label x={zones.safe.x1 + 10} y={zones.safe.y1 + 36} text="safe" className="fill-accent" />
          ) : null}
        </g>
      ) : null}

      {showBanner && captionsZone ? (
        <g data-layer="captions">
          <rect
            {...rectProps(captionsZone)}
            className="fill-machine/10 stroke-machine"
            strokeWidth={4}
            strokeDasharray="10 10"
          />
          <Label x={captionsZone.x1 + 10} y={captionsZone.y2 - 12} text="субтитры" className="fill-machine" />
        </g>
      ) : null}

      {showBanner ? (
        <g data-layer="banner">
          {boxes.map((box) => {
            const style = BOX_STYLE[box.kind] ?? BOX_STYLE.creative;
            const on = active[groupOf(box.kind)];
            return (
              <g key={box.layer_id} opacity={on ? 1 : 0.45} data-active={on ? "true" : "false"}>
                <rect
                  {...rectProps(box)}
                  className={`${style.stroke} ${style.fill}`}
                  fillOpacity={on ? 1 : 0}
                  strokeWidth={on ? 6 : 4}
                  strokeDasharray={on ? undefined : "14 10"}
                />
                <Label
                  x={box.x1 + 8}
                  y={Math.max(box.y1 - 10, 30)}
                  text={BANNER_LAYER_KIND_LABELS[box.kind] ?? box.kind}
                  className={style.text}
                />
              </g>
            );
          })}
        </g>
      ) : null}
    </svg>
  );
}

/** Какие группы слоёв видны в момент `t` (перерисовка только при смене набора). */
function useActiveGroups(windows: readonly BannerWindow[], timeStore?: PlayerTimeStore) {
  const persistent = windows.filter((w) => w.group === "persistent");
  const withCreative = windows.filter((w) => w.group === "with_creative");
  const key = useTimeSelector(
    timeStore,
    (t) => `${persistent.length === 0 || inWindow(persistent, t) ? 1 : 0}${inWindow(withCreative, t) ? 1 : 0}`,
  );
  return { persistent: key[0] === "1", with_creative: key[1] === "1" };
}

const noopStore: PlayerTimeStore = {
  getState: () => ({ t: 0, duration: 0, playing: false }),
  getInitialState: () => ({ t: 0, duration: 0, playing: false }),
  setState: () => undefined,
  subscribe: () => () => undefined,
};

/** Производная от времени плеера; компонент перерисуется, только когда результат изменится. */
export function useTimeSelector<T>(timeStore: PlayerTimeStore | undefined, select: (t: number) => T): T {
  return useStore(timeStore ?? noopStore, (state) => select(state.t));
}
