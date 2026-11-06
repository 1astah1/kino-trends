"use client";

import { type InfiniteData, useInfiniteQuery } from "@tanstack/react-query";

import { api, type Schemas, unwrap } from "@/lib/api/client";

/** Журнал аудита (`GET /audit-log`, operator+): курсор `before_id`, страница ≤ 200 записей. */
export type AuditEntry = Schemas["AuditEntry"];
export type AuditJournal = Schemas["AuditJournal"];
export type ActorKind = Schemas["ActorKind"];

export const AUDIT_PAGE_SIZE = 50;

export interface AuditFilters {
  actorUserId?: number | null;
  entityType?: string | null;
  entityId?: string | null;
  /** Точно (`secret.set`) или префиксом со звёздочкой (`user.*`). */
  action?: string | null;
  /** YYYY-MM-DD включительно, в бизнес-поясе. */
  dateFrom?: string | null;
  dateTo?: string | null;
  limit?: number;
}

export const auditKeys = {
  all: ["audit-log"] as const,
  list: (filters: AuditFilters) => ["audit-log", "list", filters] as const,
};

/** Бесконечный список: первая страница без курсора, следующая — `before_id = next_before_id`. */
export function useAuditLog(filters: AuditFilters, { enabled = true }: { enabled?: boolean } = {}) {
  const limit = filters.limit ?? AUDIT_PAGE_SIZE;
  return useInfiniteQuery({
    queryKey: auditKeys.list(filters),
    queryFn: ({ pageParam, signal }) =>
      unwrap(
        api.GET("/api/v1/audit-log", {
          params: {
            query: {
              actor_user_id: filters.actorUserId ?? undefined,
              entity_type: filters.entityType || undefined,
              entity_id: filters.entityId || undefined,
              action: filters.action || undefined,
              date_from: filters.dateFrom || undefined,
              date_to: filters.dateTo || undefined,
              before_id: pageParam ?? undefined,
              limit,
            },
          },
          signal,
        }),
      ),
    initialPageParam: null as number | null,
    // null от сервера = страниц больше нет (v5 трактует null/undefined как конец).
    getNextPageParam: (lastPage) => lastPage.next_before_id,
    enabled,
  });
}

/** Склеить страницы в один список записей. */
export function auditEntries(data: InfiniteData<AuditJournal> | undefined): AuditEntry[] {
  return data?.pages.flatMap((page) => page.items) ?? [];
}
