"use client";

/**
 * Галочка «Субтитры» в приёмке: снять — ролик перерендерится без субтитров (заголовок и тезисы остаются),
 * поставить — с субтитрами. Перерендер — со стадии render, ролик на это время уходит из очереди.
 */
import { toast } from "sonner";

import { errorMessage } from "@/lib/api/errors";
import { cn } from "@/lib/cn";
import { useJobCaptions, useSetJobCaptions } from "@/lib/queries/layout";

export function CaptionsToggle({ jobId, canOperate, className }: { jobId: number; canOperate: boolean; className?: string }) {
  const captions = useJobCaptions(jobId);
  const save = useSetJobCaptions(jobId);
  const data = captions.data;
  const locked = !data?.rerender_allowed;
  const disabled = !canOperate || !data || save.isPending || locked;
  const hint = !canOperate
    ? "Нужна роль оператора"
    : locked
      ? (data?.rerender_hint ?? "Сейчас не поменять")
      : data?.enabled
        ? "Снимите галочку — ролик перерендерится без субтитров"
        : "Поставьте галочку — ролик перерендерится с субтитрами";

  return (
    <label
      className={cn(
        "inline-flex select-none items-center gap-2 rounded-md border border-border px-2 py-1 text-sm",
        disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer hover:bg-surface-2",
        className,
      )}
      title={hint}
    >
      <input
        type="checkbox"
        className="h-4 w-4 accent-[var(--accent)]"
        checked={data?.enabled ?? true}
        disabled={disabled}
        aria-describedby={`captions-hint-${jobId}`}
        onChange={(e) => {
          const enabled = e.target.checked;
          save.mutate(
            { enabled, rerender: true },
            {
              onSuccess: (res) =>
                toast.success(
                  res.rerender_started
                    ? enabled
                      ? "Субтитры включены — ролик перерендерится и вернётся в приёмку"
                      : "Субтитры выключены — ролик перерендерится и вернётся в приёмку"
                    : "Сохранено",
                  { id: `captions-${jobId}` },
                ),
              onError: (err) => toast.error(errorMessage(err), { id: `captions-${jobId}` }),
            },
          );
        }}
      />
      <span>Субтитры</span>
      <span id={`captions-hint-${jobId}`} className="sr-only">
        {hint}
      </span>
    </label>
  );
}
