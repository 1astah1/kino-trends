"use client";

import { Play } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui";
import { errorMessage } from "@/lib/api/errors";
import { type TrendsEnqueueRequest, useEnqueueTrend } from "@/lib/queries/trends";

/**
 * «В конвейер»: кино (`kind=film`, `ref_id=title_id`) или ролик (`kind=video`, `ref_id=video_id`)
 * уходит в очередь Kino-trends, та готовит файл и шлёт источник в auto-shorts. Повтор активного
 * задания новое не создаёт — Kino-trends вернёт существующее. Только для оператора (UI лишь прячет).
 */
export function EnqueueButton({
  kind,
  refId,
  disabled,
  title,
  size = "xs",
}: {
  kind: TrendsEnqueueRequest["kind"];
  refId: string;
  disabled?: boolean;
  title?: string;
  size?: "xs" | "sm";
}) {
  const enqueue = useEnqueueTrend();

  async function onClick() {
    try {
      const item = await enqueue.mutateAsync({ kind, ref_id: refId });
      toast.success("Поставлено в конвейер", {
        description: `${item.label ?? refId}: ${item.status_ru ?? item.status}`,
      });
    } catch (error) {
      toast.error("Не поставлено в конвейер", { description: errorMessage(error) });
    }
  }

  return (
    <Button
      variant="human"
      size={size}
      onClick={() => void onClick()}
      disabled={disabled || enqueue.isPending}
      title={title ?? "Поставить в очередь auto-shorts: файл, нарезка, оформление, ревью, залив"}
      data-testid="trends-enqueue"
    >
      <Play className="h-3 w-3" aria-hidden="true" />
      {enqueue.isPending ? "Ставлю…" : "В конвейер"}
    </Button>
  );
}
