/**
 * Подписи и оформление трека нарезки (docs/ARCHITECTURE.md §6.3, §7.3). Статус — цветом **и**
 * иконкой с подписью (§7.4). Цвет говорит, чья очередь действовать: машина — фиолет, «твой ход» —
 * янтарь, провал — красный. Подписи режимов отбора и авторского текста приходят с сервера
 * (`origin_label`, `selection.label`, `JobAuthorScript.label`); здесь только запасные.
 */
import {
  Archive,
  AudioLines,
  Ban,
  CircleCheck,
  CircleDashed,
  CircleX,
  Clock,
  Cog,
  Download,
  FileQuestionMark,
  Hand,
  Info,
  LoaderCircle,
  type LucideIcon,
  OctagonAlert,
  Scissors,
  SkipForward,
  Sparkles,
  ThumbsUp,
  TriangleAlert,
  WandSparkles,
} from "lucide-react";

import type { StatusMetaMap } from "@/components/ui/status-badge";
import type { Schemas } from "@/lib/api/client";

type SourceStatus = Schemas["SourceStatus"];
type SourceStep = Schemas["SourceStep"];
type SourceStepStatus = Schemas["SourceStepStatus"];
type RightsType = Schemas["RightsType"];
type AllowlistStatus = Schemas["AllowlistStatus"];
type SourceOrigin = Schemas["SourceOrigin"];
type SelectionMode = Schemas["SelectionMode"];
type CandidateStatus = Schemas["CandidateStatus"];
type CandidateFlag = Schemas["CandidateFlag"];
type CandidateOrigin = Schemas["CandidateOrigin"];
type HookType = Schemas["HookType"];
type OriginalityVerdict = Schemas["OriginalityVerdict"];
type OriginalityCheckStatus = Schemas["OriginalityCheckStatus"];
type AuthorVoiceSegment = Schemas["AuthorVoiceSegment"];
type AuthorVoiceKind = Schemas["AuthorVoiceKind"];
type AuthorVoiceStatus = Schemas["AuthorVoiceStatus"];
type AuthorVoiceTiming = Schemas["AuthorVoiceTiming"];
type AuthorScriptMode = Schemas["AuthorScriptMode"];
type ReframeMode = Schemas["ReframeMode"];
type AuthorInsertMode = Schemas["AuthorInsertMode"];
type AuthorCtaStyle = Schemas["AuthorCtaStyle"];
type SelectorChoice = Schemas["SelectorChoice"];
type WatermarkSeverity = Schemas["WatermarkSeverity"];

// ---------------------------------------------------------------- источник

export const SOURCE_STATUS_META: StatusMetaMap<SourceStatus> = {
  queued: { label: "В очереди", variant: "machine", icon: Clock, outline: true },
  downloading: { label: "Скачивается", variant: "machine", icon: Download },
  preparing: { label: "Подготовка", variant: "machine", icon: Cog },
  transcribing: { label: "Распознавание", variant: "machine", icon: AudioLines },
  selecting: { label: "Отбор моментов", variant: "machine", icon: Sparkles },
  ready: { label: "Готов к нарезке", variant: "human", icon: Scissors },
  failed: { label: "Ошибка", variant: "negative", icon: OctagonAlert },
  archived: { label: "В архиве", variant: "neutral", icon: Archive, outline: true },
};

export const SOURCE_STATUS_ORDER: readonly SourceStatus[] = [
  "queued",
  "downloading",
  "preparing",
  "transcribing",
  "selecting",
  "ready",
  "failed",
  "archived",
];

/** Фильтр списка (`?status=`): группы статусов. */
export const SOURCE_FILTERS = {
  all: { label: "Все", statuses: null },
  processing: {
    label: "В обработке",
    statuses: ["queued", "downloading", "preparing", "transcribing", "selecting"] as SourceStatus[],
  },
  ready: { label: "Готовы", statuses: ["ready"] as SourceStatus[] },
  failed: { label: "С ошибкой", statuses: ["failed"] as SourceStatus[] },
  archived: { label: "Архив", statuses: ["archived"] as SourceStatus[] },
} as const;
export type SourceFilter = keyof typeof SOURCE_FILTERS;
export const SOURCE_FILTER_KEYS = Object.keys(SOURCE_FILTERS) as SourceFilter[];

export const SOURCE_STEP_LABELS: Record<SourceStep, string> = {
  download: "Скачивание",
  prepare: "Прокси, волна, раскадровка",
  transcribe: "Распознавание речи",
  select: "Отбор моментов",
  scenes: "Склейки",
};

