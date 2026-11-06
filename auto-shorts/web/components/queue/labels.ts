/**
 * Подписи экрана «Очередь»: виды задач auto-shorts (`TaskKind` из src/autoshorts/domain/enums.py — в
 * `LABELS` домена их нет, поэтому словарь живёт здесь), фоновые работы и прогоны Kino-trends, группы
 * тумблеров автоматики. Неизвестный вид задачи показывается как есть: `tasks.kind` — свободный TEXT.
 */
import { CircleCheck, CircleX, LoaderCircle } from "lucide-react";

import type { StatusMetaMap } from "@/components/ui/status-badge";
import type { Task } from "@/lib/queries/tasks";
import { stageLabel } from "@/lib/status-meta";

// ---------------------------------------------------------------- задачи auto-shorts

export const TASK_KIND_LABELS: Record<string, string> = {
  job_stage: "Стадия ролика",
  package_build: "Сборка пакета",
  source_download: "Загрузка источника",
  source_prepare: "Подготовка источника",
  source_transcribe_chunk: "Расшифровка (локально)",
  source_transcribe_cloud: "Расшифровка (облако)",
  source_select: "Отбор секций",
  source_scenes: "Разбор сцен",
  competitor_analyze: "Разбор конкурента",
  voice_sample: "Пример голоса",
  metrics_poll: "Опрос метрик",
  competitor_discovery: "Поиск конкурентов",
  competitor_stats_poll: "Статистика конкурентов",
  outlier_compute: "Расчёт выбросов",
  yt_analytics_pull: "YouTube Analytics",
  channel_stats: "Статистика каналов",
  stats_rollup: "Свёртка статистики",
  cleanup: "Уборка медиа",
  alerts_eval: "Проверка тревог",
  task_reaper: "Сборщик зависших задач",
  baselines_refresh: "Обновление базовых линий",
  brief_field_stats: "Статистика полей брифа",
  rss_health_check: "Проверка RSS",
  autopublish_tick: "Шаг автозалива",
};

export function taskKindLabel(kind: string | null | undefined): string {
  if (!kind) return "—";
  return TASK_KIND_LABELS[kind] ?? kind;
}

/** Что делает задача, по-русски: для стадии ролика — стадия и ролик, для остальных — вид и объект. */
export function taskTitle(task: Pick<Task, "kind" | "stage" | "job_id" | "source_id">): string {
  if (task.kind === "job_stage") {
    return task.job_id ? `${stageLabel(task.stage)} · ролик #${task.job_id}` : stageLabel(task.stage);
  }
  const base = taskKindLabel(task.kind);
  if (task.source_id) return `${base} · источник #${task.source_id}`;
  if (task.job_id) return `${base} · ролик #${task.job_id}`;
  return base;
}

// ---------------------------------------------------------------- фон Kino-trends

export const TRENDS_JOB_LABELS: Record<string, string> = {
  dict_import: "Импорт словаря",
  recognize: "Опознание фильмов",
  neuro: "Нейро-разбор",
  sources_top: "Поиск исходников для топа",
  popular: "Чарты популярных видео и разбор",
};

export function trendsJobLabel(name: string): string {
  return TRENDS_JOB_LABELS[name] ?? name;
}

export const RUN_TRIGGER_LABELS: Record<string, string> = {
  manual: "вручную",
  schedule: "по расписанию",
};

export function runTriggerLabel(trigger: string | null | undefined): string {
  if (!trigger) return "—";
  return RUN_TRIGGER_LABELS[trigger] ?? trigger;
}

export type RunStatus = "running" | "ok" | "error";

export const RUN_STATUS_META: StatusMetaMap<RunStatus> = {
  running: { label: "Идёт", variant: "machine", icon: LoaderCircle },
  ok: { label: "Готово", variant: "positive", icon: CircleCheck },
  error: { label: "Ошибка", variant: "negative", icon: CircleX },
};

