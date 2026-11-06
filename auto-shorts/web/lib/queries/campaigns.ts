"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type Schemas, unwrap } from "@/lib/api/client";

export type Campaign = Schemas["Campaign"];
export type CampaignCreate = Schemas["CampaignCreate"];
export type CampaignPatch = Schemas["CampaignPatch"];
export type CampaignStatus = Schemas["CampaignStatus"];
export type BannerVariant = Schemas["BannerVariant"];
export type BannerVariantCreate = Schemas["BannerVariantCreate"];
export type BannerVariantPatch = Schemas["BannerVariantPatch"];
export type VariantStatus = Schemas["VariantStatus"];

/** Ключи кампаний и вариантов баннера. */
export const campaignKeys = {
  all: ["campaigns"] as const,
  list: (productId: string | null) => ["campaigns", productId ?? "all"] as const,
  detail: (id: number) => ["campaign", id] as const,
  variants: (campaignId: number) => ["campaign-variants", campaignId] as const,
  variantsAll: ["campaign-variants"] as const,
  variant: (id: number) => ["banner-variant", id] as const,
};

export function useCampaigns({ productId }: { productId?: string | null } = {}) {
  return useQuery({
    queryKey: campaignKeys.list(productId ?? null),
    queryFn: () =>
      unwrap(api.GET("/api/v1/campaigns", { params: { query: { product_id: productId ?? undefined, limit: 200 } } })),
    placeholderData: keepPreviousData,
  });
}

export function useCampaign(id: number | null | undefined) {
  return useQuery({
    queryKey: campaignKeys.detail(id ?? 0),
    queryFn: () => unwrap(api.GET("/api/v1/campaigns/{campaign_id}", { params: { path: { campaign_id: id ?? 0 } } })),
    enabled: !!id,
  });
}

export function useCreateCampaign() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CampaignCreate) => unwrap(api.POST("/api/v1/campaigns", { body })),
    meta: { silentError: true },
    onSuccess: (campaign) => {
      queryClient.setQueryData(campaignKeys.detail(campaign.id), campaign);
      void queryClient.invalidateQueries({ queryKey: campaignKeys.all });
    },
  });
}

export function useUpdateCampaign() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: CampaignPatch }) =>
      unwrap(api.PATCH("/api/v1/campaigns/{campaign_id}", { params: { path: { campaign_id: id } }, body })),
    meta: { silentError: true },
    onSuccess: (campaign) => {
      queryClient.setQueryData(campaignKeys.detail(campaign.id), campaign);
      void queryClient.invalidateQueries({ queryKey: campaignKeys.all });
    },
  });
}

export function useSetCampaignStatus() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, status }: { id: number; status: CampaignStatus }) =>
      unwrap(
        api.POST("/api/v1/campaigns/{campaign_id}/status", {
          params: { path: { campaign_id: id } },
          body: { status },
        }),
      ),
    onSuccess: (campaign) => {
      queryClient.setQueryData(campaignKeys.detail(campaign.id), campaign);
      void queryClient.invalidateQueries({ queryKey: campaignKeys.all });
    },
  });
}

// ---------------------------------------------------------------- варианты

export function useVariants(campaignId: number | null | undefined) {
  return useQuery({
    queryKey: campaignKeys.variants(campaignId ?? 0),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/campaigns/{campaign_id}/variants", { params: { path: { campaign_id: campaignId ?? 0 } } }),
      ),
    select: (data) => data.items,
    enabled: !!campaignId,
  });
}

export function useVariant(id: number | null | undefined) {
  return useQuery({
    queryKey: campaignKeys.variant(id ?? 0),
    queryFn: () => fetchVariant(id ?? 0),
    enabled: !!id,
    // Редактор держит свою копию черновика: фоновое перечитывание не должно её перетирать.
    refetchOnWindowFocus: false,
  });
}

