/**
 * Подписи экрана «Пакеты» (повторяют `LABELS` domain/enums.py и `CHECKLIST_LABELS`
 * services/packages.py; источник истины — сервер). Статус всегда цветом **и** иконкой (§7.4).
 */
import {
  Ban,
  CalendarClock,
  CircleCheck,
  CircleDashed,
  CircleQuestionMark,
  CircleSlash,
  CloudUpload,
  Download,
  EyeOff,
  Hourglass,
  Link2,
  Lock,
  OctagonAlert,
  PackageCheck,
  Replace,
  SearchX,
  ShieldAlert,
  ShieldCheck,
  TriangleAlert,
  UserX,
} from "lucide-react";

import type { StatusMetaMap } from "@/components/ui/status-badge";
import type { Schemas } from "@/lib/api/client";

export const PACKAGE_STATUS_META: StatusMetaMap<Schemas["PackageStatus"]> = {
  ready: { label: "Готов к заливке", variant: "human", icon: PackageCheck },
  downloaded: { label: "Скачан — ждёт ссылку", variant: "human", icon: Download, outline: true },
  published: { label: "Опубликован", variant: "positive", icon: CircleCheck },
  superseded: { label: "Заменён", variant: "neutral", icon: Replace, outline: true },
};

export const VERIFY_STATUS_META: StatusMetaMap<Schemas["VerifyStatus"]> = {
  pending: { label: "Проверяется", variant: "machine", icon: Hourglass },
  ok: { label: "Канал подтверждён", variant: "positive", icon: ShieldCheck },
  channel_mismatch: { label: "Чужой канал", variant: "negative", icon: UserX },
  not_found: { label: "Ролик не найден", variant: "negative", icon: SearchX },
  unverifiable: { label: "Не проверить", variant: "warning", icon: CircleQuestionMark },
};

export const PUBLICATION_STATUS_META: StatusMetaMap<Schemas["PublicationStatus"]> = {
  live: { label: "Опубликован", variant: "positive", icon: CircleCheck },
  unlisted: { label: "По ссылке", variant: "warning", icon: Link2 },
  private: { label: "Скрыт", variant: "warning", icon: EyeOff },
  removed_by_platform: { label: "Снят площадкой", variant: "negative", icon: OctagonAlert },
  deleted_by_us: { label: "Удалён нами", variant: "neutral", icon: Ban, outline: true },
};

/** Что делать оператору при каждом исходе проверки принадлежности. */
export const VERIFY_HINTS: Partial<Record<Schemas["VerifyStatus"], string>> = {
  pending: "Проверка идёт в фоне: YouTube — по oEmbed, VK — сразу по владельцу в ссылке.",
  channel_mismatch:
    "Ролик по ссылке принадлежит другому каналу. Если залили не туда — удалите заливку на площадке и регистрацию здесь, затем вставьте правильную ссылку.",
  not_found: "Площадка не отдаёт ролик: он ещё обрабатывается, скрыт или удалён. Повторите проверку через пару минут.",
  unverifiable:
    "Автоматически не сопоставить (например, у канала в карточке только /channel/UC…, а площадка отдаёт @handle). Проверьте глазами и дополните ссылку канала в «Каналах».",
};

/**
 * Пункты чек-листа. Ключи — строки, а не `ChecklistCode`: сервер уже хранит `ad_marking` и
 * `content_id`, и как только их добавят в enum, экран покажет их без правок.
 */
export const CHECKLIST_LABELS: Record<string, string> = {
  file_downloaded: "Файл скачан",
  right_account: "Открыт нужный аккаунт канала",
  texts_pasted: "Заголовок и описание вставлены",
  hashtags: "Хэштеги добавлены в конец описания",
  related_video: "Related video указан (длинный ролик темы)",
  pinned_comment: "Комментарий опубликован и закреплён",
  ai_disclosure: "Отмечено «Изменённый или синтетический контент» (AI-метка)",
  ad_marking: "В кадре и описании есть «Реклама · erid»",
  content_id: "Проверен allowlist Content ID правообладателя",
  published: "Ролик опубликован, ссылка вставлена",
};