export const STEP_STATUS_META: StatusMetaMap<SourceStepStatus> = {
  pending: { label: "Ждёт", variant: "neutral", icon: CircleDashed },
  queued: { label: "В очереди", variant: "machine", icon: Clock, outline: true },
  running: { label: "Идёт", variant: "machine", icon: LoaderCircle },
  succeeded: { label: "Готово", variant: "positive", icon: CircleCheck },
  failed: { label: "Ошибка", variant: "negative", icon: CircleX },
  cancelled: { label: "Отменено", variant: "neutral", icon: Ban },
  skipped: { label: "Пропущено", variant: "neutral", icon: SkipForward },
};

export const SOURCE_ORIGIN_LABELS: Record<SourceOrigin, string> = { upload: "Файл", url: "Ссылка" };

export const WATERMARK_META: Record<WatermarkSeverity, { label: string; variant: "neutral" | "warning" }> = {
  ok: { label: "Чужих логотипов нет", variant: "neutral" },
  hint: { label: "Возможен чужой логотип", variant: "warning" },
  warning: { label: "Чужой водяной знак", variant: "warning" },
};

// ---------------------------------------------------------------- права

export const RIGHTS_TYPE_LABELS: Record<RightsType, string> = {
  own: "Своё видео",
  licensed_written: "Лицензия по договору",
  licensed_platform: "Лицензия площадки",
  cc_by: "CC BY",
  public_domain: "Общественное достояние",
};

export const RIGHTS_TYPE_HINTS: Record<RightsType, string> = {
  own: "Вы автор и правообладатель. Гейт 30% авторского вклада только показывается.",
  licensed_written: "Письменное разрешение правообладателя. Нужны атрибуция и документ; гейт авторского вклада ≥ 30%.",
  licensed_platform:
    "Лицензия, выданная через площадку (например, партнёрская). Нужна атрибуция; гейт авторского вклада ≥ 30%.",
  cc_by:
    "Только CC BY (любой версии). CC BY-SA, NC и ND не принимаются. Атрибуция уходит в описание ролика; гейт ≥ 30%.",
  public_domain: "Общественное достояние. Укажите, откуда это известно; проверьте музыку внутри.",
};

export const RIGHTS_TYPES: readonly RightsType[] = [
  "own",
  "licensed_written",
  "licensed_platform",
  "cc_by",
  "public_domain",
];

export const ALLOWLIST_LABELS: Record<AllowlistStatus, string> = {
  not_needed: "Не нужен (своё видео)",
  requested: "Запрошен у правообладателя",
  allowlisted: "Канал в allowlist",
  unknown: "Неизвестно",
};

// ---------------------------------------------------------------- отбор и кандидаты

export const SELECTION_MODE_META: StatusMetaMap<SelectionMode> = {
  llm: { label: "LLM", variant: "machine", icon: Sparkles },
  mock: { label: "Заглушка LLM (mock)", variant: "negative", icon: TriangleAlert },
  heuristic: { label: "Эвристика без LLM", variant: "warning", icon: WandSparkles },
};

export const SELECTOR_LABELS: Record<SelectorChoice, string> = {
  auto: "Авто: LLM, если есть ключ, иначе эвристика",
  llm: "Только LLM (платно, через бюджет)",
  heuristic: "Эвристика без LLM (бесплатно)",
};

export const CANDIDATE_STATUS_META: StatusMetaMap<CandidateStatus> = {
  proposed: { label: "Предложен", variant: "neutral", icon: CircleDashed },
  approved: { label: "Одобрен", variant: "human", icon: ThumbsUp },
  dismissed: { label: "Отброшен", variant: "neutral", icon: Ban, outline: true },
  used: { label: "В работе", variant: "machine", icon: Scissors },
};

export const CANDIDATE_ORIGIN_META: StatusMetaMap<CandidateOrigin> = {
  llm: { label: "LLM", variant: "machine", icon: Sparkles },
  mock: { label: "mock", variant: "negative", icon: TriangleAlert },
  heuristic: { label: "эвристика", variant: "warning", icon: WandSparkles },
  manual: { label: "вручную", variant: "accent", icon: Hand },
};

export const CANDIDATE_FLAG_LABELS: Record<CandidateFlag, string> = {
  medical_claim: "Медицинские утверждения",
  guarantee_claim: "Гарантии результата",
  financial_advice: "Финансовые советы",
  legal_advice: "Юридические советы",
  profanity: "Мат",
  politics: "Политика",
  violence: "Насилие",
  sexual: "Сексуальный контент",
  hate: "Язык вражды",
  dangerous_act: "Опасные действия",
  personal_data: "Личные данные",
  advertising: "Реклама",
  forbidden_phrase: "Запрещённая фраза ниши",
};

