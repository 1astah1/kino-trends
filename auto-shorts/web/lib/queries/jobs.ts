"use client";

/**
 * Ролики: список, лента стадий, деталь, таймлайн задач, история, сцены, действия и создание
 * (docs/ARCHITECTURE.md §5.2 «Ролики, приёмка, задачи», §6.2, §7.3 «Конвейер»). Владелец — M2-D1.
 *
 * Ключи (§7.4): всё про список — под `['jobs', …]`, всё про один ролик — под `['job', id, …]`.
 * SSE-мост (lib/hooks/use-live-events.ts) на `job_status`/`job_progress` инвалидирует `['jobs']`
 * и `['job', id]` — префиксы покрывают и деталь, и таймлайн, и историю.
 */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type Schemas, unwrap } from "@/lib/api/client";
import { useLiveRefetchInterval } from "@/lib/hooks/use-live-refetch";

import { queryKeys } from "./keys";

export type Job = Schemas["Job"];
export type JobRow = Schemas["JobRow"];
export type JobPage = Schemas["JobPage"];
export type JobStatus = Schemas["JobStatus"];
export type JobStage = Schemas["JobStage"];
export type JobKind = Schemas["JobKind"];
export type Platform = Schemas["Platform"];
export type StageCount = Schemas["StageCount"];
export type JobTaskItem = Schemas["JobTaskItem"];
export type JobHistoryItem = Schemas["JobHistoryItem"];
export type Scene = Schemas["Scene"];
export type SimilarJob = Schemas["SimilarJob"];
export type JobCreateGenerate = Schemas["JobCreateGenerate"];
export type JobCreateClip = Schemas["JobCreateClip"];
export type JobBatchCreate = Schemas["JobBatchCreate"];
export type JobCloneOverrides = Schemas["JobCloneOverrides"];
export type Channel = Schemas["Channel"];
export type Campaign = Schemas["Campaign"];
export type BannerVariant = Schemas["BannerVariant"];
export type Provider = Schemas["Provider"];

/** Белый список сортировки `GET /jobs` (services/jobs.py JOB_SORT_FIELDS). */
export const JOB_SORT_FIELDS = [
  "created_at",
  "updated_at",
  "priority",
  "duration_sec",
  "cost_usd",
  "status",
  "id",
] as const;
export type JobSortField = (typeof JOB_SORT_FIELDS)[number];

export interface JobsFilters {
  kind?: JobKind | null;
  status?: JobStatus[] | null;
  stage?: JobStage[] | null;
  channel_id?: string | null;
  q?: string | null;
  sort?: string | null;
  limit?: number;
  offset?: number;
}

export const JOBS_PAGE_SIZE = 50;

export const jobKeys = {
  all: queryKeys.jobs,
  list: (filters: JobsFilters) => [...queryKeys.jobs, "list", filters] as const,
  stageCounts: [...queryKeys.jobs, "stage-counts"] as const,
  detail: (id: number) => queryKeys.job(id),
  tasks: (id: number) => [...queryKeys.job(id), "tasks"] as const,
  history: (id: number) => [...queryKeys.job(id), "history"] as const,
  scenes: (id: number) => [...queryKeys.job(id), "scenes"] as const,
  similar: (id: number) => [...queryKeys.job(id), "similar"] as const,
  // Справочники формы создания и конвейера. Отдельные ключи, чтобы не делить кеш с экранами каталога
  // (M2-D2), у которых может быть другая форма данных под тем же адресом.
  channels: ["create-job", "channels"] as const,
  campaigns: ["create-job", "campaigns"] as const,
  variants: (campaignId: number) => ["create-job", "variants", campaignId] as const,
  providers: ["production", "providers"] as const,
};

function cleanFilters(filters: JobsFilters) {
  return {
    kind: filters.kind ?? undefined,
    status: filters.status?.length ? filters.status : undefined,
    stage: filters.stage?.length ? filters.stage : undefined,
    channel_id: filters.channel_id || undefined,
    q: filters.q?.trim() || undefined,
    sort: filters.sort || undefined,
    limit: filters.limit ?? JOBS_PAGE_SIZE,
    offset: filters.offset ?? 0,
  };
}

/** Список роликов (`GET /jobs`); предыдущая страница держится на экране, пока грузится новая. */
export function useJobs(filters: JobsFilters) {
  const refetchInterval = useLiveRefetchInterval(false);
  return useQuery({
    queryKey: jobKeys.list(filters),
    queryFn: ({ signal }) => unwrap(api.GET("/api/v1/jobs", { params: { query: cleanFilters(filters) }, signal })),
    placeholderData: keepPreviousData,
    refetchInterval,
  });
}

/** Лента стадий (`GET /jobs/stage-counts`). */
export function useStageCounts() {
  const refetchInterval = useLiveRefetchInterval(false);
  return useQuery({
    queryKey: jobKeys.stageCounts,
    queryFn: ({ signal }) => unwrap(api.GET("/api/v1/jobs/stage-counts", { signal })),
    select: (data) => data.items,
    refetchInterval,
  });
}