/**
 * Форма ответа openapi-fetch: кортежи схемы (`timing.impulse_window_pct`) приходят массивами
 * (`Readable` из openapi-typescript-helpers). Редактор переводит её в `BannerVariant` через `normalizeTiming`.
 */
export type ApiVariant = Awaited<ReturnType<typeof fetchVariant>>;

function fetchVariant(id: number) {
  return unwrap(api.GET("/api/v1/banner-variants/{variant_id}", { params: { path: { variant_id: id } } }));
}

/** `impulse_window_pct` из ответа (массив) → кортеж схемы; остальное как есть. */
export function normalizeTiming<T extends { impulse_window_pct: number[] }>(
  timing: T,
): Omit<T, "impulse_window_pct"> & { impulse_window_pct: [number, number] } {
  const [lo = 0.4, hi = 0.65] = timing.impulse_window_pct;
  return { ...timing, impulse_window_pct: [lo, hi] };
}

export function normalizeVariant(variant: ApiVariant): BannerVariant {
  return { ...variant, timing: normalizeTiming(variant.timing) };
}

function storeVariant(queryClient: ReturnType<typeof useQueryClient>, variant: ApiVariant) {
  queryClient.setQueryData(campaignKeys.variant(variant.id), variant);
  void queryClient.invalidateQueries({ queryKey: campaignKeys.variants(variant.campaign_id) });
  void queryClient.invalidateQueries({ queryKey: campaignKeys.all });
}

/**
 * Новый вариант. Без `layers` сервер строит слои по умолчанию вокруг `creative_sha256`
 * (микро-бренд продукта, креатив в зоне оффера, код ролика под ним), статус по умолчанию — draft.
 */
export function useCreateVariant() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ campaignId, body }: { campaignId: number; body: BannerVariantCreate }) =>
      unwrap(
        api.POST("/api/v1/campaigns/{campaign_id}/variants", { params: { path: { campaign_id: campaignId } }, body }),
      ),
    meta: { silentError: true },
    onSuccess: (variant) => storeVariant(queryClient, variant),
  });
}

/** Правка варианта; `expected_version` → 409 `version_conflict`, если вариант уже изменили. */
export function useUpdateVariant() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: BannerVariantPatch }) =>
      unwrap(api.PATCH("/api/v1/banner-variants/{variant_id}", { params: { path: { variant_id: id } }, body })),
    meta: { silentError: true },
    onSuccess: (variant) => storeVariant(queryClient, variant),
  });
}

/** Статус варианта. Включение (`active`) проходит серверную проверку: ошибки → 422 с `details.warnings`. */
export function useSetVariantStatus() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, status }: { id: number; status: VariantStatus }) =>
      unwrap(
        api.POST("/api/v1/banner-variants/{variant_id}/status", {
          params: { path: { variant_id: id } },
          body: { status },
        }),
      ),
    meta: { silentError: true },
    onSuccess: (variant) => storeVariant(queryClient, variant),
  });
}

/** Копия для A/B: те же слои и тайминг, новый код; дальше меняется креатив или позиция. */
export function useDuplicateVariant() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, code, name }: { id: number; code: string; name?: string | null }) =>
      unwrap(
        api.POST("/api/v1/banner-variants/{variant_id}/duplicate", {
          params: { path: { variant_id: id } },
          body: { code, name: name ?? null },
        }),
      ),
    meta: { silentError: true },
    onSuccess: (variant) => storeVariant(queryClient, variant),
  });
}

/** Следующий свободный код варианта: A, B, … Z, затем A2, B2, … */
export function nextVariantCode(existing: readonly string[]): string {
  const taken = new Set(existing.map((code) => code.toUpperCase()));
  for (let round = 1; round < 10; round += 1) {
    for (let i = 0; i < 26; i += 1) {
      const code = `${String.fromCharCode(65 + i)}${round === 1 ? "" : round}`;
      if (!taken.has(code)) return code;
    }
  }
  return `V${existing.length + 1}`;
}
