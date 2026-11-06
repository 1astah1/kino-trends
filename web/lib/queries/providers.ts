"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type Schemas, unwrap } from "@/lib/api/client";

export type Provider = Schemas["Provider"];
export type ProviderKind = Schemas["ProviderKind"];
export type ProviderUpdate = Schemas["ProviderUpdate"];
export type ProviderTestResult = Schemas["ProviderTestResult"];
export type SecretItem = Schemas["SecretItem"];

export const providerKeys = {
  list: ["providers"] as const,
  secrets: ["secrets"] as const,
};

/** Реестр провайдеров с конфигами (`GET /providers`, читать может любой вошедший). */
export function useProviders({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: providerKeys.list,
    queryFn: () => unwrap(api.GET("/api/v1/providers")),
    select: (data) => data.items,
    enabled,
  });
}

/** Конфиг провайдера (admin). Тело — полное: `enabled`, `is_default`, `params`, `secret_name`. */
export function useUpdateProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ key, body }: { key: string; body: ProviderUpdate }) =>
      unwrap(api.PUT("/api/v1/providers/{provider_key}", { params: { path: { provider_key: key } }, body })),
    meta: { silentError: true },
    // is_default одного провайдера снимает флаг у остальных того же вида — перечитываем весь список.
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: providerKeys.list }),
  });
}

/** «Проверить связь»: реальный вызов провайдера (может стоить копейки у платных). */
export function useTestProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (key: string) =>
      unwrap(api.POST("/api/v1/providers/{provider_key}/test", { params: { path: { provider_key: key } } })),
    meta: { silentError: true },
    onSettled: () => void queryClient.invalidateQueries({ queryKey: providerKeys.list }),
  });
}

// ---------------------------------------------------------------- секреты (только admin, значения не отдаются)

export function useSecrets({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: providerKeys.secrets,
    queryFn: () => unwrap(api.GET("/api/v1/secrets")),
    select: (data) => data.items,
    enabled,
  });
}

function invalidateSecrets(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: providerKeys.secrets });
  void queryClient.invalidateQueries({ queryKey: providerKeys.list });
}

/** Записать секрет: только на запись, обратно сервер отдаёт лишь последние 4 символа. */
export function useWriteSecret() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ name, value }: { name: string; value: string }) =>
      unwrap(api.PUT("/api/v1/secrets/{secret_name}", { params: { path: { secret_name: name } }, body: { value } })),
    meta: { silentError: true },
    onSuccess: () => invalidateSecrets(queryClient),
  });
}

export function useDeleteSecret() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) =>
      unwrap(api.DELETE("/api/v1/secrets/{secret_name}", { params: { path: { secret_name: name } } })),
    onSuccess: () => invalidateSecrets(queryClient),
  });
}
