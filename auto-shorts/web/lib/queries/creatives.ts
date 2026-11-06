"use client";

import { keepPreviousData, useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";

import { API_PREFIX, api, CSRF_HEADER, csrfToken, type Schemas, unwrap } from "@/lib/api/client";
import { ApiError, networkError, toApiError } from "@/lib/api/errors";

export type BannerCreative = Schemas["BannerCreative"];
export type BannerCreativeUploadResult = Schemas["BannerCreativeUploadResult"];
export type CreativeStatus = Schemas["CreativeStatus"];
export type CreativeKind = Schemas["CreativeKind"];
export type CreativeRejectCode = Schemas["CreativeRejectCode"];

/** Лимит загрузки креатива (`MAX_CREATIVE_UPLOAD_BYTES` сервера). */
export const MAX_CREATIVE_BYTES = 50 * 1024 * 1024;
/** Что принимает `<input type="file">`: тип сервер всё равно определяет по сигнатуре. */
export const CREATIVE_ACCEPT =
  "image/png,image/jpeg,image/webp,image/gif,image/apng,video/webm,video/mp4,video/quicktime,.png,.jpg,.jpeg,.webp,.gif,.apng,.webm,.mp4,.mov";

export const creativeKeys = {
  all: ["banner-creatives"] as const,
  list: (filters: CreativeFilters) => ["banner-creatives", filters] as const,
  detail: (sha: string) => ["banner-creative", sha] as const,
};

export interface CreativeFilters {
  productId?: string | null;
  kind?: CreativeKind | null;
  q?: string | null;
}

/** Библиотека креативов (без удалённых). Пока есть `processing`, опрашиваем раз в 3 с. */
export function useCreatives(filters: CreativeFilters = {}) {
  return useQuery({
    queryKey: creativeKeys.list(filters),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/banner-creatives", {
          params: {
            query: {
              product_id: filters.productId ?? undefined,
              kind: filters.kind ?? undefined,
              q: filters.q || undefined,
              sort: "-created_at",
              limit: 200,
            },
          },
        }),
      ),
    placeholderData: keepPreviousData,
    refetchInterval: (query) => (query.state.data?.items.some((item) => item.status === "processing") ? 3_000 : false),
  });
}

const creativeQuery = (sha: string) => ({
  queryKey: creativeKeys.detail(sha),
  queryFn: () =>
    unwrap(api.GET("/api/v1/banner-creatives/{creative_sha256}", { params: { path: { creative_sha256: sha } } })),
  staleTime: 60_000,
  refetchInterval: (query: { state: { data?: BannerCreative } }) =>
    query.state.data?.status === "processing" ? 3_000 : false,
});

export function useCreative(sha: string | null | undefined) {
  return useQuery({ ...creativeQuery(sha ?? ""), enabled: !!sha });
}

/** Карточки креативов по списку sha256 (для холста редактора: размеры и адреса файлов). */
export function useCreativesBySha(shas: readonly string[]) {
  const unique = Array.from(new Set(shas.filter(Boolean)));
  return useQueries({
    queries: unique.map((sha) => ({ ...creativeQuery(sha), retry: false })),
    combine: (results) => {
      const map: Record<string, BannerCreative> = {};
      const failed: string[] = [];
      results.forEach((result, index) => {
        if (result.data) map[unique[index]] = result.data;
        else if (result.isError) failed.push(unique[index]);
      });
      return { map, failed, isPending: results.some((result) => result.isPending) };
    },
  });
}

export function useUpdateCreative() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ sha, body }: { sha: string; body: Schemas["BannerCreativePatch"] }) =>
      unwrap(
        api.PATCH("/api/v1/banner-creatives/{creative_sha256}", {
          params: { path: { creative_sha256: sha } },
          body,
        }),
      ),
    onSuccess: (creative) => {
      queryClient.setQueryData(creativeKeys.detail(creative.sha256), creative);
      void queryClient.invalidateQueries({ queryKey: creativeKeys.all });
    },
  });
}

