"use client";

import { useQuery } from "@tanstack/react-query";

import { api, type Schemas, unwrap } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import { useLiveRefetchInterval } from "@/lib/hooks/use-live-refetch";

import { queryKeys } from "./keys";

export type SystemStatus = Schemas["SystemStatus"];
export type ReadyResponse = Schemas["ReadyResponse"];

/** `/system/status`: воркеры, тяжёлый слот, диск, память, PSI, очередь, бюджет, версии. */
export function useSystemStatus({ slowWhenLive = 30_000 }: { slowWhenLive?: number | false } = {}) {
  const refetchInterval = useLiveRefetchInterval(slowWhenLive);
  return useQuery({
    queryKey: queryKeys.systemStatus,
    queryFn: () => unwrap(api.GET("/api/v1/system/status")),
    refetchInterval,
  });
}

/** `/health/ready`: 503 тоже несёт `ReadyResponse` — показываем проверки, а не голую ошибку. */
export function useReady() {
  const refetchInterval = useLiveRefetchInterval(30_000);
  return useQuery({
    queryKey: queryKeys.ready,
    queryFn: async (): Promise<ReadyResponse> => {
      const result = await api.GET("/api/v1/health/ready");
      if (result.response.ok && result.data) return result.data as ReadyResponse;
      const body = result.error as unknown;
      if (body && typeof body === "object" && "checks" in body && "status" in body) return body as ReadyResponse;
      return unwrap(Promise.resolve(result));
    },
    refetchInterval,
    retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 1,
  });
}
