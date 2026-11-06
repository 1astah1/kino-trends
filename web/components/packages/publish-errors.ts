/**
 * Ошибки пакетов и публикаций по-русски (контракт M3-A: details.reason / details.codes / errors[].type).
 * Сообщение сервера уже русское; здесь — более точный текст с тем, что делать оператору, и имена
 * каналов/площадок из details. Модуль без DOM и без "@/"-импортов значений.
 */
import { type ApiError, isApiError } from "@/lib/api/errors";
import { UNSUPPORTED_URL_MESSAGES, type UnsupportedUrlReason } from "@/lib/platform-url";

const PLATFORM_RU: Record<string, string> = { youtube: "YouTube", vk: "VK" };

function platformRu(value: unknown): string {
  return typeof value === "string" ? (PLATFORM_RU[value] ?? value) : "другую площадку";
}

const PACKAGE_NOT_READY: Record<string, string> = {
  job_not_approved: "Ролик ещё не одобрен в Приёмке — пакет собирается только для одобренного ролика.",
  no_final: "У ролика нет финального файла. Перезапустите рендер из Конвейера.",
  final_file_missing: "Финальный файл ролика не найден на диске (убран уборкой?). Пересоберите пакет.",
  originality_fail:
    "Отчёт об оригинальности провален, а источник чужой: такой ролик не заливаем. Доработайте авторский слой и пересоберите.",
  erid_missing:
    "Вариант баннера требует маркировку рекламы, а erid не задан. Впишите erid в варианте кампании и пересоберите пакет.",
  package_not_live: "Пакет уже опубликован или заменён новым. Обновите список.",
  package_superseded: "Пакет заменён более новым. Откройте актуальный пакет ролика.",
};

const RIGHTS_CODES: Record<string, string> = {
  rights_missing: "у источника нет записи о правах",
  rights_expired: "срок прав на источник истёк",
  attribution_required: "для CC BY и лицензий нужна строка атрибуции",
};

const VALIDATION_TYPES: Record<string, string> = {
  attribution_required: "Строка атрибуции источника обязана остаться в описании (чужие права, CC BY).",
  checklist_code_absent: "Такого пункта нет в чек-листе этого пакета.",
  hashtag_format: "Хэштег — «#слово» без пробелов.",
  string_too_long: "Слишком длинный текст.",
  string_too_short: "Поле не может быть пустым.",
};

function firstValidationType(error: ApiError): { type: string; msg: string } | null {
  const raw = error.details.errors;
  if (!Array.isArray(raw)) return null;
  for (const item of raw as Array<{ type?: unknown; msg?: unknown }>) {
    if (item && typeof item.type === "string")
      return { type: item.type, msg: typeof item.msg === "string" ? item.msg : "" };
  }
  return null;
}

