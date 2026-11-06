"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type Schemas, setCsrfToken, unwrap } from "@/lib/api/client";

import { queryKeys } from "./keys";

export type AuthState = Schemas["AuthState"];

export async function fetchMe(): Promise<AuthState> {
  const state = await unwrap(api.GET("/api/v1/auth/me"));
  setCsrfToken(state.csrf_token);
  return state;
}

/** Текущая сессия. 401 не повторяется (retry по умолчанию пропускает 4xx). */
export function useMe() {
  return useQuery({
    queryKey: queryKeys.me,
    queryFn: fetchMe,
    staleTime: 60_000,
    refetchOnWindowFocus: true,
  });
}

export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["LoginRequest"]) => unwrap(api.POST("/api/v1/auth/login", { body })),
    meta: { silentError: true },
    onSuccess: (state) => {
      setCsrfToken(state.csrf_token);
      queryClient.setQueryData(queryKeys.me, state);
    },
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      try {
        await unwrap(api.POST("/api/v1/auth/logout"));
      } catch {
        // сессия могла уже истечь — выходим локально в любом случае
      }
    },
    meta: { silentError: true },
    onSettled: () => {
      setCsrfToken(null);
      queryClient.clear();
    },
  });
}

/** Смена своего пароля: сервер отзывает остальные сессии (§5.2). */
export function useChangePassword() {
  return useMutation({
    mutationFn: (body: Schemas["ChangePasswordRequest"]) => unwrap(api.POST("/api/v1/auth/change-password", { body })),
    meta: { silentError: true },
  });
}
