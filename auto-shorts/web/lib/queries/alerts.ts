"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { api, type Schemas, unwrap } from "@/lib/api/client";
import { useLiveRefetchInterval } from "@/lib/hooks/use-live-refetch";

import { queryKeys } from "./keys";

export type Alert = Schemas["Alert"];

/** Открытые алерты. Событие SSE `alert` инвалидирует этот запрос. */
export function useOpenAlerts() {
  const refetchInterval = useLiveRefetchInterval(60_000);
  return useQuery({
    queryKey: queryKeys.alerts("open"),
    queryFn: () => unwrap(api.GET("/api/v1/alerts", { params: { query: { status: ["open"] } } })),
    refetchInterval,
    select: (data) => data.items,
  });
}

export function useAckAlert() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (alertId: number) =>
      unwrap(api.POST("/api/v1/alerts/{alert_id}/ack", { params: { path: { alert_id: alertId } } })),
    onSuccess: () => {
      toast.success("Алерт принят");
      void queryClient.invalidateQueries({ queryKey: queryKeys.alertsAll });
    },
  });
}