/** Ошибка регистрации публикации (`POST /publications`) или разбора ссылки. */
export function publishErrorMessage(error: unknown): string {
  if (!isApiError(error)) return error instanceof Error && error.message ? error.message : "Неизвестная ошибка";
  const d = error.details;
  switch (error.code) {
    case "unsupported_url": {
      const reason = typeof d.reason === "string" ? (d.reason as UnsupportedUrlReason) : null;
      const text = reason && reason in UNSUPPORTED_URL_MESSAGES ? UNSUPPORTED_URL_MESSAGES[reason] : null;
      return text && reason !== "unsupported_host" ? text : error.message;
    }
    case "platform_mismatch":
      return `Ссылка ведёт на ${platformRu(d.url_platform)}, а канал пакета — на ${platformRu(d.channel_platform)}. Проверьте, в какой аккаунт залили ролик.`;
    case "publication_exists":
      return `Этот ролик площадки уже зарегистрирован${typeof d.publication_id === "number" ? ` (публикация #${d.publication_id})` : ""}. Повторно вставлять ссылку не нужно.`;
    case "same_platform_crosspost_forbidden":
      return `Этот ролик (или его клон, адаптация, нарезка того же момента) уже опубликован на ${platformRu(d.platform)}${typeof d.channel_id === "string" ? ` в канале ${d.channel_id}` : ""}. Один ролик — один канал площадки: удалите заливку и сделайте новый ролик.`;
    case "package_not_ready": {
      const reason = typeof d.reason === "string" ? d.reason : "";
      const base = PACKAGE_NOT_READY[reason] ?? error.message;
      if (reason === "originality_fail" && Array.isArray(d.fails) && d.fails.length) {
        return `${base} Провалы: ${(d.fails as unknown[]).map(String).join(", ")}.`;
      }
      return base;
    }
    case "rights_rejected": {
      const codes = Array.isArray(d.codes) ? (d.codes as unknown[]).map(String) : [];
      const parts = codes.map((code) => RIGHTS_CODES[code] ?? code);
      return parts.length ? `Права на источник не позволяют заливать ролик: ${parts.join("; ")}.` : error.message;
    }
    case "idempotency_in_progress":
      return "Регистрация по этой ссылке уже выполняется. Подождите пару секунд и обновите пакет.";
    case "validation_error": {
      const first = firstValidationType(error);
      if (first && VALIDATION_TYPES[first.type]) return VALIDATION_TYPES[first.type];
      return first?.msg || error.message;
    }
    default:
      if (error.status === 403) return "Недостаточно прав: регистрировать публикации могут оператор и владелец.";
      return error.message;
  }
}

/** 422 PATCH текстов → ошибка по полю (`title`, `description`, `hashtags`, `pinned_comment`). */
export function textsFieldErrors(error: unknown): Record<string, string> {
  if (!isApiError(error) || error.status !== 422) return {};
  const raw = error.details.errors;
  const out: Record<string, string> = {};
  if (!Array.isArray(raw)) return out;
  for (const item of raw as Array<{ loc?: unknown; msg?: unknown; type?: unknown }>) {
    if (!item || !Array.isArray(item.loc)) continue;
    const field = String(item.loc.filter((part) => part !== "body")[0] ?? "");
    if (!field || field in out) continue;
    const type = typeof item.type === "string" ? item.type : "";
    out[field] = VALIDATION_TYPES[type] && type !== "string_too_long" ? VALIDATION_TYPES[type] : String(item.msg ?? "");
  }
  return out;
}

const AUTOPUBLISH_CODES: Record<string, string> = {
  autopublish_in_progress: "Ролик уже заливается — дождитесь окончания заливки.",
  autopublish_done: "Ролик уже залит автоматически — повторно ставить не нужно.",
  autopublish_not_scheduled: "Пакет не стоит в очереди автозалива — снимать нечего.",
  autopublish_no_slot:
    "Свободного слота на 60 дней вперёд нет: увеличьте «Роликов в день» или добавьте слоты в плане канала (карточка канала).",
  autopublish_platform_unsupported: "Автозалив пока только для YouTube: VK Клипы заливаются вручную.",
  package_not_ready: "Пакет уже опубликован или заменён новым. Обновите список.",
};

/** Ошибка постановки/снятия автозалива (`POST|DELETE /packages/{id}/autopublish`). */
export function autopublishErrorMessage(error: unknown): string {
  if (!isApiError(error)) return error instanceof Error && error.message ? error.message : "Неизвестная ошибка";
  if (error.code === "autopublish_blocked") {
    const problems = autopublishProblems(error);
    return problems.length ? `Автозалив невозможен: ${problems.map((p) => p.message).join("; ")}.` : error.message;
  }
  if (AUTOPUBLISH_CODES[error.code]) return AUTOPUBLISH_CODES[error.code];
  if (error.code === "validation_error") return firstValidationType(error)?.msg || error.message;
  if (error.status === 403) return "Недостаточно прав: автозалив ставят оператор и владелец.";
  return error.message;
}

/** `details.problems` у 409 `autopublish_blocked`: `[{code, message}]`. */
export function autopublishProblems(error: unknown): Array<{ code: string; message: string }> {
  if (!isApiError(error) || error.code !== "autopublish_blocked" || !Array.isArray(error.details.problems)) return [];
  return (error.details.problems as Array<{ code?: unknown; message?: unknown }>)
    .filter((item) => item && typeof item.message === "string")
    .map((item) => ({ code: String(item.code ?? ""), message: String(item.message) }));
}
