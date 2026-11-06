"use client";

/**
 * Трек нарезки: источники, загрузки, транскрипт и медиа студии, кандидаты, clip-job, авторский слой,
 * голос автора и отчёт об оригинальности (docs/ARCHITECTURE.md §5.2, §6.3, §7.3). Владелец — M5-C.
 *
 * Ключи (§7.4) согласованы с SSE-мостом (lib/hooks/use-live-events.ts): событие `source_status`
 * инвалидирует `['sources']` и `['source', id]`, поэтому список — под `['sources', …]`, карточка и
 * кандидаты — под `['source', id, …]`. Тяжёлые неизменные данные (транскрипт, peaks, раскадровка)
 * лежат под `['source-media', id, …]`, чтобы каждое событие статуса их не перекачивало; в ключ
 * входит версия транскрипта, так что новый распознанный текст подтянется сам.
 * Всё про clip-job — под `['job', id, …]` (событие `job_status` перечитывает и их).
 */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, CSRF_HEADER, csrfToken, type Schemas, unwrap } from "@/lib/api/client";
import { networkError, toApiError } from "@/lib/api/errors";
import { useLiveRefetchInterval } from "@/lib/hooks/use-live-refetch";

import { jobKeys } from "./jobs";
import { queryKeys } from "./keys";

export type Source = Schemas["Source"];
export type SourceRow = Schemas["SourceRow"];
export type SourcePage = Schemas["SourcePage"];
export type SourceStatus = Schemas["SourceStatus"];
export type SourceRights = Schemas["SourceRights"];
export type SourceRightsIn = Schemas["SourceRightsIn"];
export type SourceOptions = Schemas["SourceOptions"];
export type SourcePipelineStep = Schemas["SourcePipelineStep"];
export type SourceTranscript = Schemas["SourceTranscript"];
export type SourcePeaks = Schemas["SourcePeaks"];
export type SourceStoryboard = Schemas["SourceStoryboard"];
export type SourceScenes = Schemas["SourceScenes"];
export type SourceSelectionInfo = Schemas["SourceSelectionInfo"];
export type ClipCandidate = Schemas["ClipCandidate"];
export type ClipCandidateList = Schemas["ClipCandidateList"];
export type ClipCandidatePatch = Schemas["ClipCandidatePatch"];
export type ClipCandidateCreate = Schemas["ClipCandidateCreate"];
export type ClipJobOptions = Schemas["ClipJobOptions"];
export type SourceSelectRequest = Schemas["SourceSelectRequest"];
export type AuthorScriptBody = Schemas["AuthorScriptBody"];
export type JobAuthorScript = Schemas["JobAuthorScript"];
export type JobAuthorVoice = Schemas["JobAuthorVoice"];
export type AuthorVoiceSegment = Schemas["AuthorVoiceSegment"];
export type AuthorVoiceSegmentState = Schemas["AuthorVoiceSegmentState"];
export type AuthorVoiceSource = Schemas["AuthorVoiceSource"];
export type OriginalityReportOut = Schemas["OriginalityReportOut"];
export type OriginalityEstimateRequest = Schemas["OriginalityEstimateRequest"];
export type UploadInfo = Schemas["UploadInfo"];
export type NicheRow = Schemas["NicheRow"];

export interface SourcesFilters {
  status?: SourceStatus[] | null;
  q?: string | null;
  sort?: string | null;
  limit?: number;
  offset?: number;
}

export const SOURCES_PAGE_SIZE = 50;

