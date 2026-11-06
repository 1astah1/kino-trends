/**
 * Подписи каталога продуктов и кампаний. Повторяют `LABELS` из src/autoshorts/domain/enums.py
 * (источник истины — `GET /meta/enums`), здесь — чтобы экран не ждал справочник.
 */
import { Archive, CircleCheck, CircleStop, PauseCircle, PencilLine, PlayCircle } from "lucide-react";

import type { StatusMetaMap } from "@/components/ui/status-badge";
import type { Schemas } from "@/lib/api/client";

export const PRODUCT_KIND_LABELS: Record<Schemas["ProductKind"], string> = {
  web_service: "Веб-сервис",
  telegram_bot: "Telegram-бот",
  telegram_mini_app: "Mini App",
  other: "Другое",
};

export const PRODUCT_STATUS_META: StatusMetaMap<Schemas["ProductStatus"]> = {
  active: { label: "Активен", variant: "positive", icon: CircleCheck },
  archived: { label: "В архиве", variant: "neutral", icon: Archive, outline: true },
};

export const REDIRECT_MODE_LABELS: Record<Schemas["RedirectMode"], string> = {
  landing_split: "Лендинг с выбором",
  direct_site: "Сразу на сайт",
  direct_telegram: "Сразу в Telegram",
};

export const TELEGRAM_LINK_STYLE_LABELS: Record<Schemas["TelegramLinkStyle"], string> = {
  tg_scheme_with_fallback: "tg:// с запасной https-ссылкой",
  https_tme: "Только ссылка telegram.me",
};

export const CAMPAIGN_STATUS_META: StatusMetaMap<Schemas["CampaignStatus"]> = {
  draft: { label: "Черновик", variant: "neutral", icon: PencilLine },
  active: { label: "Активна", variant: "positive", icon: PlayCircle },
  paused: { label: "На паузе", variant: "warning", icon: PauseCircle },
  ended: { label: "Завершена", variant: "neutral", icon: CircleStop, outline: true },
};

export const CAMPAIGN_ALLOCATION_LABELS: Record<Schemas["CampaignAllocation"], string> = {
  weighted: "По весам (A/B)",
  round_robin: "По очереди",
  manual: "Вручную на ролике",
};

export const PLATFORM_LABELS: Record<Schemas["Platform"], string> = {
  youtube: "YouTube Shorts",
  vk: "VK Клипы",
};

export const JOB_KIND_LABELS: Record<Schemas["JobKind"], string> = {
  generate: "Генерация",
  clip: "Нарезка",
};

/** Копейки/центы → «1 234,50 ₽». */
export function formatMinor(value: number | null | undefined, currency: string | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const code = (currency || "RUB").toUpperCase();
  try {
    return new Intl.NumberFormat("ru-RU", { style: "currency", currency: code, maximumFractionDigits: 0 }).format(
      value / 100,
    );
  } catch {
    return `${(value / 100).toFixed(0)} ${code}`;
  }
}
