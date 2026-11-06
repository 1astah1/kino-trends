"use client";

import { useQuery } from "@tanstack/react-query";

import { api, type Schemas, unwrap } from "@/lib/api/client";

export type BannerSafeZoneCatalog = Schemas["BannerSafeZoneCatalog"];

export const safeZoneKeys = {
  catalog: ["banner-safe-zones"] as const,
};

/**
 * Профили безопасных зон площадок (`GET /banner-safe-zones` ← config/safe_zones.json).
 * Меняются редко (правкой файла на сервере): держим час и не перечитываем по фокусу.
 */
export function useBannerSafeZones() {
  return useQuery({
    queryKey: safeZoneKeys.catalog,
    queryFn: () => unwrap(api.GET("/api/v1/banner-safe-zones")),
    staleTime: 60 * 60_000,
    refetchOnWindowFocus: false,
  });
}