export const clipKeys = {
  sources: queryKeys.sources,
  list: (filters: SourcesFilters) => [...queryKeys.sources, "list", filters] as const,
  source: (id: number) => queryKeys.source(id),
  rights: (id: number) => [...queryKeys.source(id), "rights"] as const,
  candidates: (id: number, includeSuperseded: boolean) =>
    [...queryKeys.source(id), "candidates", { includeSuperseded }] as const,
  candidatesAll: (id: number) => [...queryKeys.source(id), "candidates"] as const,
  scenes: (id: number) => [...queryKeys.source(id), "scenes"] as const,
  media: (id: number) => ["source-media", id] as const,
  transcript: (id: number, version: string) => ["source-media", id, "transcript", version] as const,
  peaks: (id: number, version: string) => ["source-media", id, "peaks", version] as const,
  storyboard: (id: number, version: string) => ["source-media", id, "storyboard", version] as const,
  candidate: (id: number) => ["clip-candidate", id] as const,
  estimate: (kind: "candidate" | "job", id: number, body: OriginalityEstimateRequest) =>
    ["originality-estimate", kind, id, body] as const,
  authorScript: (jobId: number) => [...queryKeys.job(jobId), "author-script"] as const,
  authorVoice: (jobId: number) => [...queryKeys.job(jobId), "author-voice"] as const,
  originality: (jobId: number) => [...queryKeys.job(jobId), "originality"] as const,
  niches: ["clipping", "niches"] as const,
  upload: (id: string) => ["upload", id] as const,
};

// ---------------------------------------------------------------- источники

/** Список источников с прогрессом; живёт от SSE `source_status`, без него — опрос 10 с. */
export function useSources(filters: SourcesFilters) {
  const refetchInterval = useLiveRefetchInterval(false);
  return useQuery({
    queryKey: clipKeys.list(filters),
    queryFn: ({ signal }) =>
      unwrap(
        api.GET("/api/v1/sources", {
          params: {
            query: {
              status: filters.status?.length ? filters.status : undefined,
              q: filters.q?.trim() || undefined,
              sort: filters.sort || undefined,
              limit: filters.limit ?? SOURCES_PAGE_SIZE,
              offset: filters.offset ?? 0,
            },
          },
          signal,
        }),
      ),
    placeholderData: keepPreviousData,
    refetchInterval,
  });
}

const PROCESSING: ReadonlySet<SourceStatus> = new Set([
  "queued",
  "downloading",
  "preparing",
  "transcribing",
  "selecting",
]);

export function isSourceProcessing(status: SourceStatus | null | undefined): boolean {
  return !!status && PROCESSING.has(status);
}

/** Карточка источника: статус, шаги конвейера, права, медиа, итог отбора. */
export function useSource(id: number | null | undefined) {
  const live = useLiveRefetchInterval(false);
  return useQuery({
    queryKey: clipKeys.source(id ?? 0),
    queryFn: ({ signal }) =>
      unwrap(api.GET("/api/v1/sources/{source_id}", { params: { path: { source_id: id as number } }, signal })),
    enabled: typeof id === "number" && id > 0,
    // Пока идёт обработка, прогресс шагов обновляем и без SSE (события несут только статус и pct).
    refetchInterval: (query) => (isSourceProcessing(query.state.data?.status) ? 5_000 : live),
  });
}

/** Версия медиа источника для ключей кеша: меняется, когда пересчитан транскрипт или подготовка. */
export function mediaVersion(source: Source | undefined): string {
  if (!source) return "none";
  return [source.transcript?.created_at ?? "no-tr", source.duration_sec ?? 0, source.media.peaks_url ? 1 : 0].join("|");
}

export function useTranscript(source: Source | undefined) {
  const id = source?.id ?? 0;
  return useQuery({
    queryKey: clipKeys.transcript(id, mediaVersion(source)),
    queryFn: ({ signal }) =>
      unwrap(
        api.GET("/api/v1/sources/{source_id}/transcript", {
          params: { path: { source_id: id }, query: { format: "json" } },
          signal,
        }),
      ) as Promise<SourceTranscript>,
    enabled: id > 0 && !!source?.media.transcript_url,
    staleTime: Infinity,
    gcTime: 10 * 60_000,
  });
}

