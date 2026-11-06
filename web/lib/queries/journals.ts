"use client";

import { type InfiniteData, useInfiniteQuery } from "@tanstack/react-query";

import { api, type Schemas, unwrap } from "@/lib/api/client";

/**
 * Журналы редиректора (`GET /clicks`, `GET /conversions`, viewer+): курсор `before_id`,
 * `next_before_id` в ответе — следующая страница. Боты и тестовые события по умолчанию скрыты.
 */
export type Click = Schemas["Click"];
export type ClickRoute = Schemas["ClickRoute"];
export type ClickJournal = Schemas["ClickJournal"];
export type Conversion = Schemas["Conversion"];
export type ConversionJournal = Schemas["ConversionJournal"];
export type ConversionEventType = Schemas["ConversionEventType"];
export type AttributionLevel = Schemas["AttributionLevel"];

export const JOURNAL_PAGE_SIZE = 50;

export interface ClickFilters {
  trackingLinkId?: number | null;
  publicationId?: number | null;
  productId?: string | null;
  channelId?: string | null;
  routes?: ClickRoute[] | null;
  includeBots?: boolean;
  limit?: number;
}

export interface ConversionFilters {
  productId?: string | null;
  trackingLinkId?: number | null;
  publicationId?: number | null;
  eventTypes?: ConversionEventType[] | null;
  includeTest?: boolean;
  limit?: number;
}

export const journalKeys = {
  clicks: (filters: ClickFilters) => ["clicks", filters] as const,
  conversions: (filters: ConversionFilters) => ["conversions", filters] as const,
};

export function useClicksJournal(filters: ClickFilters, { enabled = true }: { enabled?: boolean } = {}) {
  const limit = filters.limit ?? JOURNAL_PAGE_SIZE;
  return useInfiniteQuery({
    queryKey: journalKeys.clicks(filters),
    queryFn: ({ pageParam, signal }) =>
      unwrap(
        api.GET("/api/v1/clicks", {
          params: {
            query: {
              tracking_link_id: filters.trackingLinkId ?? undefined,
              publication_id: filters.publicationId ?? undefined,
              product_id: filters.productId || undefined,
              channel_id: filters.channelId || undefined,
              route: filters.routes?.length ? filters.routes : undefined,
              include_bots: filters.includeBots || undefined,
              before_id: pageParam ?? undefined,
              limit,
            },
          },
          signal,
        }),
      ),
    initialPageParam: null as number | null,
    getNextPageParam: (lastPage) => lastPage.next_before_id,
    enabled,
  });
}

export function useConversionsJournal(filters: ConversionFilters, { enabled = true }: { enabled?: boolean } = {}) {
  const limit = filters.limit ?? JOURNAL_PAGE_SIZE;
  return useInfiniteQuery({
    queryKey: journalKeys.conversions(filters),
    queryFn: ({ pageParam, signal }) =>
      unwrap(
        api.GET("/api/v1/conversions", {
          params: {
            query: {
              product_id: filters.productId || undefined,
              tracking_link_id: filters.trackingLinkId ?? undefined,
              publication_id: filters.publicationId ?? undefined,
              event_type: filters.eventTypes?.length ? filters.eventTypes : undefined,
              include_test: filters.includeTest || undefined,
              before_id: pageParam ?? undefined,
              limit,
            },
          },
          signal,
        }),
      ),
    initialPageParam: null as number | null,
    getNextPageParam: (lastPage) => lastPage.next_before_id,
    enabled,
  });
}

/** Склеить страницы журнала в один список. */
export function journalItems<T>(data: InfiniteData<{ items: T[] }> | undefined): T[] {
  return data?.pages.flatMap((page) => page.items) ?? [];
}