/** Блокируют кандидата до явной разблокировки оператором (`BLOCKING_CANDIDATE_FLAGS`). */
export const BLOCKING_FLAGS: ReadonlySet<CandidateFlag> = new Set([
  "medical_claim",
  "guarantee_claim",
  "forbidden_phrase",
]);

export const HOOK_TYPE_LABELS: Record<HookType, string> = {
  question: "вопрос",
  bold_claim: "смелое заявление",
  surprising_fact: "неожиданный факт",
  story: "история",
  contrarian: "против течения",
  how_to: "как сделать",
  mistake_warning: "предупреждение об ошибке",
  direct_address: "обращение к зрителю",
  stakes_escalation: "нарастание ставок",
  emotional: "эмоция",
  humor: "юмор",
  other: "другое",
};

export const SCORE_PART_LABELS: Record<string, string> = {
  llm: "Модель",
  feat: "Речь",
  len_fit: "Длина",
  trend: "Тренд",
};

export const LLM_SCORE_LABELS: Record<keyof Schemas["LlmScores"], string> = {
  hook: "Хук",
  standalone: "Понятен без контекста",
  payoff: "Развязка",
  emotion: "Эмоция",
  niche_fit: "В нише",
  novelty: "Новизна",
};

// ---------------------------------------------------------------- авторский слой

export const SEGMENT_LABELS: Record<AuthorVoiceSegment, string> = {
  cold_open: "Хук (cold open)",
  insert_0: "Вставка 1",
  insert_1: "Вставка 2",
  outro: "Вывод (outro)",
};

export const VOICE_KIND_LABELS: Record<AuthorVoiceKind, string> = {
  tts: "синтез",
  operator: "мой голос",
  mock: "заглушка TTS",
};

export const VOICE_STATUS_META: StatusMetaMap<AuthorVoiceStatus> = {
  missing: { label: "Нет записи", variant: "neutral", icon: CircleDashed },
  processing: { label: "Обработка", variant: "machine", icon: LoaderCircle },
  ready: { label: "Готово", variant: "positive", icon: CircleCheck },
  failed: { label: "Ошибка", variant: "negative", icon: CircleX },
};

export const VOICE_TIMING_LABELS: Record<AuthorVoiceTiming, string> = {
  tts: "тайминги синтеза",
  asr: "тайминги по распознаванию",
  estimated: "тайминги оценены",
  mock: "тайминги заглушки",
};

export const AUTHOR_MODE_META: StatusMetaMap<AuthorScriptMode> = {
  llm: { label: "Черновик LLM", variant: "machine", icon: Sparkles },
  mock: { label: "Заглушка LLM", variant: "negative", icon: TriangleAlert },
  heuristic: { label: "Шаблон без LLM", variant: "warning", icon: WandSparkles },
  operator: { label: "Текст оператора", variant: "accent", icon: Hand },
};

export const REFRAME_LABELS: Record<ReframeMode, string> = {
  auto: "Авто: лица и склейки",
  track: "Следить за лицом",
  center: "Центр кадра",
  fit_blur: "Целиком с размытым фоном",
};

export const INSERT_MODE_LABELS: Record<AuthorInsertMode, string> = {
  freeze_zoom: "Стоп-кадр с наездом",
  over_ducked: "Поверх приглушённого звука",
};

export const CTA_STYLE_LABELS: Record<AuthorCtaStyle, string> = {
  question: "Вопрос зрителю",
  loop_back: "Петля к началу",
  offer: "Оффер продукта",
};

// ---------------------------------------------------------------- оригинальность (OWNER-DECISIONS п. 5)

export const VERDICT_META: StatusMetaMap<OriginalityVerdict> = {
  pass: { label: "Проходит", variant: "positive", icon: CircleCheck },
  warn: { label: "С замечаниями", variant: "warning", icon: TriangleAlert },
  fail: { label: "Не проходит", variant: "negative", icon: OctagonAlert },
};

export const CHECK_STATUS_META: StatusMetaMap<OriginalityCheckStatus> = {
  pass: { label: "ок", variant: "positive", icon: CircleCheck },
  warn: { label: "внимание", variant: "warning", icon: TriangleAlert },
  fail: { label: "стоп", variant: "negative", icon: CircleX },
  info: { label: "справка", variant: "neutral", icon: Info },
  skip: { label: "не проверялось", variant: "neutral", icon: FileQuestionMark },
};

export const UNKNOWN_STATUS_ICON: LucideIcon = CircleDashed;