/** Ролик детально. `id = null` — запрос выключен. */
export function useJob(id: number | null | undefined) {
  const refetchInterval = useLiveRefetchInterval(false);
  return useQuery({
    queryKey: jobKeys.detail(id ?? 0),
    queryFn: ({ signal }) =>
      unwrap(api.GET("/api/v1/jobs/{job_id}", { params: { path: { job_id: id as number } }, signal })),
    enabled: typeof id === "number" && id > 0,
    refetchInterval,
  });
}

/** Таймлайн стадий: все задачи ролика с попытками, прогрессом и ценой. */
export function useJobTasks(id: number | null | undefined, { enabled = true }: { enabled?: boolean } = {}) {
  const refetchInterval = useLiveRefetchInterval(false);
  return useQuery({
    queryKey: jobKeys.tasks(id ?? 0),
    queryFn: ({ signal }) =>
      unwrap(api.GET("/api/v1/jobs/{job_id}/tasks", { params: { path: { job_id: id as number } }, signal })),
    enabled: enabled && typeof id === "number" && id > 0,
    select: (data) => data.items,
    refetchInterval,
  });
}

export function useJobHistory(id: number | null | undefined, { enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: jobKeys.history(id ?? 0),
    queryFn: ({ signal }) =>
      unwrap(api.GET("/api/v1/jobs/{job_id}/history", { params: { path: { job_id: id as number } }, signal })),
    enabled: enabled && typeof id === "number" && id > 0,
    select: (data) => data.items,
  });
}

export function useJobScenes(id: number | null | undefined, { enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: jobKeys.scenes(id ?? 0),
    queryFn: ({ signal }) =>
      unwrap(api.GET("/api/v1/jobs/{job_id}/scenes", { params: { path: { job_id: id as number } }, signal })),
    enabled: enabled && typeof id === "number" && id > 0,
    select: (data) => data.items,
  });
}

export function useSimilarJobs(id: number | null | undefined, { enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: jobKeys.similar(id ?? 0),
    queryFn: ({ signal }) =>
      unwrap(api.GET("/api/v1/jobs/{job_id}/similar", { params: { path: { job_id: id as number } }, signal })),
    enabled: enabled && typeof id === "number" && id > 0,
    select: (data) => data.items,
    staleTime: 60_000,
  });
}

// ---------------------------------------------------------------- действия

/** После действия над роликом: деталь из ответа, список и лента стадий — перечитать. */
function useJobMutationSettled() {
  const queryClient = useQueryClient();
  return (job: Job | undefined, jobId: number) => {
    if (job) queryClient.setQueryData(jobKeys.detail(job.id), job);
    void queryClient.invalidateQueries({ queryKey: jobKeys.all });
    void queryClient.invalidateQueries({ queryKey: queryKeys.job(jobId) });
  };
}

export function useStartJob() {
  const settle = useJobMutationSettled();
  return useMutation({
    mutationFn: (jobId: number) =>
      unwrap(api.POST("/api/v1/jobs/{job_id}/start", { params: { path: { job_id: jobId } } })),
    onSuccess: (job, jobId) => settle(job, jobId),
  });
}

export function useCancelJob() {
  const settle = useJobMutationSettled();
  return useMutation({
    mutationFn: (jobId: number) =>
      unwrap(api.POST("/api/v1/jobs/{job_id}/cancel", { params: { path: { job_id: jobId } } })),
    onSuccess: (job, jobId) => settle(job, jobId),
  });
}

export function useRetryJob() {
  const settle = useJobMutationSettled();
  return useMutation({
    mutationFn: ({ jobId, fromStage }: { jobId: number; fromStage?: JobStage | null }) =>
      unwrap(
        api.POST("/api/v1/jobs/{job_id}/retry", {
          params: { path: { job_id: jobId } },
          body: { from_stage: fromStage ?? null },
        }),
      ),
    onSuccess: (job, { jobId }) => settle(job, jobId),
  });
}

export function usePatchJob() {
  const settle = useJobMutationSettled();
  return useMutation({
    mutationFn: ({ jobId, body }: { jobId: number; body: Schemas["JobPatch"] }) =>
      unwrap(api.PATCH("/api/v1/jobs/{job_id}", { params: { path: { job_id: jobId } }, body })),
    meta: { silentError: true },
    onSuccess: (job, { jobId }) => settle(job, jobId),
  });
}

export function useCloneJob() {
  const settle = useJobMutationSettled();
  return useMutation({
    mutationFn: ({ jobId, overrides }: { jobId: number; overrides: JobCloneOverrides }) =>
      unwrap(api.POST("/api/v1/jobs/{job_id}/clone", { params: { path: { job_id: jobId } }, body: { overrides } })),
    meta: { silentError: true },
    onSuccess: (job, { jobId }) => settle(job, jobId),
  });
}

