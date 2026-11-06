/**
 * Типизированный клиент API (docs/ARCHITECTURE.md §5.1, §7.4).
 *
 * - openapi-fetch по типам `schema.d.ts` (генерирует владелец контрактов, руками не править:
 *   `npm run gen:api`). Пути в схеме уже содержат `/api/v1`, поэтому baseUrl пустой: запросы идут
 *   на тот же origin (в dev — через rewrites next.config.ts, в проде — nginx).
 * - Cookie-сессия `as_session`: `credentials: "include"`.
 * - CSRF double-submit: каждая мутация (POST/PUT/PATCH/DELETE) несёт `X-CSRF-Token`, равный cookie
 *   `as_csrf`. Если cookie не читается из JS, берём значение из `GET /auth/me` / `POST /auth/login`.
 * - 401 на любом запросе, кроме проверки сессии и входа, сообщает подписчикам `onAuthLost`
 *   (AuthGate переводит на `/login?next=`).
 */
import createClient, { type Middleware } from "openapi-fetch";

import { networkError, toApiError } from "./errors";
import type { components, paths } from "./schema";

export type Schemas = components["schemas"];
export type { paths };

export const API_PREFIX = "/api/v1";
export const CSRF_COOKIE = "as_csrf";
export const CSRF_HEADER = "X-CSRF-Token";

const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);
/** 401 на этих путях — нормальный ответ «не вошёл», а не потеря сессии. */
const AUTH_PROBE_PATHS = new Set(["/api/v1/auth/me", "/api/v1/auth/login", "/api/v1/auth/logout"]);

let csrfFromAuth: string | null = null;

export function setCsrfToken(token: string | null | undefined) {
  csrfFromAuth = token ?? null;
}

export function readCookie(name: string, cookieString?: string): string | null {
  const source = cookieString ?? (typeof document === "undefined" ? "" : document.cookie);
  for (const part of source.split(";")) {
    const index = part.indexOf("=");
    if (index === -1) continue;
    if (part.slice(0, index).trim() !== name) continue;
    const value = part.slice(index + 1).trim();
    try {
      return decodeURIComponent(value);
    } catch {
      return value;
    }
  }
  return null;
}

/** Значение для `X-CSRF-Token`: cookie `as_csrf` (её сравнивает сервер), иначе токен из /auth/me. */
export function csrfToken(): string | null {
  return readCookie(CSRF_COOKIE) || csrfFromAuth;
}

type AuthLostListener = () => void;
const authLostListeners = new Set<AuthLostListener>();

export function onAuthLost(listener: AuthLostListener): () => void {
  authLostListeners.add(listener);
  return () => {
    authLostListeners.delete(listener);
  };
}

export const csrfMiddleware: Middleware = {
  onRequest({ request }) {
    if (UNSAFE_METHODS.has(request.method.toUpperCase())) {
      const token = csrfToken();
      if (token) request.headers.set(CSRF_HEADER, token);
    }
    if (!request.headers.has("Accept")) request.headers.set("Accept", "application/json");
    return request;
  },
  onResponse({ response, schemaPath }) {
    if (response.status === 401 && !AUTH_PROBE_PATHS.has(schemaPath)) {
      for (const listener of authLostListeners) listener();
    }
    return undefined;
  },
};

export const api = createClient<paths>({ baseUrl: "", credentials: "include" });
api.use(csrfMiddleware);

type ApiResult<T> =
  { data: T; error?: never; response: Response } | { data?: never; error: unknown; response: Response };

/**
 * Разворачивает ответ openapi-fetch: данные при 2xx, иначе класс ошибки из `errors.ts`
 * (401 → NotAuthenticatedError, 409 → ConflictError, 422 → ValidationError, 423, 507, …).
 * Сетевой сбой → ApiError со статусом 0. Отмена запроса (AbortError) пробрасывается как есть.
 */
export async function unwrap<T>(request: Promise<ApiResult<T>>): Promise<Exclude<T, undefined>> {
  let result: ApiResult<T>;
  try {
    result = await request;
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw networkError(cause);
  }
  if (!result.response.ok || result.error !== undefined) {
    throw toApiError(result.response.status, result.error);
  }
  // Для 204 openapi-fetch отдаёт `undefined`; у таких операций тип данных и так пустой.
  return result.data as Exclude<T, undefined>;
}