export function usePeaks(source: Source | undefined) {
  const id = source?.id ?? 0;
  return useQuery({
    queryKey: clipKeys.peaks(id, mediaVersion(source)),
    queryFn: ({ signal }) =>
      unwrap(api.GET("/api/v1/sources/{source_id}/peaks", { params: { path: { source_id: id } }, signal })),
    enabled: id > 0 && !!source?.media.peaks_url,
    staleTime: Infinity,
    gcTime: 10 * 60_000,
  });
}

export function useStoryboard(source: Source | undefined) {
  const id = source?.id ?? 0;
  return useQuery({
    queryKey: clipKeys.storyboard(id, mediaVersion(source)),
    queryFn: ({ signal }) =>
      unwrap(api.GET("/api/v1/sources/{source_id}/storyboard", { params: { path: { source_id: id } }, signal })),
    enabled: id > 0 && !!source?.media.storyboard_url,
    staleTime: Infinity,
  });
}

/** Склейки источника (PySceneDetect на прокси): маркеры на дорожке. */
export function useScenes(source: Source | undefined) {
  const id = source?.id ?? 0;
  return useQuery({
    queryKey: clipKeys.scenes(id),
    queryFn: ({ signal }) =>
      unwrap(api.GET("/api/v1/sources/{source_id}/scenes", { params: { path: { source_id: id } }, signal })),
    enabled: id > 0 && source?.status === "ready",
    staleTime: 5 * 60_000,
    retry: false,
  });
}

export function useSourceRights(id: number | null | undefined, { enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: clipKeys.rights(id ?? 0),
    queryFn: ({ signal }) =>
      unwrap(api.GET("/api/v1/sources/{source_id}/rights", { params: { path: { source_id: id as number } }, signal })),
    enabled: enabled && typeof id === "number" && id > 0,
  });
}

/** Ниши для словаря запретов при отборе (необязательно: без ответа — «без ниши»). */
export function useNicheOptions({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: clipKeys.niches,
    queryFn: ({ signal }) =>
      unwrap(api.GET("/api/v1/niches", { params: { query: { status: "active", limit: 200 } }, signal })),
    select: (data) => data.items,
    enabled,
    staleTime: 5 * 60_000,
    retry: false,
  });
}

function useSourcesSettled() {
  const queryClient = useQueryClient();
  return (source?: Source) => {
    if (source) queryClient.setQueryData(clipKeys.source(source.id), source);
    void queryClient.invalidateQueries({ queryKey: clipKeys.sources });
  };
}

export function useCreateSourceFromUpload() {
  const settle = useSourcesSettled();
  return useMutation({
    mutationFn: (body: Schemas["SourceFromUpload"]) => unwrap(api.POST("/api/v1/sources/from-upload", { body })),
    meta: { silentError: true },
    onSuccess: (source) => settle(source),
  });
}

export function useCreateSourceFromUrl() {
  const settle = useSourcesSettled();
  return useMutation({
    mutationFn: (body: Schemas["SourceFromUrl"]) => unwrap(api.POST("/api/v1/sources/from-url", { body })),
    meta: { silentError: true },
    onSuccess: (source) => settle(source),
  });
}

export function usePatchSource(id: number) {
  const settle = useSourcesSettled();
  return useMutation({
    mutationFn: (body: Schemas["SourcePatch"]) =>
      unwrap(api.PATCH("/api/v1/sources/{source_id}", { params: { path: { source_id: id } }, body })),
    onSuccess: (source) => settle(source),
  });
}

export function useDeleteSource() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, purgeFiles }: { id: number; purgeFiles: boolean }) =>
      unwrap(
        api.DELETE("/api/v1/sources/{source_id}", {
          params: { path: { source_id: id }, query: { purge_files: purgeFiles } },
        }),
      ),
    meta: { silentError: true },
    onSuccess: (_data, { id }) => {
      queryClient.removeQueries({ queryKey: clipKeys.source(id) });
      void queryClient.invalidateQueries({ queryKey: clipKeys.sources });
    },
  });
}

