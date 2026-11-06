/**
 * Медиа (docs/ARCHITECTURE.md §5.1 «Медиа», §7.4, §7.7).
 *
 * URL медиа берутся **только** из ответов API (`media.preview_url`, `file.download_url`, …) —
 * фронт путей не собирает. Сервер отдаёт непрозрачные адреса `/api/v1/...` с поддержкой Range
 * (в проде за ними `X-Accel-Redirect`). Всё прочее (внешние адреса, протокол-относительные `//host`,
 * пути файловой системы) отбрасывается: у оператора из РФ внешний ресурс = пустой плеер,
 * а CSP `media-src 'self' blob:` его всё равно заблокирует.
 */

export const MEDIA_PREFIX = "/api/v1/";

/** Адрес медиа из ответа API: тот же origin, под `/api/v1/`, без схемы и `..`. */
export function isApiMediaUrl(url: string | null | undefined): url is string {
  if (!url) return false;
  if (!url.startsWith(MEDIA_PREFIX)) return false;
  if (url.startsWith("//") || url.includes("\\")) return false;
  const path = url.split(/[?#]/, 1)[0];
  return !path.split("/").some((segment) => segment === ".." || segment === ".");
}

/** Локальные объекты (превью записи голоса, выбранный файл) — `blob:`; `data:` — только картинки. */
export function isLocalObjectUrl(url: string | null | undefined): url is string {
  return !!url && (url.startsWith("blob:") || url.startsWith("data:image/"));
}

/** `src` для `<video>/<img>/<audio>` или `undefined`, если адрес не из API. */
export function mediaSrc(url: string | null | undefined): string | undefined {
  if (isApiMediaUrl(url) || isLocalObjectUrl(url)) return url;
  if (url && process.env.NODE_ENV !== "production") {
    console.warn("[media] отброшен адрес не из API:", url);
  }
  return undefined;
}

/** Ссылка «Скачать»: тот же адрес с `download=1` (сервер ставит attachment и латинское имя). */
export function downloadHref(url: string | null | undefined): string | undefined {
  const src = isApiMediaUrl(url) ? url : undefined;
  if (!src) return undefined;
  const [base, hash = ""] = src.split("#", 2);
  const separator = base.includes("?") ? "&" : "?";
  return `${base}${separator}download=1${hash ? `#${hash}` : ""}`;
}
