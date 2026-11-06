"use client";

import { keepPreviousData, type QueryClient, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type Schemas, unwrap } from "@/lib/api/client";
import { queryKeys } from "@/lib/queries/keys";

/**
 * Пакеты «К заливке» (docs/ARCHITECTURE.md §5.2, §7.3 `/packages`). Публикация ручная: сервер
 * готовит файл, тексты, чек-лист и код, оператор заливает сам и вставляет ссылку
 * (lib/queries/publications.ts).
 */
export type UploadPackage = Schemas["UploadPackage"];
export type PackageStatus = Schemas["PackageStatus"];
export type PackagePatch = Schemas["PackagePatch"];
export type ChecklistItem = Schemas["ChecklistItem"];
export type ChecklistCode = Schemas["ChecklistCode"];
export type AutopublishState = Schemas["AutopublishState"];
export type AutopublishRequest = Schemas["AutopublishRequest"];
export type AutopublishQueueItem = Schemas["AutopublishQueueItem"];

export interface PackageFilters {
  statuses: PackageStatus[];
  channelId?: string | null;
}

/** Ключи §7.4: `['packages', …]` — списки, `['package', id]` — карточка. */
export const packageKeys = {
  all: ["packages"] as const,
  list: (filters: PackageFilters) => ["packages", "list", filters] as const,
  /** Счётчики вкладок (свой ключ: `queryKeys.packagesCounts` держит SSE и сайдбар). */
  totals: (channelId: string | null) => ["packages", "totals", channelId] as const,
  detail: (id: number) => ["package", id] as const,
  byJob: (jobId: number) => ["package", "job", jobId] as const,
  /** Очередь автозалива (`GET /autopublish/queue`). */
  autopublishQueue: (filters: AutopublishQueueFilters) => ["autopublish", "queue", filters] as const,
};

export interface AutopublishQueueFilters {
  channelId?: string | null;
  states?: AutopublishState[] | null;
}

/** Живые пакеты (ready + downloaded) и опубликованные; сервер отдаёт до 200 за раз. */
export function usePackages(filters: PackageFilters) {
  return useQuery({
    queryKey: packageKeys.list(filters),
    queryFn: ({ signal }) =>
      unwrap(
        api.GET("/api/v1/packages", {
          params: {
            query: {
              status: filters.statuses.length ? filters.statuses : undefined,
              channel_id: filters.channelId ?? undefined,
              limit: 200,
            },
          },
          signal,
        }),
      ),
    placeholderData: keepPreviousData,
  });
}

export interface PackageTotals {
  ready: number;
  downloaded: number;
}

/**
 * Сколько пакетов ждёт заливки и ссылки (бейджи вкладок). Без фильтра канала результат кладётся
 * в `queryKeys.packagesCounts` — тот же кеш, что наполняет SSE `packages` для бейджа сайдбара.
 */
export function usePackageTotals(channelId: string | null = null) {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: packageKeys.totals(channelId),
    queryFn: async ({ signal }): Promise<PackageTotals> => {
      const count = (status: PackageStatus) =>
        unwrap(
          api.GET("/api/v1/packages", {
            params: { query: { status: [status], channel_id: channelId ?? undefined, limit: 1 } },
            signal,
          }),
        ).then((page) => page.total);
      const [ready, downloaded] = await Promise.all([count("ready"), count("downloaded")]);
      const totals = { ready, downloaded };
      if (!channelId) queryClient.setQueryData(queryKeys.packagesCounts, totals);
      return totals;
    },
    staleTime: 30_000,
  });
}

export function usePackage(id: number | null, initial?: UploadPackage) {
  return useQuery({
    queryKey: packageKeys.detail(id ?? 0),
    queryFn: ({ signal }) =>
      unwrap(api.GET("/api/v1/packages/{package_id}", { params: { path: { package_id: id ?? 0 } }, signal })),
    enabled: id !== null && id > 0,
    placeholderData: initial,
    // Проверка принадлежности каналу (YouTube — oEmbed) и автозалив идут в фоне: пока идут, опрашиваем.
    refetchInterval: (query) =>
      query.state.data?.publication?.verify_status === "pending" || query.state.data?.autopublish?.state === "uploading"
        ? 10_000
        : false,
  });
}

/** Свежая версия пакета: карточка сразу, списки и счётчики — перечитать. */
export function storePackage(queryClient: QueryClient, pkg: UploadPackage) {
  queryClient.setQueryData(packageKeys.detail(pkg.id), pkg);
  void queryClient.invalidateQueries({ queryKey: packageKeys.all });
}