export function useReprocessSource(id: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["SourceReprocess"]) =>
      unwrap(api.POST("/api/v1/sources/{source_id}/reprocess", { params: { path: { source_id: id } }, body })),
    meta: { silentError: true },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: clipKeys.source(id) });
      void queryClient.invalidateQueries({ queryKey: clipKeys.sources });
    },
  });
}

export function usePutRights(id: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: SourceRightsIn) =>
      unwrap(api.PUT("/api/v1/sources/{source_id}/rights", { params: { path: { source_id: id } }, body })),
    meta: { silentError: true },
    onSuccess: (rights) => {
      queryClient.setQueryData(clipKeys.rights(id), rights);
      void queryClient.invalidateQueries({ queryKey: clipKeys.source(id) });
    },
  });
}

// ---------------------------------------------------------------- кандидаты

export function useCandidates(sourceId: number | null | undefined, { includeSuperseded = false } = {}) {
  const refetchInterval = useLiveRefetchInterval(false);
  return useQuery({
    queryKey: clipKeys.candidates(sourceId ?? 0, includeSuperseded),
    queryFn: ({ signal }) =>
      unwrap(
        api.GET("/api/v1/sources/{source_id}/candidates", {
          params: { path: { source_id: sourceId as number }, query: { include_superseded: includeSuperseded } },
          signal,
        }),
      ),
    enabled: typeof sourceId === "number" && sourceId > 0,
    placeholderData: keepPreviousData,
    refetchInterval,
  });
}

/** Ответ-кандидат кладём в список источника на место старого (без перечитывания всего списка). */
function useCandidateSettled() {
  const queryClient = useQueryClient();
  return (candidate: ClipCandidate) => {
    queryClient.setQueryData(clipKeys.candidate(candidate.id), candidate);
    queryClient.setQueriesData<ClipCandidateList>({ queryKey: clipKeys.candidatesAll(candidate.source_id) }, (old) => {
      if (!old) return old;
      const exists = old.items.some((item) => item.id === candidate.id);
      return {
        ...old,
        items: exists
          ? old.items.map((item) => (item.id === candidate.id ? candidate : item))
          : [candidate, ...old.items],
      };
    });
  };
}

export function usePatchCandidate() {
  const settle = useCandidateSettled();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: ClipCandidatePatch }) =>
      unwrap(api.PATCH("/api/v1/clip-candidates/{candidate_id}", { params: { path: { candidate_id: id } }, body })),
    meta: { silentError: true },
    onSuccess: (candidate) => settle(candidate),
  });
}

export function useCreateCandidate(sourceId: number) {
  const settle = useCandidateSettled();
  return useMutation({
    mutationFn: (body: ClipCandidateCreate) =>
      unwrap(api.POST("/api/v1/sources/{source_id}/candidates", { params: { path: { source_id: sourceId } }, body })),
    meta: { silentError: true },
    onSuccess: (candidate) => settle(candidate),
  });
}

export function useSelectAgain(sourceId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: SourceSelectRequest) =>
      unwrap(api.POST("/api/v1/sources/{source_id}/select", { params: { path: { source_id: sourceId } }, body })),
    meta: { silentError: true },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: clipKeys.source(sourceId) });
      void queryClient.invalidateQueries({ queryKey: clipKeys.sources });
    },
  });
}

/** «В рендер» пачкой: до 20 роликов из моментов источника одним запросом (`POST /sources/{id}/clips`). */
export function useCreateClipsBatch(sourceId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (items: Schemas["ClipBatchItem"][]) =>
      unwrap(api.POST("/api/v1/sources/{source_id}/clips", { params: { path: { source_id: sourceId } }, body: { items } })),
    meta: { silentError: true },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: jobKeys.all });
      void queryClient.invalidateQueries({
        predicate: (query) => query.queryKey[0] === "source" && query.queryKey[2] === "candidates",
      });
      void queryClient.invalidateQueries({ queryKey: clipKeys.source(sourceId) });
    },
  });
}

