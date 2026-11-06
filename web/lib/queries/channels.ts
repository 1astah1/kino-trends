"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type Schemas, unwrap } from "@/lib/api/client";

import { providerKeys } from "./providers";

export type Channel = Schemas["Channel"];
export type ChannelCreate = Schemas["ChannelCreate"];
export type ChannelPatch = Schemas["ChannelPatch"];
export type ChannelStatus = Schemas["ChannelStatus"];
export type ChannelHealth = Schemas["ChannelHealth"];
export type ChannelIncident = Schemas["ChannelIncident"];
export type ChannelIncidentCreate = Schemas["ChannelIncidentCreate"];
export type IncidentKind = Schemas["IncidentKind"];
export type Platform = Schemas["Platform"];
export type NicheRow = Schemas["NicheRow"];
export type Cluster = Schemas["Cluster"];
export type YoutubeConnection = Schemas["YoutubeConnection"];

export interface ChannelFilters {
  platform?: Platform | null;
  status?: ChannelStatus[] | null;
  nicheId?: string | null;
  clusterId?: string | null;
}

export const channelKeys = {
  all: ["channels"] as const,
  list: (filters: ChannelFilters) => ["channels", filters] as const,
  detail: (id: string) => ["channel", id] as const,
  health: (id: string) => ["channel", id, "health"] as const,
  incidents: (id: string) => ["channel", id, "incidents"] as const,
  youtubeAll: ["youtube-oauth"] as const,
  youtube: (id: string) => ["youtube-oauth", id] as const,
  nicheOptions: ["niches", "options"] as const,
  clusters: ["clusters"] as const,
};

export function useChannels(filters: ChannelFilters = {}) {
  return useQuery({
    queryKey: channelKeys.list(filters),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/channels", {
          params: {
            query: {
              platform: filters.platform ?? undefined,
              status: filters.status?.length ? filters.status : undefined,
              niche_id: filters.nicheId ?? undefined,
              cluster_id: filters.clusterId ?? undefined,
              limit: 200,
            },
          },
        }),
      ),
    placeholderData: keepPreviousData,
  });
}

export function useChannel(id: string | null | undefined) {
  return useQuery({
    queryKey: channelKeys.detail(id ?? ""),
    queryFn: () => unwrap(api.GET("/api/v1/channels/{channel_id}", { params: { path: { channel_id: id ?? "" } } })),
    enabled: !!id,
  });
}

export function useChannelHealth(id: string | null | undefined) {
  return useQuery({
    queryKey: channelKeys.health(id ?? ""),
    queryFn: () =>
      unwrap(api.GET("/api/v1/channels/{channel_id}/health", { params: { path: { channel_id: id ?? "" } } })),
    enabled: !!id,
  });
}

export function useChannelIncidents(id: string | null | undefined) {
  return useQuery({
    queryKey: channelKeys.incidents(id ?? ""),
    queryFn: () =>
      unwrap(api.GET("/api/v1/channels/{channel_id}/incidents", { params: { path: { channel_id: id ?? "" } } })),
    select: (data) => data.items,
    enabled: !!id,
  });
}

export function useCreateChannel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ChannelCreate) => unwrap(api.POST("/api/v1/channels", { body })),
    meta: { silentError: true },
    onSuccess: (channel) => {
      queryClient.setQueryData(channelKeys.detail(channel.id), channel);
      void queryClient.invalidateQueries({ queryKey: channelKeys.all });
    },
  });
}

export function useUpdateChannel(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ChannelPatch) =>
      unwrap(api.PATCH("/api/v1/channels/{channel_id}", { params: { path: { channel_id: id } }, body })),
    meta: { silentError: true },
    onSuccess: (channel) => {
      queryClient.setQueryData(channelKeys.detail(id), channel);
      void queryClient.invalidateQueries({ queryKey: channelKeys.all });
      void queryClient.invalidateQueries({ queryKey: channelKeys.health(id) });
      void queryClient.invalidateQueries({ queryKey: channelKeys.youtube(id) });
    },
  });
}

export function useCreateIncident(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ChannelIncidentCreate) =>
      unwrap(api.POST("/api/v1/channels/{channel_id}/incidents", { params: { path: { channel_id: id } }, body })),
    meta: { silentError: true },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: channelKeys.incidents(id) });
      void queryClient.invalidateQueries({ queryKey: channelKeys.health(id) });
    },
  });
}

// ---------------------------------------------------------------- YouTube: OAuth и план автозалива

/** Подключение канала к YouTube (OAuth-клиент, токен, право загрузки) и план автозалива. */
export function useYoutubeConnection(id: string | null | undefined) {
  return useQuery({
    queryKey: channelKeys.youtube(id ?? ""),
    queryFn: () =>
      unwrap(api.GET("/api/v1/channels/{channel_id}/oauth/youtube", { params: { path: { channel_id: id ?? "" } } })),
    enabled: !!id,
  });
}

/** Ссылка на экран согласия Google. 409 `oauth_client_missing` (+ `details.secret_name`, `redirect_uri`) — нет клиента. */
export function useStartYoutubeOAuth(id: string) {
  return useMutation({
    mutationFn: () =>
      unwrap(api.GET("/api/v1/channels/{channel_id}/oauth/youtube/start", { params: { path: { channel_id: id } } })),
    meta: { silentError: true },
  });
}

/** Отключить канал от YouTube: токен удаляется (admin). */
export function useDisconnectYoutube(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(api.DELETE("/api/v1/channels/{channel_id}/oauth/youtube", { params: { path: { channel_id: id } } })),
    meta: { silentError: true },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: channelKeys.youtube(id) }),
  });
}

/**
 * JSON OAuth-клиента Google → секрет (admin). Клиент общий для куста или для всех каналов, поэтому
 * перечитываются подключения всех карточек.
 */
export function useWriteYoutubeClient() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ name, value }: { name: string; value: string }) =>
      unwrap(api.PUT("/api/v1/secrets/{secret_name}", { params: { path: { secret_name: name } }, body: { value } })),
    meta: { silentError: true },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: channelKeys.youtubeAll });
      void queryClient.invalidateQueries({ queryKey: providerKeys.secrets });
    },
  });
}

// ---------------------------------------------------------------- справочники формы канала

/** Ниши для выбора в канале (без архивных не фильтруем: канал может стоять на архивной). */
export function useNicheOptions() {
  return useQuery({
    queryKey: channelKeys.nicheOptions,
    queryFn: () => unwrap(api.GET("/api/v1/niches", { params: { query: { limit: 200 } } })),
    select: (data) => data.items,
    staleTime: 60_000,
  });
}

/** Кусты изоляции (свой Cloud-проект и прокси на куст). */
export function useClusters() {
  return useQuery({
    queryKey: channelKeys.clusters,
    queryFn: () => unwrap(api.GET("/api/v1/clusters")),
    select: (data) => data.items,
    staleTime: 60_000,
  });
}

export function useCreateCluster() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["ClusterCreate"]) => unwrap(api.POST("/api/v1/clusters", { body })),
    meta: { silentError: true },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: channelKeys.clusters }),
  });
}
