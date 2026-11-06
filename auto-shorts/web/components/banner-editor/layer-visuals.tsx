"use client";

import { memo, useMemo } from "react";

import { mediaSrc } from "@/lib/api/media";
import type { BannerCreative } from "@/lib/queries/creatives";
import { anchorInSlot, type Box, type LocalPlacement } from "@/lib/safe-zones";

import { type BannerLayer, effectiveTextStyle, renderLayerText, type TextContext } from "./draft";
import { cssFont, layoutText } from "./fonts";

/** Картинка креатива: статика — нормализованный PNG, анимация — первый кадр (движение — в «Кадре из рендера»). */
export function creativeImageSrc(creative: BannerCreative | undefined): string | undefined {
  if (!creative || creative.status !== "ready") return undefined;
  const url = creative.kind === "animated" ? creative.first_frame_url : creative.file_url;
  return mediaSrc(url ?? creative.thumb_url);
}

function clipRectId(prefix: string, layerId: string) {
  return `${prefix}-clip-${layerId.replace(/[^A-Za-z0-9_-]/g, "_")}`;
}

export const CreativeVisual = memo(function CreativeVisual({
  placement,
  layer,
  creative,
  idPrefix,
  opacity,
}: {
  placement: LocalPlacement;
  layer: BannerLayer;
  creative: BannerCreative | undefined;
  idPrefix: string;
  opacity: number;
}) {
  const src = creativeImageSrc(creative);
  const visible = placement.rect;
  const full = placement.full;
  if (!src || !full || !visible) {
    const slot = placement.slot;
    return (
      <g opacity={opacity}>
        <rect
          x={slot.x}
          y={slot.y}
          width={slot.w}
          height={slot.h}
          fill="var(--machine)"
          fillOpacity="0.12"
          stroke="var(--machine)"
          strokeDasharray="6 6"
          strokeWidth="1.5"
          vectorEffect="non-scaling-stroke"
        />
        <text
          x={slot.x + slot.w / 2}
          y={slot.y + slot.h / 2}
          textAnchor="middle"
          dominantBaseline="central"
          fontSize={30}
          fill="var(--machine)"
          fontFamily="var(--font-mono)"
        >
          {creative?.status === "processing" ? "креатив обрабатывается…" : "нет файла креатива"}
        </text>
      </g>
    );
  }
  const clipId = clipRectId(idPrefix, layer.id);
  const radius = layer.slot.corner_radius_px;
  return (
    <g opacity={opacity * layer.slot.opacity} data-layer-visual={layer.id}>
      <clipPath id={clipId}>
        <rect x={visible.x} y={visible.y} width={visible.w} height={visible.h} rx={radius} ry={radius} />
      </clipPath>
      <image
        href={src}
        x={full.x}
        y={full.y}
        width={full.w}
        height={full.h}
        preserveAspectRatio="none"
        clipPath={`url(#${clipId})`}
      />
    </g>
  );
});

export interface TextVisualResult {
  plate: Box;
}

export const TextVisual = memo(function TextVisual({
  placement,
  layer,
  ctx,
  opacity,
  fontsLoaded,
}: {
  placement: LocalPlacement;
  layer: BannerLayer;
  ctx: TextContext;
  opacity: number;
  fontsLoaded: number;
}) {
  const style = effectiveTextStyle(layer);
  const text = renderLayerText(layer.text, ctx);
  const slot = placement.slot;
  const layout = useMemo(
    () => layoutText(text || " ", style, slot.w, slot.h),
    // fontsLoaded: после загрузки шрифта размеры текста пересчитываются
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [text, style, slot.w, slot.h, fontsLoaded],
  );
  if (!text) return null;
  const plateW = style.hug_text ? Math.round(layout.width + style.padding_x * 2) : slot.w;
  const plateH = style.hug_text ? Math.round(layout.height + style.padding_y * 2) : slot.h;
  const plate = anchorInSlot(Math.min(plateW, slot.w), Math.min(plateH, slot.h), slot);
  const lineH = layout.fontPx * style.line_spacing;
  const blockTop = plate.y + (plate.h - lineH * layout.lines.length) / 2;
  const anchor = style.align === "left" ? "start" : style.align === "right" ? "end" : "middle";
  const tx =
    style.align === "left"
      ? plate.x + style.padding_x
      : style.align === "right"
        ? plate.x + plate.w - style.padding_x
        : plate.x + plate.w / 2;
  const layerOpacity = opacity * layer.slot.opacity;
  return (
    <g opacity={layerOpacity} data-layer-visual={layer.id}>
      {style.bg_opacity > 0 ? (
        <rect
          x={plate.x}
          y={plate.y}
          width={plate.w}
          height={plate.h}
          rx={style.corner_radius_px}
          ry={style.corner_radius_px}
          fill={style.bg_color}
          fillOpacity={style.bg_opacity}
        />
      ) : null}
      <text
        textAnchor={anchor}
        fill={style.color}
        fillOpacity={style.text_opacity}
        stroke={style.stroke_px > 0 ? style.stroke_color : undefined}
        strokeWidth={style.stroke_px > 0 ? style.stroke_px * 2 : undefined}
        paintOrder="stroke"
        style={{ font: cssFont(style, layout.fontPx) }}
      >
        {layout.lines.map((line, index) => (
          <tspan key={index} x={tx} y={blockTop + lineH * index + lineH / 2} dominantBaseline="central">
            {line}
          </tspan>
        ))}
      </text>
    </g>
  );
});