/** «В рендер» / черновик ролика: clip-job из кандидата (`POST /clip-candidates/{id}/jobs`). */
export function useCreateJobFromCandidate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ candidateId, body }: { candidateId: number; body: ClipJobOptions }) =>
      unwrap(
        api.POST("/api/v1/clip-candidates/{candidate_id}/jobs", {
          params: { path: { candidate_id: candidateId } },
          body,
        }),
      ),
    meta: { silentError: true },
    onSuccess: (job, { candidateId }) => {
      queryClient.setQueryData(jobKeys.detail(job.id), job);
      void queryClient.invalidateQueries({ queryKey: jobKeys.all });
      // Кандидат сразу «в работе» с этим роликом (не ждём перечитывания списка).
      const isCandidateList = (query: { queryKey: readonly unknown[] }) =>
        query.queryKey[0] === "source" && query.queryKey[2] === "candidates";
      queryClient.setQueriesData<ClipCandidateList>({ predicate: isCandidateList }, (old) =>
        old
          ? {
              ...old,
              items: old.items.map((item) =>
                item.id === candidateId ? { ...item, job_id: job.id, status: "used" as const } : item,
              ),
            }
          : old,
      );
      void queryClient.invalidateQueries({ predicate: isCandidateList });
      if (job.source) void queryClient.invalidateQueries({ queryKey: clipKeys.source(job.source.source_id) });
    },
  });
}

// ---------------------------------------------------------------- оригинальность

/**
 * Оценка оригинальности до рендера (индикатор 70/30). POST без побочных эффектов — поэтому запрос,
 * а не мутация: ключ — тело запроса, прежний отчёт держится на экране, пока считается новый.
 */
export function useOriginalityEstimate(
  target: { kind: "candidate" | "job"; id: number } | null,
  body: OriginalityEstimateRequest,
  { enabled = true }: { enabled?: boolean } = {},
) {
  return useQuery({
    queryKey: clipKeys.estimate(target?.kind ?? "candidate", target?.id ?? 0, body),
    queryFn: ({ signal }) => {
      if (!target) throw new Error("no target");
      return target.kind === "candidate"
        ? unwrap(
            api.POST("/api/v1/clip-candidates/{candidate_id}/originality-estimate", {
              params: { path: { candidate_id: target.id } },
              body,
              signal,
            }),
          )
        : unwrap(
            api.POST("/api/v1/jobs/{job_id}/originality/estimate", {
              params: { path: { job_id: target.id } },
              body,
              signal,
            }),
          );
    },
    enabled: enabled && !!target && target.id > 0,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
    retry: false,
  });
}

/** Отчёт финального рендера (стадия qc); 404 `originality_not_found` — ещё не считался. */
export function useJobOriginality(jobId: number | null | undefined, { enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: clipKeys.originality(jobId ?? 0),
    queryFn: ({ signal }) =>
      unwrap(api.GET("/api/v1/jobs/{job_id}/originality", { params: { path: { job_id: jobId as number } }, signal })),
    enabled: enabled && typeof jobId === "number" && jobId > 0,
    retry: false,
  });
}

// ---------------------------------------------------------------- авторский текст

export function useAuthorScript(jobId: number | null | undefined) {
  return useQuery({
    queryKey: clipKeys.authorScript(jobId ?? 0),
    queryFn: ({ signal }) =>
      unwrap(api.GET("/api/v1/jobs/{job_id}/author-script", { params: { path: { job_id: jobId as number } }, signal })),
    enabled: typeof jobId === "number" && jobId > 0,
    // Черновик пишет задача author_script_draft: пока она идёт, опрашиваем раз в 2 с.
    refetchInterval: (query) => (query.state.data?.drafting_task_id ? 2_000 : false),
  });
}

export function usePutAuthorScript() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ jobId, body }: { jobId: number; body: Schemas["AuthorScriptPut"] }) =>
      unwrap(api.PUT("/api/v1/jobs/{job_id}/author-script", { params: { path: { job_id: jobId } }, body })),
    meta: { silentError: true },
    onSuccess: (data, { jobId }) => {
      queryClient.setQueryData(clipKeys.authorScript(jobId), data);
      void queryClient.invalidateQueries({ queryKey: clipKeys.authorVoice(jobId) });
    },
  });
}

