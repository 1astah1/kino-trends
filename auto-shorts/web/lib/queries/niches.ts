"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, API_PREFIX, CSRF_HEADER, csrfToken, type Schemas, unwrap } from "@/lib/api/client";
import { networkError, toApiError } from "@/lib/api/errors";

import { channelKeys } from "./channels";

export type Niche = Schemas["Niche"];
export type NicheRow = Schemas["NicheRow"];
export type NicheStatus = Schemas["NicheStatus"];
export type NicheCreate = Schemas["NicheCreate"];
export type NichePatch = Schemas["NichePatch"];
export type NicheVersionPut = Schemas["NicheVersionPut"];
export type NicheValidateResponse = Schemas["NicheValidateResponse"];
export type NicheVersionItem = Schemas["NicheVersionItem"];
export type NicheVersionConfig = Schemas["NicheVersionConfig"];
export type NicheVersionSource = Schemas["NicheVersionSource"];
export type ConfigIssue = Schemas["ConfigIssue"];
/** Конфиг ниши для API — объект (`domain/niche_schema.NicheConfig`), в редакторе — YAML-текст. */
export type NicheConfig = Record<string, unknown>;
export type NicheImportMode = "create" | "replace";

export interface NicheFilters {
  status?: NicheStatus | null;
  limit?: number;
  offset?: number;
}

/**
 * Ключи. `all = ["niches"]` накрывает и `channelKeys.nicheOptions` (`["niches","options"]`): любое
 * изменение ниши перечитывает справочник в форме канала.
 */
export const nicheKeys = {
  all: ["niches"] as const,
  list: (filters: NicheFilters) => ["niches", "list", filters] as const,
  detail: (id: string) => ["niche", id] as const,
  versions: (id: string) => ["niche", id, "versions"] as const,
  version: (id: string, version: number) => ["niche", id, "version", version] as const,
};

/** Ссылка на экспорт YAML (`Content-Disposition: attachment; filename="<id>.yaml"`). */
export function nicheExportYamlUrl(id: string): string {
  return `${API_PREFIX}/niches/${encodeURIComponent(id)}/export-yaml`;
}

export function useNiches(filters: NicheFilters = {}, { enabled = true } = {}) {
  return useQuery({
    queryKey: nicheKeys.list(filters),
    queryFn: ({ signal }) =>
      unwrap(
        api.GET("/api/v1/niches", {
          params: {
            query: {
              status: filters.status ?? undefined,
              limit: filters.limit ?? 200,
              offset: filters.offset ?? 0,
            },
          },
          signal,
        }),
      ),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useNiche(id: string | null | undefined) {
  return useQuery({
    queryKey: nicheKeys.detail(id ?? ""),
    queryFn: ({ signal }) =>
      unwrap(api.GET("/api/v1/niches/{niche_id}", { params: { path: { niche_id: id ?? "" } }, signal })),
    enabled: !!id,
  });
}

export function useNicheVersions(id: string | null | undefined) {
  return useQuery({
    queryKey: nicheKeys.versions(id ?? ""),
    queryFn: ({ signal }) =>
      unwrap(api.GET("/api/v1/niches/{niche_id}/versions", { params: { path: { niche_id: id ?? "" } }, signal })),
    select: (data) => data.items,
    enabled: !!id,
  });
}

export function useNicheVersion(id: string | null | undefined, version: number | null | undefined) {
  return useQuery({
    queryKey: nicheKeys.version(id ?? "", version ?? 0),
    queryFn: ({ signal }) =>
      unwrap(
        api.GET("/api/v1/niches/{niche_id}/versions/{version}", {
          params: { path: { niche_id: id ?? "", version: version ?? 0 } },
          signal,
        }),
      ),
    enabled: !!id && version != null && version > 0,
    staleTime: Infinity,
  });
}

function invalidateNicheLists(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: nicheKeys.all });
  void queryClient.invalidateQueries({ queryKey: channelKeys.nicheOptions });
}

/** `POST /niches`: пустой конфиг (`{}` — значения по умолчанию подставит сервер) или `clone_from`. */
export function useCreateNiche() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: NicheCreate) => unwrap(api.POST("/api/v1/niches", { body })),
    meta: { silentError: true },
    onSuccess: (niche) => {
      queryClient.setQueryData(nicheKeys.detail(niche.id), niche);
      invalidateNicheLists(queryClient);
    },
  });
}