/** Подсказка «где это на площадке» к пунктам, которые чаще всего забывают. */
export const CHECKLIST_HINTS: Record<string, Partial<Record<Schemas["Platform"], string>>> = {
  ai_disclosure: {
    youtube: "YouTube Studio → «Подробнее» → «Изменённый контент» → «Да». Нужна при синтетическом голосе или картинке.",
    vk: "Отметьте в описании, что голос или изображение созданы ИИ.",
  },
  related_video: { youtube: "Studio → ролик → «Связанное видео» (Related video)." },
  right_account: { youtube: "Проверьте аватар в правом верхнем углу Studio — Brand Account канала." },
};

export function checklistLabel(code: string): string {
  return CHECKLIST_LABELS[code] ?? code;
}

export const TEXTS_MODE_META: Record<Schemas["PackageTextsMode"], { variant: "machine" | "warning" | "accent" }> = {
  llm: { variant: "machine" },
  template: { variant: "warning" },
  operator: { variant: "accent" },
};

export const ORIGINALITY_VERDICT_META: StatusMetaMap<Schemas["OriginalityVerdict"]> = {
  pass: { label: "Оригинальность: норма", variant: "positive", icon: ShieldCheck },
  warn: { label: "Оригинальность: на грани", variant: "warning", icon: TriangleAlert },
  fail: { label: "Оригинальность: провал", variant: "negative", icon: ShieldAlert },
};

/** Автозалив через API площадки (0019): очередь → заливка → залит, ошибка или снят. */
export const AUTOPUBLISH_STATE_META: StatusMetaMap<Schemas["AutopublishState"]> = {
  scheduled: { label: "В очереди", variant: "machine", icon: CalendarClock },
  uploading: { label: "Заливается", variant: "machine", icon: CloudUpload, outline: true },
  done: { label: "Залит автоматически", variant: "positive", icon: CircleCheck },
  failed: { label: "Ошибка автозалива", variant: "negative", icon: OctagonAlert },
  cancelled: { label: "Снят с очереди", variant: "neutral", icon: CircleSlash, outline: true },
};

export const PRIVACY_LABELS: Record<Schemas["VideoPrivacy"], string> = {
  public: "публичный",
  unlisted: "по ссылке",
  private: "скрытый",
};

export const PLATFORM_ACCOUNT_LABEL: Record<Schemas["Platform"], string> = {
  youtube: "YouTube Studio",
  vk: "VK Видео",
};

export const INGEST_OUTCOME_META: StatusMetaMap<Schemas["IngestOutcome"]> = {
  accepted: { label: "Принят", variant: "positive", icon: CircleCheck },
  invalid_signature: { label: "Неверная подпись", variant: "negative", icon: Lock },
  stale_timestamp: { label: "Время вне окна", variant: "negative", icon: Hourglass },
  unknown_product: { label: "Неизвестный продукт", variant: "negative", icon: CircleSlash },
  payload_too_large: { label: "Слишком большое тело", variant: "warning", icon: TriangleAlert },
  rate_limited: { label: "Лимит запросов", variant: "warning", icon: CircleDashed },
  invalid_body: { label: "Кривое тело", variant: "warning", icon: TriangleAlert },
};

export const CONVERSION_EVENT_LABELS: Record<Schemas["ConversionEventType"], string> = {
  bot_start: "Старт бота",
  tma_open: "Открытие Mini App",
  site_visit: "Визит на сайт",
  signup: "Регистрация",
  trial_start: "Начало пробного",
  lead: "Заявка",
  first_payment: "Первая оплата",
  payment: "Оплата",
  refund: "Возврат",
};

export const TRACKING_LEVEL_LABELS: Record<Schemas["TrackingLevel"], string> = {
  package: "Пакет",
  publication: "Алиас площадки",
  channel: "Канал",
  campaign: "Кампания",
  manual: "Ручная",
};

export const UPLOAD_ICON = CloudUpload;
