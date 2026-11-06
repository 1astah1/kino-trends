"use client";

import { VideoOff } from "lucide-react";
import { useState } from "react";

import { mediaSrc } from "@/lib/api/media";
import { cn } from "@/lib/cn";

/**
 * Мини-плеер панели ролика (§7.3 «Конвейер»): превью 9:16 с нативными контролами. URL — только из API.
 */
export function MiniPlayer({
  src,
  poster,
  label,
  className,
}: {
  src: string | null | undefined;
  poster?: string | null;
  label: string;
  className?: string;
}) {
  const safeSrc = mediaSrc(src);
  const safePoster = mediaSrc(poster);
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  const failed = failedSrc !== null && failedSrc === safeSrc;

  return (
    <div className={cn("relative aspect-[9/16] w-full overflow-hidden bg-video", className)} data-testid="mini-player">
      {safeSrc && !failed ? (
        <video
          key={safeSrc}
          src={safeSrc}
          poster={safePoster}
          controls
          playsInline
          preload="metadata"
          aria-label={label}
          onError={() => setFailedSrc(safeSrc)}
          className="absolute inset-0 h-full w-full object-contain"
        />
      ) : (
        <>
          {safePoster ? (
            // eslint-disable-next-line @next/next/no-img-element -- статический экспорт, картинки без оптимизатора
            <img src={safePoster} alt={label} className="absolute inset-0 h-full w-full object-contain" />
          ) : null}
          <span className="absolute inset-x-2 bottom-2 flex items-center justify-center gap-1 rounded-sm bg-black/70 px-1.5 py-1 text-2xs text-white">
            <VideoOff className="h-3 w-3" aria-hidden="true" />
            {failed ? "не загрузилось" : "превью ещё нет"}
          </span>
        </>
      )}
    </div>
  );
}
