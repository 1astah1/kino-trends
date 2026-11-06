"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type Schemas, unwrap } from "@/lib/api/client";
import { networkError, toApiError } from "@/lib/api/errors";

export type BannerTemplate = Schemas["BannerTemplate"];
export type BannerVariantDraft = Schemas["BannerVariantDraft"];
export type BannerValidateRequest = Schemas["BannerValidateRequest"];
export type BannerValidateResponse = Schemas["BannerValidateResponse"];
export type BannerPreviewRequest = Schemas["BannerPreviewRequest"];
export type BannerWarning = Schemas["BannerWarning"];
export type BannerFont = Schemas["BannerFont"];

export const bannerKeys = {
  templates: ["banner-templates"] as const,
  template: (id: number) => ["banner-template", id] as const,
  fonts: ["banner-fonts"] as const,
  validate: (request: BannerValidateRequest, revision = "") => ["banner-validate", request, revision] as const,
};

// ---------------------------------------------------------------- шаблоны (пресеты варианта)

export function useBannerTemplates({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: bannerKeys.templates,
    queryFn: () => unwrap(api.GET("/api/v1/banner-templates", { params: { query: { limit: 200 } } })),
    select: (data) => data.items,
    enabled,
  });
}

export function useBannerTemplate(id: number | null | undefined) {
  return useQuery({
    queryKey: bannerKeys.template(id ?? 0),
    queryFn: () =>
      unwrap(api.GET("/api/v1/banner-templates/{template_id}", { params: { path: { template_id: id ?? 0 } } })),
    enabled: !!id,
    refetchOnWindowFocus: false,
  });
}

export function useUpdateBannerTemplate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: Schemas["BannerTemplatePatch"] }) =>
      unwrap(api.PATCH("/api/v1/banner-templates/{template_id}", { params: { path: { template_id: id } }, body })),
    meta: { silentError: true },
    onSuccess: (template) => {
      queryClient.setQueryData(bannerKeys.template(template.id), template);
      void queryClient.invalidateQueries({ queryKey: bannerKeys.templates });
    },
  });
}

// ---------------------------------------------------------------- шрифты служебного текста

export function useBannerFonts() {
  return useQuery({
    queryKey: bannerKeys.fonts,
    queryFn: () => unwrap(api.GET("/api/v1/banner-fonts")),
    select: (data) => data.items,
    staleTime: 60 * 60_000,
    refetchOnWindowFocus: false,
  });
}

// ---------------------------------------------------------------- проверка

/**
 * Серверная проверка — источник истины редактора. Запрос строит вызывающий из уже «успокоенного»
 * (debounce) черновика; ключ кеша — сам запрос, поэтому повтор того же состояния не ходит на сервер.
 * Прошлый результат держится, пока идёт новый (`keepPreviousData`): список предупреждений не мигает.
 */
export function useBannerValidation(
  request: BannerValidateRequest | null,
  { enabled = true, revision = "" }: { enabled?: boolean; revision?: string } = {},
) {
  return useQuery({
    // revision — состояние креативов (processing → ready): тот же черновик проверяется заново.
    queryKey: bannerKeys.validate(request ?? { platform: "youtube", duration_sec: 30 }, revision),
    queryFn: ({ signal }) => unwrap(api.POST("/api/v1/banner-variants/validate", { body: request!, signal })),
    enabled: enabled && request !== null,
    placeholderData: keepPreviousData,
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
    retry: false,
  });
}

// ---------------------------------------------------------------- «Кадр из рендера»

export interface PreviewFrame {
  /** `blob:`-адрес PNG; вызывающий освобождает его через `URL.revokeObjectURL`. */
  url: string;
  blob: Blob;
  /** Фактическое время кадра из `X-Preview-T-Sec`. */
  tSec: number | null;
  width: number | null;
  height: number | null;
  request: BannerPreviewRequest;
}

async function imageSize(blob: Blob): Promise<{ width: number | null; height: number | null }> {
  try {
    const bitmap = await createImageBitmap(blob);
    const size = { width: bitmap.width, height: bitmap.height };
    bitmap.close();
    return size;
  } catch {
    return { width: null, height: null };
  }
}

/**
 * PNG-кадр тем же кодом, что финальный рендер (`banner_overlay`). 429 `rate_limited` — другой кадр
 * ещё рендерится (`details.retry_after_sec`).
 */
export async function fetchPreviewFrame(request: BannerPreviewRequest, signal?: AbortSignal): Promise<PreviewFrame> {
  let result;
  try {
    result = await api.POST("/api/v1/banner-variants/preview", {
      body: request,
      parseAs: "blob",
      signal,
      headers: { Accept: "image/png" },
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw networkError(cause);
  }
  const { data, error, response } = result;
  if (!response.ok || error !== undefined || !data) {
    let body: unknown = error;
    // parseAs: "blob" отдаёт и тело ошибки как Blob — разбираем JSON сами.
    if (body instanceof Blob) {
      try {
        body = JSON.parse(await body.text());
      } catch {
        body = null;
      }
    }
    throw toApiError(response.status, body);
  }
  const blob = data as Blob;
  const header = response.headers.get("X-Preview-T-Sec");
  const tSec = header !== null && header !== "" && Number.isFinite(Number(header)) ? Number(header) : null;
  const size = await imageSize(blob);
  return { url: URL.createObjectURL(blob), blob, tSec, ...size, request };
}

export function usePreviewFrame() {
  return useMutation({
    mutationFn: ({ request, signal }: { request: BannerPreviewRequest; signal?: AbortSignal }) =>
      fetchPreviewFrame(request, signal),
    meta: { silentError: true },
  });
}
