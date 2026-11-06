"use client";

/**
 * Канал ролика в приёмке: куда он уйдёт после одобрения. Смена канала до одобрения — баннер и оформление
 * нового канала, ролик перерендерится и вернётся в приёмку; после одобрения пакет и автозалив идут в план
 * нового канала. Вне приёмки (в работе, одобрен) — только показ.
 */
import { toast } from "sonner";

import { PLATFORM_LABELS } from "@/components/production/job-meta";
import { Select } from "@/components/ui";
import { errorMessage } from "@/lib/api/errors";
import { useChannelOptions } from "@/lib/queries/jobs";
import { useJobChannel, useSetJobChannel } from "@/lib/queries/layout";

export function ChannelPicker({
  jobId,
  platform,
  fallbackTitle,
  canOperate,
}: {
  jobId: number;
  platform: "youtube" | "vk" | string;
  fallbackTitle: string;
  canOperate: boolean;
}) {
  const view = useJobChannel(jobId);
  const channels = useChannelOptions();
  const save = useSetJobChannel(jobId);
  const data = view.data;
  const options = (channels.data ?? []).filter((c) => c.platform === platform);
  const editable = canOperate && !!data?.change_allowed && options.length > 1;
  const label = PLATFORM_LABELS[platform as keyof typeof PLATFORM_LABELS] ?? platform;

  if (!editable) {
    return (
      <span className="truncate" title={data?.change_hint ?? (canOperate ? undefined : "Нужна роль оператора")}>
        {data?.channel_title ?? fallbackTitle}
        <span className="text-fg-muted"> · {label}</span>
      </span>
    );
  }
  return (
    <Select
      aria-label="Канал, куда ролик уйдёт после одобрения"
      title="Куда ролик уйдёт после одобрения. Смена — перерендер с баннером и оформлением выбранного канала"
      value={data?.channel_id ?? ""}
      disabled={save.isPending}
      className="h-7 w-full text-xs"
      data-testid="review-channel"
      onChange={(event) => {
        const next = event.target.value;
        const title = options.find((c) => c.id === next)?.title ?? next;
        save.mutate(
          { channel_id: next, rerender: true },
          {
            onSuccess: (res) =>
              toast.success(
                res.rerender_started
                  ? `Канал «${title}»: ролик перерендерится в его оформлении и вернётся в приёмку`
                  : `Канал «${title}»`,
                { id: `channel-${jobId}` },
              ),
            onError: (err) => toast.error(errorMessage(err), { id: `channel-${jobId}` }),
          },
        );
      }}
    >
      {options.map((c) => (
        <option key={c.id} value={c.id}>
          {c.title ?? c.id} · {label}
        </option>
      ))}
    </Select>
  );
}