/** Автосохранение текстов (`texts_mode` → operator). Ошибки полей экран показывает сам. */
export function useUpdatePackageTexts(id: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: PackagePatch) =>
      unwrap(api.PATCH("/api/v1/packages/{package_id}", { params: { path: { package_id: id } }, body })),
    meta: { silentError: true },
    onSuccess: (pkg) => {
      // Список не перечитываем на каждое автосохранение: только карточку и строку в кеше списков.
      queryClient.setQueryData(packageKeys.detail(pkg.id), pkg);
      queryClient.setQueriesData<Schemas["UploadPackagePage"]>({ queryKey: ["packages", "list"] }, (page) =>
        page ? { ...page, items: page.items.map((item) => (item.id === pkg.id ? pkg : item)) } : page,
      );
    },
  });
}

export function useSetChecklistItem(id: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { code: ChecklistCode; done: boolean }) =>
      unwrap(api.PATCH("/api/v1/packages/{package_id}/checklist", { params: { path: { package_id: id } }, body })),
    onMutate: async ({ code, done }) => {
      const key = packageKeys.detail(id);
      await queryClient.cancelQueries({ queryKey: key });
      const previous = queryClient.getQueryData<UploadPackage>(key);
      if (previous) {
        queryClient.setQueryData<UploadPackage>(key, {
          ...previous,
          checklist: previous.checklist.map((item) =>
            item.code === code ? { ...item, done, done_at: done ? new Date().toISOString() : null } : item,
          ),
        });
      }
      return { previous };
    },
    onError: (_error, _vars, context) => {
      if (context?.previous) queryClient.setQueryData(packageKeys.detail(id), context.previous);
    },
    onSuccess: (pkg) => storePackage(queryClient, pkg),
  });
}

/** «Залил, ссылку вставлю позже» → status downloaded, `marked_uploaded_at`. */
export function useMarkUploaded(id: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/packages/{package_id}/mark-uploaded", { params: { path: { package_id: id } } })),
    onSuccess: (pkg) => storePackage(queryClient, pkg),
  });
}

/** Пересборка пакета ролика: `regenerate_texts` заново пишет тексты (LLM или шаблон). */
export function useRebuildPackage(jobId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["PackageRebuild"]) =>
      unwrap(api.POST("/api/v1/jobs/{job_id}/package", { params: { path: { job_id: jobId } }, body })),
    meta: { silentError: true },
    onSuccess: (pkg) => storePackage(queryClient, pkg),
  });
}

/**
 * Скачивание файла (`GET /packages/{id}/video|zip`) сервер отмечает сам: ready → downloaded и пункт
 * `file_downloaded`. Браузер качает в фоне, поэтому карточку перечитываем чуть позже.
 */
export function refreshAfterDownload(queryClient: QueryClient, id: number) {
  for (const delay of [1500, 6000]) {
    window.setTimeout(() => {
      void queryClient.invalidateQueries({ queryKey: packageKeys.detail(id) });
      void queryClient.invalidateQueries({ queryKey: packageKeys.all });
    }, delay);
  }
}

// ---------------------------------------------------------------- автозалив через API площадки (0019)

/**
 * Поставить пакет в автозалив: `{now: false}` без времени — ближайший свободный слот канала, `now` — сразу,
 * `at` — на это время (ISO с поясом). 409 `autopublish_blocked` с `details.problems` — что мешает.
 */
export function useScheduleAutopublish(id: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: AutopublishRequest) =>
      unwrap(api.POST("/api/v1/packages/{package_id}/autopublish", { params: { path: { package_id: id } }, body })),
    meta: { silentError: true },
    onSuccess: (pkg) => {
      storePackage(queryClient, pkg);
      void queryClient.invalidateQueries({ queryKey: ["autopublish"] });
    },
  });
}

/** Снять с очереди автозалива (кроме идущей заливки). */
export function useCancelAutopublish(id: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(api.DELETE("/api/v1/packages/{package_id}/autopublish", { params: { path: { package_id: id } } })),
    meta: { silentError: true },
    onSuccess: (pkg) => {
      storePackage(queryClient, pkg);
      void queryClient.invalidateQueries({ queryKey: ["autopublish"] });
    },
  });
}

/** Очередь «Запланировано»: заливаются, ошибки, в слотах, залитые. Пока что-то в работе — опрос раз в 15 с. */
export function useAutopublishQueue(filters: AutopublishQueueFilters = {}) {
  return useQuery({
    queryKey: packageKeys.autopublishQueue(filters),
    queryFn: ({ signal }) =>
      unwrap(
        api.GET("/api/v1/autopublish/queue", {
          params: {
            query: {
              channel_id: filters.channelId ?? undefined,
              state: filters.states?.length ? filters.states : undefined,
              limit: 200,
            },
          },
          signal,
        }),
      ),
    placeholderData: keepPreviousData,
    refetchInterval: (query) =>
      query.state.data?.items.some((item) => item.state === "uploading" || item.state === "scheduled") ? 15_000 : false,
  });
}
