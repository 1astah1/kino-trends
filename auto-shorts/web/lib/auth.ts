import type { Schemas } from "@/lib/api/client";

export type CurrentUser = Schemas["CurrentUser"];
export type Role = Schemas["Role"];

/**
 * Права ролей (§5.1): viewer — только чтение (кроме секретов, настроек и пользователей);
 * operator — контент и каталог; admin — всё, плюс пользователи, секреты, провайдеры, бюджет.
 * UI лишь прячет недоступные действия; проверяет права сервер.
 */
export function canOperate(role: Role | null | undefined): boolean {
  return role === "operator" || role === "admin";
}

export function isAdmin(role: Role | null | undefined): boolean {
  return role === "admin";
}

/**
 * Безопасный `next` после входа: только относительный путь этого сайта.
 * Отсекаем `//evil`, `/\evil`, схемы и возврат на сам /login.
 */
export function safeNextPath(next: string | null | undefined): string {
  // Значение приходит уже раскодированным из URLSearchParams.
  if (!next) return "/";
  const value = next.trim();
  if (!value.startsWith("/") || value.startsWith("//") || value.startsWith("/\\")) return "/";
  if (/[\u0000-\u001f]/.test(value)) return "/";
  const path = value.split(/[?#]/, 1)[0].replace(/\/+$/, "");
  if (path === "/login") return "/";
  return value;
}

/** `next` для редиректа на вход: текущий путь (в каноничной форме экспорта, со слэшем) с query. */
export function buildNextParam(pathname: string, search: string): string {
  const path = pathname.endsWith("/") ? pathname : `${pathname}/`;
  const query = search && search !== "?" ? (search.startsWith("?") ? search : `?${search}`) : "";
  return `${path}${query}`;
}

export function loginHref(next?: string): string {
  const target = next ? safeNextPath(next) : "/";
  return target === "/" ? "/login/" : `/login/?next=${encodeURIComponent(target)}`;
}
