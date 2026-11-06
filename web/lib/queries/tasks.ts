"use client";

/**
 * Задачи очереди (docs/ARCHITECTURE.md §5.2 `/tasks`, §6.1, §7.4 «лог задачи открывается отдельным
 * стримом только при видимой вкладке»). Владелец — M2-D1.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, type Schemas, unwrap } from "@/lib/api/client";
import { useLiveRefetchInterval } from "@/lib/hooks/use-live-refetch";

import { queryKeys } from "./keys";

export type Task = Schemas["Task"];
export type TaskDetail = Schemas["TaskDetail"];
export type TaskStatus = Schemas["TaskStatus"];

export interface TasksFilters {
  status?: TaskStatus[] | null;
  kind?: string[] | null;
  resource_class?: Schemas["ResourceClass"] | null;
  job_id?: number | null;
  source_id?: number | null;
  limit?: number;
  offset?: number;
}

export const taskKeys = {
  all: queryKeys.tasks,
  list: (filters: TasksFilters) => [...queryKeys.tasks, "list", filters] as const,
  detail: (id: number) => queryKeys.task(id),
};

export const TASK_ACTIVE_STATUSES: readonly TaskStatus[] = ["queued", "running"];

export function isTaskActive(status: TaskStatus | null | undefined): boolean {
  return status === "queued" || status === "running";
}

/**
 * `GET /tasks` — задачи с фильтрами (ролик, источник, класс ресурса, статус). По умолчанию при живом SSE
 * не опрашивает (события `task_progress` инвалидируют кеш); `whenLive` — интервал опроса для экранов,
 * которым нужна гарантированная свежесть (очередь).
 */
export function useTasks(
  filters: TasksFilters,
  { enabled = true, whenLive = false }: { enabled?: boolean; whenLive?: number | false } = {},
) {
  const refetchInterval = useLiveRefetchInterval(whenLive);
  return useQuery({
    queryKey: taskKeys.list(filters),
    queryFn: ({ signal }) =>
      unwrap(
        api.GET("/api/v1/tasks", {
          params: {
            query: {
              status: filters.status?.length ? filters.status : undefined,
              kind: filters.kind?.length ? filters.kind : undefined,
              resource_class: filters.resource_class ?? undefined,
              job_id: filters.job_id ?? undefined,
              source_id: filters.source_id ?? undefined,
              limit: filters.limit ?? 50,
              offset: filters.offset ?? 0,
            },
          },
          signal,
        }),
      ),
    enabled,
    refetchInterval,
  });
}

/** Задача и последние 200 строк лога. Пока задача идёт — опрос раз в 5 с (стрим может быть закрыт). */
export function useTask(id: number | null | undefined) {
  const liveInterval = useLiveRefetchInterval(false);
  return useQuery({
    queryKey: taskKeys.detail(id ?? 0),
    queryFn: ({ signal }) =>
      unwrap(api.GET("/api/v1/tasks/{task_id}", { params: { path: { task_id: id as number } }, signal })),
    enabled: typeof id === "number" && id > 0,
    refetchInterval: (query) => (isTaskActive(query.state.data?.task.status) ? 5_000 : liveInterval),
  });
}

function useSettleTask() {
  const queryClient = useQueryClient();
  return (task: Task) => {
    queryClient.setQueryData<TaskDetail>(taskKeys.detail(task.id), (old) => (old ? { ...old, task } : old));
    void queryClient.invalidateQueries({ queryKey: taskKeys.all });
    if (task.job_id) {
      void queryClient.invalidateQueries({ queryKey: queryKeys.job(task.job_id) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.jobs });
    }
  };
}

export function useRetryTask() {
  const settle = useSettleTask();
  return useMutation({
    mutationFn: (taskId: number) =>
      unwrap(api.POST("/api/v1/tasks/{task_id}/retry", { params: { path: { task_id: taskId } } })),
    onSuccess: settle,
  });
}

export function useCancelTask() {
  const settle = useSettleTask();
  return useMutation({
    mutationFn: (taskId: number) =>
      unwrap(api.POST("/api/v1/tasks/{task_id}/cancel", { params: { path: { task_id: taskId } } })),
    onSuccess: settle,
  });
}

// ---------------------------------------------------------------- живой лог

export const TASK_LOG_MAX_LINES = 2000;

export type TaskLogState = "idle" | "connecting" | "streaming" | "ended" | "error";

function usePageVisible(): boolean {
  const [visible, setVisible] = useState(() => typeof document === "undefined" || !document.hidden);
  useEffect(() => {
    const onChange = () => setVisible(!document.hidden);
    document.addEventListener("visibilitychange", onChange);
    return () => document.removeEventListener("visibilitychange", onChange);
  }, []);
  return visible;
}

/**
 * Живой лог `/tasks/{id}/log/stream` (SSE: `data:` — строка лога, `event: end` — задача завершена).
 * Сервер начинает с хвоста в 200 строк, поэтому при переподключении список строк начинается заново.
 * Стрим открыт, только пока вкладка видима и `enabled`.
 */
export function useTaskLogStream(taskId: number | null | undefined, enabled: boolean) {
  const visible = usePageVisible();
  const active = enabled && visible && typeof taskId === "number" && taskId > 0;
  const [lines, setLines] = useState<string[]>([]);
  const [state, setState] = useState<TaskLogState>("idle");

  useEffect(() => {
    if (!active || typeof EventSource === "undefined") return;
    let closed = false;
    const source = new EventSource(`/api/v1/tasks/${taskId}/log/stream`, { withCredentials: true });
    // Смена задачи или переподключение: начинаем с чистого листа (сервер повторит хвост).
    const reset = () => {
      if (!closed) {
        setLines([]);
        setState("streaming");
      }
    };
    queueMicrotask(() => {
      if (!closed) setState("connecting");
    });
    source.onopen = reset;
    source.onmessage = (event: MessageEvent<string>) => {
      if (closed) return;
      const line = event.data ?? "";
      setLines((prev) => {
        const next =
          prev.length >= TASK_LOG_MAX_LINES ? prev.slice(prev.length - TASK_LOG_MAX_LINES + 1) : prev.slice();
        next.push(line);
        return next;
      });
    };
    source.addEventListener("end", () => {
      closed = true;
      source.close();
      setState("ended");
    });
    source.onerror = () => {
      if (closed) return;
      if (source.readyState === EventSource.CLOSED) setState("error");
    };
    return () => {
      closed = true;
      source.close();
    };
  }, [active, taskId]);

  return { lines, state: active ? state : ("idle" as TaskLogState), visible };
}