export function useAuthorScriptDraft() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ jobId, body }: { jobId: number; body: Schemas["AuthorScriptDraftRequest"] }) =>
      unwrap(api.POST("/api/v1/jobs/{job_id}/author-script/draft", { params: { path: { job_id: jobId } }, body })),
    meta: { silentError: true },
    onSuccess: (_accepted, { jobId }) => {
      void queryClient.invalidateQueries({ queryKey: clipKeys.authorScript(jobId) });
    },
  });
}

// ---------------------------------------------------------------- голос автора

export function useAuthorVoice(jobId: number | null | undefined) {
  return useQuery({
    queryKey: clipKeys.authorVoice(jobId ?? 0),
    queryFn: ({ signal }) =>
      unwrap(api.GET("/api/v1/jobs/{job_id}/author-voice", { params: { path: { job_id: jobId as number } }, signal })),
    enabled: typeof jobId === "number" && jobId > 0,
    refetchInterval: (query) =>
      query.state.data?.segments.some((segment) => segment.status === "processing") ? 2_000 : false,
  });
}

/** Лимит записи голоса (`AUTHOR_VOICE_MAX_BYTES`): 20 МБ. */
export const AUTHOR_VOICE_MAX_BYTES = 20 * 1024 * 1024;

/**
 * multipart `audio` + `segment` → 202 `AuthorVoiceAccepted`. openapi-fetch не собирает FormData
 * из типов схемы, поэтому запрос — обычным fetch с тем же CSRF и той же семантикой ошибок.
 */
export async function uploadAuthorVoice(
  jobId: number,
  segment: AuthorVoiceSegment,
  audio: Blob,
  filename: string,
): Promise<Schemas["AuthorVoiceAccepted"]> {
  const form = new FormData();
  form.append("segment", segment);
  form.append("audio", audio, filename);
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = csrfToken();
  if (token) headers[CSRF_HEADER] = token;
  let response: Response;
  try {
    response = await fetch(`/api/v1/jobs/${jobId}/author-voice`, {
      method: "POST",
      body: form,
      headers,
      credentials: "include",
    });
  } catch (cause) {
    throw networkError(cause);
  }
  const text = await response.text();
  let payload: unknown = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    payload = null;
  }
  if (!response.ok) throw toApiError(response.status, payload);
  return payload as Schemas["AuthorVoiceAccepted"];
}

export function useUploadAuthorVoice() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      jobId,
      segment,
      audio,
      filename,
    }: {
      jobId: number;
      segment: AuthorVoiceSegment;
      audio: Blob;
      filename: string;
    }) => uploadAuthorVoice(jobId, segment, audio, filename),
    meta: { silentError: true },
    onSuccess: (_accepted, { jobId }) => {
      void queryClient.invalidateQueries({ queryKey: clipKeys.authorVoice(jobId) });
    },
  });
}

export function useDeleteAuthorVoice() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ jobId, segment }: { jobId: number; segment: AuthorVoiceSegment }) =>
      unwrap(
        api.DELETE("/api/v1/jobs/{job_id}/author-voice/{segment}", {
          params: { path: { job_id: jobId, segment } },
        }),
      ),
    meta: { silentError: true },
    onSuccess: (_data, { jobId }) => {
      void queryClient.invalidateQueries({ queryKey: clipKeys.authorVoice(jobId) });
    },
  });
}

// ---------------------------------------------------------------- загрузки

/** Состояние tus-загрузки (для «продолжить» после перезагрузки вкладки). */
export async function fetchUploadInfo(uploadId: string): Promise<UploadInfo> {
  return unwrap(api.GET("/api/v1/uploads/{upload_id}", { params: { path: { upload_id: uploadId } } }));
}