/** Мягкое удаление; 409 `creative_in_use` (+ `details.variant_ids`), пока креатив стоит в вариантах. */
export function useDeleteCreative() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (sha: string) =>
      unwrap(api.DELETE("/api/v1/banner-creatives/{creative_sha256}", { params: { path: { creative_sha256: sha } } })),
    meta: { silentError: true },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: creativeKeys.all }),
  });
}

// ---------------------------------------------------------------- загрузка с прогрессом

export interface UploadProgress {
  loaded: number;
  total: number;
  pct: number;
}

export interface UploadCreativeOptions {
  file: File;
  name?: string | null;
  productId?: string | null;
  onProgress?: (progress: UploadProgress) => void;
  signal?: AbortSignal;
}

/**
 * `POST /banner-creatives` (multipart) через XMLHttpRequest: у `fetch` нет прогресса отправки.
 * Та же cookie-сессия и CSRF double-submit, что у openapi-fetch. Ошибки — классы `lib/api/errors.ts`;
 * у 413/415/422 в `details.asset_error` — `CreativeRejectCode`. nginx может ответить 413 без JSON —
 * тогда код ставим сами.
 */
export function uploadCreative({
  file,
  name,
  productId,
  onProgress,
  signal,
}: UploadCreativeOptions): Promise<BannerCreativeUploadResult> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Загрузка отменена", "AbortError"));
      return;
    }
    const xhr = new XMLHttpRequest();
    const form = new FormData();
    form.append("file", file, file.name);
    if (name) form.append("name", name);
    if (productId) form.append("product_id", productId);

    xhr.open("POST", `${API_PREFIX}/banner-creatives`);
    xhr.withCredentials = true;
    xhr.setRequestHeader("Accept", "application/json");
    const token = csrfToken();
    if (token) xhr.setRequestHeader(CSRF_HEADER, token);

    xhr.upload.onprogress = (event) => {
      if (!onProgress) return;
      const total = event.lengthComputable ? event.total : file.size;
      onProgress({ loaded: event.loaded, total, pct: total ? Math.min(100, (event.loaded / total) * 100) : 0 });
    };
    const onAbort = () => xhr.abort();
    signal?.addEventListener("abort", onAbort, { once: true });

    xhr.onload = () => {
      signal?.removeEventListener("abort", onAbort);
      let body: unknown = null;
      try {
        body = xhr.responseText ? JSON.parse(xhr.responseText) : null;
      } catch {
        body = null;
      }
      if (xhr.status >= 200 && xhr.status < 300 && body) {
        resolve(body as BannerCreativeUploadResult);
        return;
      }
      if (xhr.status === 413 && !(body && typeof body === "object" && "error" in body)) {
        body = {
          error: {
            code: "payload_too_large",
            message: "Файл больше 50 МБ",
            details: { asset_error: "file_too_large" },
          },
        };
      }
      reject(toApiError(xhr.status, body));
    };
    xhr.onerror = () => {
      signal?.removeEventListener("abort", onAbort);
      reject(networkError());
    };
    xhr.onabort = () => {
      signal?.removeEventListener("abort", onAbort);
      reject(new DOMException("Загрузка отменена", "AbortError"));
    };
    xhr.send(form);
  });
}

/** Код отказа файла из ошибки загрузки (`details.asset_error`). */
export function uploadRejectCode(error: unknown): CreativeRejectCode | null {
  if (!(error instanceof ApiError)) return null;
  const code = error.details.asset_error;
  return typeof code === "string" ? (code as CreativeRejectCode) : null;
}

export function useUploadCreative() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: uploadCreative,
    meta: { silentError: true },
    onSuccess: (result) => {
      queryClient.setQueryData(creativeKeys.detail(result.creative.sha256), result.creative);
      void queryClient.invalidateQueries({ queryKey: creativeKeys.all });
    },
  });
}
