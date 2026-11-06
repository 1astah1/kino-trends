"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type Schemas, unwrap } from "@/lib/api/client";

/**
 * Атрибуция (docs/ARCHITECTURE.md §6.6): трекинг-ссылки (канальные — для кнопки «Подписаться» и
 * шапки канала), вебхук продукта (секрет HMAC, инструкция интеграции, тестовое событие).
 */
export type TrackingLink = Schemas["TrackingLink"];
export type TrackingLevel = Schemas["TrackingLevel"];
export type ProductIntegration = Schemas["ProductIntegration"];
export type WebhookSecretRotated = Schemas["WebhookSecretRotated"];
export type ConversionTestResult = Schemas["ConversionTestResult"];
export type ConversionEventType = Schemas["ConversionEventType"];
export type IngestOutcome = Schemas["IngestOutcome"];

export interface TrackingLinkFilters {
  levels?: TrackingLevel[];
  channelId?: string | null;
  productId?: string | null;
  isActive?: boolean | null;
}

export const trackingKeys = {
  all: ["tracking-links"] as const,
  list: (filters: TrackingLinkFilters) => ["tracking-links", "list", filters] as const,
  stats: (id: number) => ["tracking-links", "stats", id] as const,
  integration: (productId: string) => ["product", productId, "integration"] as const,
};

export function useTrackingLinks(filters: TrackingLinkFilters, { enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: trackingKeys.list(filters),
    queryFn: ({ signal }) =>
      unwrap(
        api.GET("/api/v1/tracking-links", {
          params: {
            query: {
              level: filters.levels?.length ? filters.levels : undefined,
              channel_id: filters.channelId ?? undefined,
              product_id: filters.productId ?? undefined,
              is_active: filters.isActive ?? undefined,
              limit: 200,
            },
          },
          signal,
        }),
      ),
    placeholderData: keepPreviousData,
    enabled,
  });
}

/**
 * Ссылка канала (кнопка «Подписаться», шапка канала). Повторный запрос отдаёт существующую
 * активную ссылку, поэтому кнопку можно нажимать без страха наплодить коды.
 */
export function useCreateChannelLink(channelId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["ChannelTrackingLinkCreate"]) =>
      unwrap(
        api.POST("/api/v1/channels/{channel_id}/tracking-link", { params: { path: { channel_id: channelId } }, body }),
      ),
    meta: { silentError: true },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: trackingKeys.all }),
  });
}

export function useUpdateTrackingLink(id: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["TrackingLinkPatch"]) =>
      unwrap(api.PATCH("/api/v1/tracking-links/{link_id}", { params: { path: { link_id: id } }, body })),
    meta: { silentError: true },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: trackingKeys.all }),
  });
}

/** Статистика ссылки без ботов и тестовых событий (по умолчанию — последние 30 дней на сервере). */
export function useTrackingLinkStats(id: number | null) {
  return useQuery({
    queryKey: trackingKeys.stats(id ?? 0),
    queryFn: ({ signal }) =>
      unwrap(
        api.GET("/api/v1/tracking-links/{link_id}/stats", {
          params: { path: { link_id: id ?? 0 }, query: { granularity: "day" } },
          signal,
        }),
      ),
    enabled: id !== null && id > 0,
  });
}

// ---------------------------------------------------------------- вебхук продукта

export function useProductIntegration(productId: string | null) {
  return useQuery({
    queryKey: trackingKeys.integration(productId ?? ""),
    queryFn: ({ signal }) =>
      unwrap(
        api.GET("/api/v1/products/{product_id}/integration", {
          params: { path: { product_id: productId ?? "" } },
          signal,
        }),
      ),
    enabled: !!productId,
  });
}

/** Новый секрет HMAC (только admin): значение приходит один раз, прежний живёт `grace_hours`. */
export function useRotateWebhookSecret(productId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (graceHours: number) =>
      unwrap(
        api.POST("/api/v1/products/{product_id}/webhook-secret/rotate", {
          params: { path: { product_id: productId } },
          body: { grace_hours: graceHours },
        }),
      ),
    meta: { silentError: true },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: trackingKeys.integration(productId) });
      void queryClient.invalidateQueries({ queryKey: ["product", productId] });
      void queryClient.invalidateQueries({ queryKey: ["products"] });
    },
  });
}

/** Тестовое событие через тот же код приёма (`is_test`, в статистику не идёт). */
export function useSendTestConversion(productId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["ConversionTestRequest"]) =>
      unwrap(
        api.POST("/api/v1/products/{product_id}/test-conversion", {
          params: { path: { product_id: productId } },
          body,
        }),
      ),
    meta: { silentError: true },
    onSettled: () => void queryClient.invalidateQueries({ queryKey: trackingKeys.integration(productId) }),
  });
}
