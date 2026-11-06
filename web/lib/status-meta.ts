/**
 * Подписи и оформление статусов (паттерн retentionos/apps/web/lib/status-meta.ts).
 * Русские подписи повторяют `LABELS` из src/autoshorts/domain/enums.py; источник истины для
 * экранов — `GET /meta/enums`, эти карты нужны оболочке, чтобы шапка рисовалась без лишнего запроса.
 * Статус всегда цветом **и** иконкой с подписью (§7.4).
 */
import { CircleAlert, Crown, Eye, Info, type LucideIcon, OctagonAlert, TriangleAlert, Wrench } from "lucide-react";

import type { StatusMetaMap } from "@/components/ui/status-badge";
import type { Schemas } from "@/lib/api/client";

export type Role = Schemas["Role"];
export type JobStage = Schemas["JobStage"];
export type AlertSeverity = Schemas["AlertSeverity"];
export type DeferReason = Schemas["DeferReason"];
export type ResourceClass = Schemas["ResourceClass"];

export const ROLE_META: StatusMetaMap<Role> = {
  admin: { label: "Владелец", variant: "human", icon: Crown },
  operator: { label: "Оператор", variant: "accent", icon: Wrench },
  viewer: { label: "Наблюдатель", variant: "neutral", icon: Eye },
};

export const ROLE_OPTIONS: ReadonlyArray<{ value: Role; label: string; hint: string }> = [
  { value: "admin", label: "Владелец", hint: "всё, включая пользователей, секреты, провайдеры и бюджет" },
  { value: "operator", label: "Оператор", hint: "контент, приёмка, пакеты, публикации, каталог" },
  { value: "viewer", label: "Наблюдатель", hint: "только чтение, без секретов, настроек и пользователей" },
];

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

export const DEFER_REASON_LABELS: Record<DeferReason, string> = {
  disk_low: "Мало места на диске",
  load_high: "Высокая нагрузка",
  mem_low: "Мало памяти",
  budget_exhausted: "Бюджет исчерпан",
  quota_exhausted: "Квота API исчерпана",
};

export const RESOURCE_CLASS_LABELS: Record<ResourceClass, string> = {
  heavy: "Тяжёлая",
  light: "Лёгкая",
};

export const ALERT_SEVERITY_META: Record<
  AlertSeverity,
  { label: string; icon: LucideIcon; text: string; bg: string; rank: number }
> = {
  info: { label: "Инфо", icon: Info, text: "text-accent", bg: "bg-accent/15", rank: 0 },
  warning: { label: "Внимание", icon: TriangleAlert, text: "text-warning", bg: "bg-warning/15", rank: 1 },
  critical: { label: "Критично", icon: OctagonAlert, text: "text-negative", bg: "bg-negative/15", rank: 2 },
};

export const UNKNOWN_ICON = CircleAlert;

export function stageLabel(stage: string | null | undefined): string {
  if (!stage) return "—";
  return JOB_STAGE_LABELS[stage as JobStage] ?? stage;
}

export function deferReasonLabel(reason: string | null | undefined): string {
  if (!reason) return "—";
  return DEFER_REASON_LABELS[reason as DeferReason] ?? reason;
}