export function useAdaptJob() {
  const settle = useJobMutationSettled();
  return useMutation({
    mutationFn: ({ jobId, channelId }: { jobId: number; channelId: string }) =>
      unwrap(
        api.POST("/api/v1/jobs/{job_id}/adapt", {
          params: { path: { job_id: jobId } },
          body: { channel_id: channelId },
        }),
      ),
    meta: { silentError: true },
    onSuccess: (job, { jobId }) => settle(job, jobId),
  });
}

export function useRegenerateScene() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ jobId, sceneIdx, prompt }: { jobId: number; sceneIdx: number; prompt?: string | null }) =>
      unwrap(
        api.POST("/api/v1/jobs/{job_id}/scenes/{scene_idx}/regenerate", {
          params: { path: { job_id: jobId, scene_idx: sceneIdx } },
          body: { prompt: prompt?.trim() || null },
        }),
      ),
    meta: { silentError: true },
    onSuccess: (_accepted, { jobId }) => {
      void queryClient.invalidateQueries({ queryKey: jobKeys.all });
      void queryClient.invalidateQueries({ queryKey: queryKeys.job(jobId) });
    },
  });
}

// ---------------------------------------------------------------- создание (NewJobSheet)

/** Ключ идемпотентности (§5.1): повтор той же отправки не создаёт второй ролик. */
export function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `k${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;
}

export type CreateJobsInput =
  | { mode: "single"; body: JobCreateGenerate | JobCreateClip; idempotencyKey: string }
  | { mode: "batch"; body: JobBatchCreate; idempotencyKey: string };

/** `POST /jobs` (одна тема или клип) либо `POST /jobs/batch` (2–20 тем). Ответ — созданные ролики. */
export function useCreateJobs() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: CreateJobsInput): Promise<Job[]> => {
      const header = { "Idempotency-Key": input.idempotencyKey };
      if (input.mode === "single") {
        const job = await unwrap(api.POST("/api/v1/jobs", { params: { header }, body: input.body }));
        return [job];
      }
      const list = await unwrap(api.POST("/api/v1/jobs/batch", { params: { header }, body: input.body }));
      return list.items;
    },
    meta: { silentError: true },
    onSuccess: (jobs) => {
      for (const job of jobs) queryClient.setQueryData(jobKeys.detail(job.id), job);
      void queryClient.invalidateQueries({ queryKey: jobKeys.all });
    },
  });
}

/** Каналы для выбора (живые: прогрев и активные). */
export function useChannelOptions({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: jobKeys.channels,
    queryFn: ({ signal }) =>
      unwrap(api.GET("/api/v1/channels", { params: { query: { status: ["active", "warming"], limit: 200 } }, signal })),
    select: (data) => data.items,
    enabled,
    staleTime: 60_000,
  });
}

/** Активные кампании (для явного выбора варианта баннера). */
export function useCampaignOptions({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: jobKeys.campaigns,
    queryFn: ({ signal }) =>
      unwrap(api.GET("/api/v1/campaigns", { params: { query: { status: ["active"], limit: 200 } }, signal })),
    select: (data) => data.items,
    enabled,
    staleTime: 60_000,
  });
}

/** Варианты кампании; к роликам назначаются только активные. */
export function useVariantOptions(campaignId: number | null | undefined) {
  return useQuery({
    queryKey: jobKeys.variants(campaignId ?? 0),
    queryFn: ({ signal }) =>
      unwrap(
        api.GET("/api/v1/campaigns/{campaign_id}/variants", {
          params: { path: { campaign_id: campaignId as number } },
          signal,
        }),
      ),
    select: (data) => data.items.filter((variant) => variant.status === "active"),
    enabled: typeof campaignId === "number" && campaignId > 0,
    staleTime: 60_000,
  });
}

/** Реестр провайдеров (viewer+): переопределения в форме и красная плашка «TTS в mock». */
export function useProviderOptions({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: jobKeys.providers,
    queryFn: ({ signal }) => unwrap(api.GET("/api/v1/providers", { signal })),
    select: (data) => data.items,
    enabled,
    staleTime: 60_000,
  });
}

/** Провайдер-заглушка: ключ или адаптер `mock` (`tts.mock`, `mock_tts`, …). */
export function isMockProvider(provider: Pick<Provider, "key" | "adapter">): boolean {
  return /(^|[._])mock($|[._])/.test(provider.adapter) || /(^|_)mock($|_)/.test(provider.key);
}

/** Озвучка по умолчанию — заглушка (§7.3 «Красный callout, если TTS в mock»). */
export function defaultTtsIsMock(providers: Provider[] | undefined): Provider | null {
  if (!providers) return null;
  const tts = providers.filter((provider) => provider.kind === "tts" && provider.enabled);
  const current = tts.find((provider) => provider.is_default) ?? (tts.length === 1 ? tts[0] : undefined);
  return current && isMockProvider(current) ? current : null;
}
