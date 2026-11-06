"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type Schemas, unwrap } from "@/lib/api/client";

import { queryKeys } from "./keys";

export type User = Schemas["User"];
export type UserCreate = Schemas["UserCreate"];
export type UserUpdate = Schemas["UserUpdate"];

/** Пользователи (только admin, §5.2). */
export function useUsers({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: queryKeys.users,
    queryFn: () => unwrap(api.GET("/api/v1/users")),
    select: (data) => data.items,
    enabled,
  });
}

export function useCreateUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: UserCreate) => unwrap(api.POST("/api/v1/users", { body })),
    meta: { silentError: true },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.users }),
  });
}

export function useUpdateUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, body }: { userId: number; body: UserUpdate }) =>
      unwrap(api.PATCH("/api/v1/users/{user_id}", { params: { path: { user_id: userId } }, body })),
    meta: { silentError: true },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.users }),
  });
}

export function useResetPassword() {
  return useMutation({
    mutationFn: ({ userId, newPassword }: { userId: number; newPassword: string }) =>
      unwrap(
        api.POST("/api/v1/users/{user_id}/reset-password", {
          params: { path: { user_id: userId } },
          body: { new_password: newPassword },
        }),
      ),
    meta: { silentError: true },
  });
}