/** Состояния конвейера Kino-trends, в которых работа идёт прямо сейчас (для вкладки «Сейчас»). */
export const PIPELINE_NOW_STATES: ReadonlySet<string> = new Set(["file_pending", "sent", "cutting", "publishing"]);

/** Состояния конвейера, которые считаем сбоем (вкладка «Сбои»). */
export const PIPELINE_FAILED_STATES: ReadonlySet<string> = new Set(["failed", "qc_failed"]);

/** Прогресс Kino-trends — свободный текст «120/560 что-то»: вытаскиваем доли для полоски. */
export function parseProgress(progress: string | number | null | undefined): { pct: number | null; text: string } {
  if (progress === null || progress === undefined || progress === "") return { pct: null, text: "" };
  if (typeof progress === "number") return { pct: clampPct(progress), text: `${Math.round(progress)}%` };
  const match = /^\s*(\d+)\s*\/\s*(\d+)/.exec(progress);
  if (match) {
    const done = Number(match[1]);
    const total = Number(match[2]);
    return { pct: total > 0 ? clampPct((done / total) * 100) : null, text: progress };
  }
  return { pct: null, text: progress };
}

export function clampPct(value: number | null | undefined): number | null {
  if (value === null || value === undefined || Number.isNaN(value)) return null;
  return Math.max(0, Math.min(100, Math.round(value)));
}

// ---------------------------------------------------------------- тумблеры автоматики

export const SETTING_GROUPS = ["collect", "pipeline", "publish", "rights"] as const;
export type SettingGroup = (typeof SETTING_GROUPS)[number];

export const SETTING_GROUP_LABELS: Record<SettingGroup, { title: string; hint: string }> = {
  collect: { title: "Сбор трендов", hint: "Как часто Kino-trends сам собирает чарты и что делает с находками." },
  pipeline: { title: "Конвейер", hint: "Фоновый цикл, который отдаёт исходники в auto-shorts и следит за клипами." },
  publish: { title: "Приёмка и залив", hint: "Что происходит с готовыми клипами без вашего участия." },
  rights: { title: "Авторские права", hint: "Насколько строго фильтровать произведения по правам." },
};

export const RIGHTS_POLICY_LABELS: Record<string, string> = {
  strict: "строго — только «проходит»",
  balanced: "сбалансированно — и «с оговорками»",
  off: "выключено — права лишь помечаются",
};

export function settingValueText(
  kind: string,
  value: boolean | number | string | null | undefined,
  unit?: string | null,
): string {
  if (value === null || value === undefined) return "—";
  if (kind === "bool") return value ? "включено" : "выключено";
  if (kind === "choice") return RIGHTS_POLICY_LABELS[String(value)] ?? String(value);
  return unit ? `${value} ${unit}` : String(value);
}

// ---------------------------------------------------------------- время

/** Сколько идёт: «12 с», «3 мин 05 с», «1 ч 12 мин», «2 д 3 ч». `to` — для завершённого. */
export function formatElapsed(from: string | null | undefined, now: number, to?: string | null): string {
  if (!from) return "—";
  const start = Date.parse(from);
  if (Number.isNaN(start)) return "—";
  const end = to ? Date.parse(to) : now;
  if (Number.isNaN(end)) return "—";
  return formatSeconds((end - start) / 1000);
}

export function formatSeconds(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const sec = Math.max(0, Math.round(value));
  if (sec < 60) return `${sec} с`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} мин ${String(sec % 60).padStart(2, "0")} с`;
  const hours = Math.floor(min / 60);
  if (hours < 24) return `${hours} ч ${String(min % 60).padStart(2, "0")} мин`;
  const days = Math.floor(hours / 24);
  return `${days} д ${hours % 24} ч`;
}

/** Срок до момента: «через 12 мин», «через 2 ч 05 мин», «уже пора». */
export function formatUntil(value: string | null | undefined, now: number): string {
  if (!value) return "—";
  const at = Date.parse(value);
  if (Number.isNaN(at)) return "—";
  if (at <= now) return "уже пора";
  return `через ${formatSeconds((at - now) / 1000)}`;
}
