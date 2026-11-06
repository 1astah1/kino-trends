"use client";

import { useEffect, useState } from "react";

import { mediaSrc } from "@/lib/api/media";
import { type BannerFont, useBannerFonts } from "@/lib/queries/banners";

import type { TextStyle } from "./draft";

/**
 * Шрифты служебного текста из `config/fonts` (`GET /banner-fonts`) подключаются через FontFace с того же
 * origin (`/api/v1/banner-fonts/{name}`, CSP `font-src 'self'`): превью текста на холсте — теми же
 * шрифтами, что в рендере (WYSIWYG, §7.6). Имя семейства — от имени файла, латиницей.
 */
export function fontFamilyFor(fontFile: string): string {
  return `bf_${fontFile.replace(/\.[a-z0-9]+$/i, "").replace(/[^A-Za-z0-9_-]/g, "_")}`;
}

const loaded = new Set<string>();

/** Подключает шрифты; возвращает счётчик загруженных — от него зависит перерасчёт размеров текста. */
export function useBannerFontFaces(): { fonts: BannerFont[]; loadedCount: number } {
  const fonts = useBannerFonts();
  const [loadedCount, setLoadedCount] = useState(() => loaded.size);

  useEffect(() => {
    if (!fonts.data || typeof document === "undefined" || typeof FontFace === "undefined") return;
    let cancelled = false;
    for (const font of fonts.data) {
      const family = fontFamilyFor(font.name);
      const src = mediaSrc(font.file_url);
      if (loaded.has(family) || !src) continue;
      const face = new FontFace(family, `url(${src})`);
      face
        .load()
        .then((ready) => {
          document.fonts.add(ready);
          loaded.add(family);
          if (!cancelled) setLoadedCount(loaded.size);
        })
        .catch(() => {
          // Шрифт не загрузился — превью покажет запасной; рендер всё равно на сервере.
        });
    }
    return () => {
      cancelled = true;
    };
  }, [fonts.data]);

  return { fonts: fonts.data ?? [], loadedCount };
}

let measureContext: CanvasRenderingContext2D | null = null;

function context(): CanvasRenderingContext2D | null {
  if (measureContext) return measureContext;
  if (typeof document === "undefined") return null;
  measureContext = document.createElement("canvas").getContext("2d");
  return measureContext;
}

export function cssFont(style: Pick<TextStyle, "font_file">, px: number): string {
  return `${px}px "${fontFamilyFor(style.font_file)}", "Onest Variable", sans-serif`;
}

export function measureText(text: string, style: Pick<TextStyle, "font_file">, px: number): number {
  const ctx = context();
  if (!ctx) return text.length * px * 0.6;
  ctx.font = cssFont(style, px);
  return ctx.measureText(text).width;
}

export interface TextLayout {
  lines: string[];
  fontPx: number;
  /** Ширина самой длинной строки. */
  width: number;
  /** Высота блока строк. */
  height: number;
  shrunkBelowMin: boolean;
  truncated: boolean;
}

function wrap(text: string, style: TextStyle, px: number, maxWidth: number): string[] {
  const words = text.split(/\s+/).filter(Boolean);
  const lines: string[] = [];
  let current = "";
  for (const word of words) {
    const candidate = current ? `${current} ${word}` : word;
    if (!current || measureText(candidate, style, px) <= maxWidth) current = candidate;
    else {
      lines.push(current);
      current = word;
    }
  }
  if (current) lines.push(current);
  return lines;
}

/**
 * Приближение `banner_text.layout_text`: кегль от `max_font_px` вниз до `min_font_px`, пока текст
 * не влезет в слот за вычетом полей; строк не больше `max_lines`. Точный текст — только в «Кадре из рендера».
 */
export function layoutText(text: string, style: TextStyle, slotW: number, slotH: number): TextLayout {
  const maxW = Math.max(1, slotW - style.padding_x * 2);
  const maxH = Math.max(1, slotH - style.padding_y * 2);
  const maxLines = Math.max(1, style.max_lines);
  let px = Math.max(style.min_font_px, style.max_font_px);
  const min = Math.max(6, Math.min(style.min_font_px, style.max_font_px));
  for (;;) {
    const lines = maxLines === 1 ? [text] : wrap(text, style, px, maxW);
    const width = Math.max(0, ...lines.map((line) => measureText(line, style, px)));
    const lineH = px * style.line_spacing;
    const height = lineH * Math.min(lines.length, maxLines);
    if ((width <= maxW && lines.length <= maxLines && height <= maxH + 1) || px <= min) {
      const fitted = lines.slice(0, maxLines);
      const truncated = lines.length > maxLines || width > maxW;
      return {
        lines: fitted,
        fontPx: px,
        width: Math.min(width, maxW),
        height: lineH * fitted.length,
        shrunkBelowMin: false,
        truncated,
      };
    }
    px -= 1;
  }
}
