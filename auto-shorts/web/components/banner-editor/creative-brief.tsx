"use client";

import { Film, ImageOff } from "lucide-react";

import type { Schemas } from "@/lib/api/client";
import { mediaSrc } from "@/lib/api/media";
import { cn } from "@/lib/cn";

/** Миниатюра креатива из `BannerCreativeBrief` (списки вариантов). */
export function CreativeThumbBrief({
  creative,
  className,
}: {
  creative: Schemas["BannerCreativeBrief"] | undefined;
  className?: string;
}) {
  const src = mediaSrc(creative?.thumb_url);
  return (
    <span
      className={cn(
        "relative flex items-center justify-center overflow-hidden rounded-sm border border-border bg-[repeating-conic-gradient(#2b2d31_0_25%,#3a3d42_0_50%)] bg-[length:10px_10px]",
        className,
      )}
      title={creative?.name}
    >
      {src ? (
        // eslint-disable-next-line @next/next/no-img-element -- миниатюра из API
        <img src={src} alt="" className="max-h-full max-w-full object-contain" loading="lazy" />
      ) : (
        <ImageOff className="h-3.5 w-3.5 text-fg-muted" aria-label={creative ? "нет миниатюры" : "без креатива"} />
      )}
      {creative?.kind === "animated" ? (
        <Film className="absolute right-0 bottom-0 h-2.5 w-2.5 text-white drop-shadow" aria-label="анимация" />
      ) : null}
    </span>
  );
}
