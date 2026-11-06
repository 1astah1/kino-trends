/** Подписи каналов (повторяют `LABELS` domain/enums.py; источник истины — `/meta/enums`). */
import { Ban, CircleCheck, CircleSlash, Flame, OctagonAlert, PauseCircle } from "lucide-react";

import type { StatusMetaMap } from "@/components/ui/status-badge";
import type { Schemas } from "@/lib/api/client";

export const CHANNEL_STATUS_META: StatusMetaMap<Schemas["ChannelStatus"]> = {
  warming: { label: "Прогрев", variant: "human", icon: Flame },
  active: { label: "Активен", variant: "positive", icon: CircleCheck },
  paused: { label: "На паузе", variant: "warning", icon: PauseCircle },
  sanctioned: { label: "Санкции", variant: "negative", icon: OctagonAlert },
  terminated: { label: "Удалён площадкой", variant: "negative", icon: Ban, outline: true },
  retired: { label: "Выведен", variant: "neutral", icon: CircleSlash, outline: true },
};

export const INCIDENT_KIND_LABELS: Record<Schemas["IncidentKind"], string> = {
  reach_drop: "Падение охвата",
  sanction: "Санкция",
  strike: "Страйк",
  claim: "Клейм",
  unique_phrase_test: "Тест уникальной фразы",
  freeze: "Заморозка",
  note: "Заметка",
};

export const PLATFORM_SHORT: Record<Schemas["Platform"], string> = {
  youtube: "YouTube",
  vk: "VK",
};

/** Сигналы санкций здоровья канала (`ChannelHealth.sanction_signals`). */
export const SANCTION_SIGNAL_LABELS: Record<string, string> = {
  uploader_suspended: "Загрузки приостановлены",
  reach_drop: "Падение охвата",
  removed_videos: "Ролики сняты площадкой",
  strike: "Страйк",
  claim: "Клейм",
  freeze: "Заморозка",
};