export interface ImportNicheYamlInput {
  file: File;
  mode: NicheImportMode;
}

/**
 * `POST /niches/import-yaml` (multipart `file`, `mode`). Как и загрузка креативов, идёт мимо
 * openapi-fetch: та же cookie-сессия и CSRF double-submit, ошибки — классы `lib/api/errors.ts`
 * (422 `details.errors[].loc=["body","file"]` — текст про сам файл, `details.config_errors` — по путям).
 */
export async function importNicheYaml({ file, mode }: ImportNicheYamlInput): Promise<Niche> {
  const form = new FormData();
  form.append("file", file, file.name);
  form.append("mode", mode);
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = csrfToken();
  if (token) headers[CSRF_HEADER] = token;

  let response: Response;
  try {
    response = await fetch(`${API_PREFIX}/niches/import-yaml`, {
      method: "POST",
      body: form,
      credentials: "include",
      headers,
    });
  } catch (cause) {
    throw networkError(cause);
  }
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  if (!response.ok) throw toApiError(response.status, body);
  return body as Niche;
}

export function useImportNicheYaml() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: importNicheYaml,
    meta: { silentError: true },
    onSuccess: (niche) => {
      queryClient.setQueryData(nicheKeys.detail(niche.id), niche);
      void queryClient.invalidateQueries({ queryKey: nicheKeys.versions(niche.id) });
      invalidateNicheLists(queryClient);
    },
  });
}

/** `PATCH /niches/{id}`: подпись и статус (active | archived). */
export function useUpdateNiche(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: NichePatch) =>
      unwrap(api.PATCH("/api/v1/niches/{niche_id}", { params: { path: { niche_id: id } }, body })),
    meta: { silentError: true },
    onSuccess: (niche) => {
      queryClient.setQueryData(nicheKeys.detail(id), niche);
      invalidateNicheLists(queryClient);
    },
  });
}

/**
 * `PUT /niches/{id}`: новая версия конфига. `expected_version` = `current_version` загруженной ниши;
 * 409 `version_conflict` (ConflictError, `details.current_version`) — нишу уже изменил кто-то другой;
 * 422 — `details.config_errors=[{path,msg}]`.
 */
export function usePutNicheConfig(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: NicheVersionPut) =>
      unwrap(api.PUT("/api/v1/niches/{niche_id}", { params: { path: { niche_id: id } }, body })),
    meta: { silentError: true },
    onSuccess: (niche) => {
      queryClient.setQueryData(nicheKeys.detail(id), niche);
      void queryClient.invalidateQueries({ queryKey: nicheKeys.versions(id) });
      invalidateNicheLists(queryClient);
    },
  });
}

/** `POST /niches/{id}/validate`: проверка без сохранения → `{ok, errors[{path,msg}], warnings[]}`. */
export function useValidateNiche(id: string) {
  return useMutation({
    mutationFn: (config: NicheConfig) =>
      unwrap(api.POST("/api/v1/niches/{niche_id}/validate", { params: { path: { niche_id: id } }, body: { config } })),
    meta: { silentError: true },
  });
}

/** Ошибки конфига по путям из 422 (`details.config_errors`), если сервер их прислал. */
export function configErrorsOf(error: unknown): ConfigIssue[] {
  if (!error || typeof error !== "object" || !("details" in error)) return [];
  const raw = (error as { details?: Record<string, unknown> }).details?.config_errors;
  if (!Array.isArray(raw)) return [];
  return raw.filter(
    (item): item is ConfigIssue =>
      !!item && typeof item === "object" && typeof (item as ConfigIssue).path === "string" && "msg" in item,
  );
}
