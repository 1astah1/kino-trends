/**
 * Подписи и оформление статусов роликов и задач (§7.4: статус всегда цветом **и** иконкой с подписью;
 * §7.6: янтарь — ход оператора, фиолет — работает машина). Подписи повторяют `LABELS` из
 * src/autoshorts/domain/enums.py (источник истины для остальных экранов — `GET /meta/enums`).
 */
import {
  Ban,
  CircleCheck,
  CircleDashed,
  CircleX,
  Clock,
  Eye,
  FilePen,
  Info,
  LoaderCircle,
  OctagonAlert,
  RotateCcw,
  Send,
  ShieldAlert,
  ThumbsUp,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";

import type { StatusMetaMap } from "@/components/ui/status-badge";
import type { Schemas } from "@/lib/api/client";

type JobStatus = Schemas["JobStatus"];
type JobStage = Schemas["JobStage"];
type JobKind = Schemas["JobKind"];
type TaskStatus = Schemas["TaskStatus"];
type Platform = Schemas["Platform"];
type QcCheck = Schemas["QcCheck"];
type ReviewFlagCode = Schemas["ReviewFlagCode"];
type WarningSeverity = Schemas["WarningSeverity"];
type BannerLayerKind = Schemas["BannerLayerKind"];

export const JOB_STATUS_META: StatusMetaMap<JobStatus> = {
  draft: { label: "Черновик", variant: "neutral", icon: FilePen },
  queued: { label: "В очереди", variant: "machine", icon: Clock, outline: true },
  processing: { label: "В работе", variant: "machine", icon: LoaderCircle },
  qc_failed: { label: "Не прошёл QC", variant: "negative", icon: ShieldAlert },
  pending_review: { label: "Ждёт приёмки", variant: "human", icon: Eye },
  rework: { label: "На доработке", variant: "warning", icon: RotateCcw },
  approved: { label: "Одобрен", variant: "positive", icon: ThumbsUp },
  published: { label: "Опубликован", variant: "positive", icon: Send, outline: true },
  rejected: { label: "Отклонён", variant: "negative", icon: CircleX, outline: true },
  failed: { label: "Сбой", variant: "negative", icon: OctagonAlert },
  cancelled: { label: "Отменён", variant: "neutral", icon: Ban, outline: true },
};

export const TASK_STATUS_META: StatusMetaMap<TaskStatus> = {
  queued: { label: "В очереди", variant: "neutral", icon: Clock },
  running: { label: "Выполняется", variant: "machine", icon: LoaderCircle },
  succeeded: { label: "Готово", variant: "positive", icon: CircleCheck },
  failed: { label: "Ошибка", variant: "negative", icon: CircleX },
  cancelled: { label: "Отменено", variant: "neutral", icon: Ban },
};

/** Стадия, которой ещё не было (для таймлайна). */
export const STAGE_PENDING_ICON: LucideIcon = CircleDashed;

export const JOB_KIND_LABELS: Record<JobKind, string> = { generate: "Генерация", clip: "Нарезка" };

export const PLATFORM_LABELS: Record<Platform, string> = { youtube: "YouTube", vk: "VK" };

export const JOB_STAGE_LABELS: Record<JobStage, string> = {
  script: "Сценарий",
  voice: "Озвучка",
  visuals: "Визуал",
  clip_fetch: "Загрузка секции",
  clip_analyze: "Анализ кадра",
  author_script: "Авторский текст",
  author_voice: "Авторский голос",
  render: "Рендер",
  qc: "QC",
  package: "Пакет",
};

export const QC_CHECK_LABELS: Record<QcCheck, string> = {
  silence: "Тишина",
  clipping: "Клиппинг",
  loudness: "Громкость",
  black_frames: "Чёрные кадры",
  frozen_frames: "Зависший кадр",
  duration: "Длительность",
  subtitle_sync: "Синхрон субтитров",
  author_share: "Доля авторского слоя",
  banner_face_overlap: "Баннер на лице",
  foreign_watermark: "Чужой водяной знак",
  library_similarity: "Похож на свой ролик",
  forbidden_phrase: "Запрещённая фраза",
};

export const REVIEW_FLAG_LABELS: Record<ReviewFlagCode, string> = {
  forbidden_phrase: "Запрещённая фраза",
  duration_dead_zone: "Длительность в мёртвой зоне",
  banner_in_dead_zone: "Баннер в мёртвой зоне",
  library_similarity: "Похож на свой ролик",
  tts_mock: "Озвучка-заглушка",
  author_share_low: "Мало авторского слоя",
  foreign_watermark: "Чужой водяной знак",
};

export const SEVERITY_META: Record<
  WarningSeverity,
  { label: string; icon: LucideIcon; text: string; bg: string; variant: "accent" | "warning" | "negative" }
> = {
  info: { label: "Инфо", icon: Info, text: "text-accent", bg: "bg-accent/10", variant: "accent" },
  warn: { label: "Внимание", icon: TriangleAlert, text: "text-warning", bg: "bg-warning/10", variant: "warning" },
  error: { label: "Ошибка", icon: OctagonAlert, text: "text-negative", bg: "bg-negative/10", variant: "negative" },
};

export const BANNER_LAYER_KIND_LABELS: Record<BannerLayerKind, string> = {
  creative: "Креатив",
  text_code: "Код ролика",
  text_erid: "Реклама · erid",
  micro_brand: "Микробренд",
  fallback_plate: "Запасная плашка",
};

export function jobStageLabel(stage: string | null | undefined): string {
  if (!stage) return "—";
  return JOB_STAGE_LABELS[stage as JobStage] ?? stage;
}

export function severityRank(severity: WarningSeverity): number {
  return severity === "error" ? 2 : severity === "warn" ? 1 : 0;
}

/** Стадии, из которых нарезка и генерация идут (порядок как в domain/enums.py). */
export const PROCESSING_STAGE_ORDER: readonly JobStage[] = [
  "script",
  "voice",
  "visuals",
  "clip_fetch",
  "clip_analyze",
  "author_script",
  "author_voice",
  "render",
  "qc",
];
