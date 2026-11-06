"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type Schemas, unwrap } from "@/lib/api/client";
import { packageKeys } from "@/lib/queries/packages";

/**
 * Публикации (docs/ARCHITECTURE.md §5.2 «…публикации…»): разбор ссылки, регистрация по вставленному
 * URL (одна транзакция на сервере: публикация, алиас `yt_`/`vk_`, пакет и ролик → published,
 * мастер в `masters/`), реестр для вкладки «Проблемы», повторная проверка принадлежности.
 */
export type Publication = Schemas["Publication"];
export type PublicationCreated = Schemas["PublicationCreated"];
export type ParsedUrl = Schemas["ParsedUrl"];
export type VerifyStatus = Schemas["VerifyStatus"];
export type PublicationStatus = Schemas["PublicationStatus"];

export interface PublicationFilters {
  verifyStatuses?: VerifyStatus[];
  statuses?: PublicationStatus[];
  channelId?: string | null;
  limit?: number;
}

export const publicationKeys = {
  all: ["publications"] as const,
  list: (filters: PublicationFilters) => ["publications", "list", filters] as const,
  detail: (id: number) => ["publication", id] as const,
};

/** Проверка принадлежности, которая требует внимания оператора (вкладка «Проблемы»). */
export const PROBLEM_VERIFY_STATUSES: VerifyStatus[] = ["channel_mismatch", "not_found", "unverifiable"];

export function usePublications(
  filters: PublicationFilters,
  { enabled = true, pollMs }: { enabled?: boolean; pollMs?: number } = {},
) {
  return useQuery({
    queryKey: publicationKeys.list(filters),
    queryFn: ({ signal }) =>
      unwrap(
        api.GET("/api/v1/publications", {
          params: {
            query: {
              verify_status: filters.verifyStatuses?.length ? filters.verifyStatuses : undefined,
              status: filters.statuses?.length ? filters.statuses : undefined,
              channel_id: filters.channelId ?? undefined,
              sort: "-published_at",
              limit: filters.limit ?? 100,
            },
          },
          signal,
        }),
      ),
    placeholderData: keepPreviousData,
    enabled,
    refetchInterval: pollMs ?? false,
  });
}

/** Серверный разбор ссылки (`POST /publications/parse-url`): 422 unsupported_url с details.reason. */
export function useParseUrl() {
  return useMutation({
    mutationFn: (url: string) => unwrap(api.POST("/api/v1/publications/parse-url", { body: { url } })),
    meta: { silentError: true },
  });
}

export interface RegisterPublicationInput {
  packageId: number;
  url: string;
  publishedAt?: string | null;
  /** Один ключ на попытку: повтор после обрыва связи вернёт тот же ответ, а не 409. */
  idempotencyKey: string;
}

/** Регистрация публикации по вставленной ссылке. Ошибки экран переводит сам (publish-errors.ts). */
export function useRegisterPublication() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ packageId, url, publishedAt, idempotencyKey }: RegisterPublicationInput) =>
      unwrap(
        api.POST("/api/v1/publications", {
          params: { header: { "Idempotency-Key": idempotencyKey } },
          body: { package_id: packageId, url, published_at: publishedAt ?? null },
        }),
      ),
    meta: { silentError: true },
    onSuccess: (created, { packageId }) => {
      queryClient.setQueryData(publicationKeys.detail(created.publication.id), created.publication);
      void queryClient.invalidateQueries({ queryKey: packageKeys.detail(packageId) });
      void queryClient.invalidateQueries({ queryKey: packageKeys.all });
      void queryClient.invalidateQueries({ queryKey: publicationKeys.all });
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}

/** Повторить проверку принадлежности каналу (YouTube — oEmbed в задаче `publication_verify`). */
export function useReverifyPublication() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      unwrap(api.POST("/api/v1/publications/{publication_id}/reverify", { params: { path: { publication_id: id } } })),
    onSuccess: (_task, id) => {
      void queryClient.invalidateQueries({ queryKey: publicationKeys.detail(id) });
      void queryClient.invalidateQueries({ queryKey: publicationKeys.all });
    },
  });
}

/**
 * Снять ролик с YouTube: сервер удаляет его на площадке токеном канала и ставит статус
 * `deleted_by_us`. Нужен scope youtube.force-ssl у подключения — иначе 409 `oauth_scope_missing`.
 */
export function useRemoveRemotePublication() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      unwrap(
        api.POST("/api/v1/publications/{publication_id}/remove-remote", { params: { path: { publication_id: id } } }),
      ),
    meta: { silentError: true },
    onSuccess: (_pub, id) => {
      void queryClient.invalidateQueries({ queryKey: publicationKeys.detail(id) });
      void queryClient.invalidateQueries({ queryKey: publicationKeys.all });
      void queryClient.invalidateQueries({ queryKey: ["packages"] });
    },
  });
}

/**
 * Мягкое удаление ошибочной регистрации (только admin): ролик → approved, пакет → ready,
 * алиас выключается — ту же или правильную ссылку можно вставить заново.
 */
export function useDeletePublication() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      unwrap(api.DELETE("/api/v1/publications/{publication_id}", { params: { path: { publication_id: id } } })),
    onSuccess: (_void, id) => {
      queryClient.removeQueries({ queryKey: publicationKeys.detail(id) });
      void queryClient.invalidateQueries({ queryKey: publicationKeys.all });
      void queryClient.invalidateQueries({ queryKey: packageKeys.all });
      void queryClient.invalidateQueries({ queryKey: ["package"] });
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}
