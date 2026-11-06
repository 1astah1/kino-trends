"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type Schemas, unwrap } from "@/lib/api/client";

import { queryKeys } from "./keys";

export type SettingItem = Schemas["SettingItem"];
export type Budget = Schemas["Budget"];
export type BudgetPatch = Schemas["BudgetPatch"];

export const settingsKeys = {
  list: ["settings"] as const,
};

/** Настройки с дефолтами (`GET /settings`, оператор и выше). */
export function useSettingsList({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: settingsKeys.list,
    queryFn: () => unwrap(api.GET("/api/v1/settings")),
    select: (data) => data.items,
    enabled,
  });
}

/** `{key: value}`; `null` сбрасывает переопределение к дефолту. Только admin. */
export function useUpdateSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Record<string, unknown>) => unwrap(api.PATCH("/api/v1/settings", { body })),
    meta: { silentError: true },
    onSuccess: (data) => queryClient.setQueryData(settingsKeys.list, data),
  });
}

// ---------------------------------------------------------------- бюджет (чтение — lib/queries/budget.ts)

function storeBudget(queryClient: ReturnType<typeof useQueryClient>, budget: Budget) {
  queryClient.setQueryData(queryKeys.budget, budget);
}

export function useUpdateBudget() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: BudgetPatch) => unwrap(api.PATCH("/api/v1/budget", { body })),
    meta: { silentError: true },
    onSuccess: (budget) => storeBudget(queryClient, budget),
  });
}

export function usePauseBudget() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (reason: string) => unwrap(api.POST("/api/v1/budget/pause", { body: { reason } })),
    onSuccess: (budget) => storeBudget(queryClient, budget),
  });
}

export function useResumeBudget() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/budget/resume")),
    onSuccess: (budget) => storeBudget(queryClient, budget),
  });
}
