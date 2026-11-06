"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api, type Schemas, unwrap } from "@/lib/api/client";
import { isApiError } from "@/lib/api/errors";

import { queryKeys } from "./keys";

/**
 * Обслуживание медиа (`POST /maintenance/cleanup-preview`, `POST /maintenance/cleanup`, admin).
 * Пока задачи `cleanup` на сервере нет, оба эндпоинта отвечают 501 `not_implemented` —
 * экран показывает «сервер пока не поддерживает», а не ошибку.
 */
export type CleanupPreview = Schemas["CleanupPreview"];
export type CleanupPreviewItem = Schemas["CleanupPreviewItem"];
export type TaskAccepted = Schemas["TaskAccepted"];

/** Категории оценки уборки (§8.2) → подпись. */
export const CLEANUP_CATEGORY_LABELS: Record<string, string> = {
  work_artifacts: "Рабочие файлы роликов",
  rejected_media: "Медиа отклонённых роликов",
  proxies: "Прокси источников",
  originals: "Оригиналы загрузок",
  tus_parts: "Недокачанные загрузки (tus)",
  task_logs: "Логи задач",
  tmp: "Временные файлы",
};

export function cleanupCategoryLabel(category: string): string {
  return CLEANUP_CATEGORY_LABELS[category] ?? category;
}

/** 501 от сервера: функция ещё не реализована. */
export function isNotImplemented(error: unknown): boolean {
  return isApiError(error) && (error.status === 501 || error.code === "not_implemented");
}

/** Оценка: сколько файлов и байт уйдёт по каждой категории. Ничего не удаляет. */
export function useCleanupPreview() {
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/maintenance/cleanup-preview")),
    meta: { silentError: true },
  });
}

/** Запуск уборки: 202 с `task_id`; ход виден в задачах и в `/system/status`. */
export function useRunCleanup() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/maintenance/cleanup")),
    meta: { silentError: true },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.systemStatus });
      void queryClient.invalidateQueries({ queryKey: queryKeys.tasks });
    },
  });
}
