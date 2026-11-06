/**
 * Семантика ошибок API (docs/ARCHITECTURE.md §5.1, §7.4). Идея взята из
 * retentionos/apps/web/lib/api-client.ts (ApiError со статусом), формат тела —
 * `{"error":{"code","message","details"}}`, для 422 — `details.errors=[{loc,msg,type}]`.
 *
 * Модуль без "@/"-импортов и без DOM: его можно проверять отдельно.
 */

export interface ApiErrorBody {
  code: string;
  message: string;
  details?: Record<string, unknown>;
}

export interface ValidationIssue {
  loc: Array<string | number>;
  msg: string;
  type?: string;
}

const DEFAULT_MESSAGES: Record<number, string> = {
  0: "Нет связи с сервером",
  400: "Некорректный запрос",
  401: "Нужно войти",
  403: "Недостаточно прав",
  404: "Не найдено",
  409: "Данные уже изменились. Перечитайте и повторите",
  413: "Слишком большой запрос",
  415: "Неподдерживаемый формат",
  422: "Проверьте поля формы",
  423: "Платные шаги на паузе: бюджет",
  429: "Слишком много запросов. Подождите немного",
  501: "Ещё не реализовано на сервере",
  503: "Сервер не готов",
  507: "Нет места на диске",
};

export function defaultMessage(status: number): string {
  return DEFAULT_MESSAGES[status] ?? (status >= 500 ? "Ошибка сервера" : `Ошибка запроса (${status})`);
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>;

  constructor(status: number, body?: Partial<ApiErrorBody> | null) {
    super(body?.message || defaultMessage(status));
    this.name = "ApiError";
    this.status = status;
    this.code = body?.code || (status === 0 ? "network_error" : `http_${status}`);
    this.details = body?.details ?? {};
  }

  /** 422: `details.errors[].loc` → имя поля (последний сегмент после `body`) → сообщение. */
  get fieldErrors(): Record<string, string> {
    const raw = this.details.errors;
    if (!Array.isArray(raw)) return {};
    const out: Record<string, string> = {};
    for (const item of raw as ValidationIssue[]) {
      if (!item || !Array.isArray(item.loc)) continue;
      const path = item.loc.filter((part) => part !== "body" && part !== "query" && part !== "path");
      const field = path.map(String).join(".");
      if (field && !(field in out)) out[field] = item.msg;
    }
    return out;
  }
}

/** 401 — сессии нет или она истекла. */
export class NotAuthenticatedError extends ApiError {
  constructor(body?: Partial<ApiErrorBody> | null) {
    super(401, body);
    this.name = "NotAuthenticatedError";
  }
}

/** 409 — `job_state_conflict`, `version_conflict`, … → тост и перечитывание. */
export class ConflictError extends ApiError {
  constructor(body?: Partial<ApiErrorBody> | null) {
    super(409, body);
    this.name = "ConflictError";
  }
}

/** 422 — ошибки по полям формы. */
export class ValidationError extends ApiError {
  constructor(body?: Partial<ApiErrorBody> | null) {
    super(422, body);
    this.name = "ValidationError";
  }
}

/** 423 `budget_paused` — «платные шаги на паузе». */
export class BudgetPausedError extends ApiError {
  constructor(body?: Partial<ApiErrorBody> | null) {
    super(423, body);
    this.name = "BudgetPausedError";
  }
}

/** 507 `insufficient_storage` — «нет места на диске». */
export class InsufficientStorageError extends ApiError {
  constructor(body?: Partial<ApiErrorBody> | null) {
    super(507, body);
    this.name = "InsufficientStorageError";
  }
}

function extractBody(raw: unknown): Partial<ApiErrorBody> | null {
  if (!raw || typeof raw !== "object") return null;
  const error = (raw as { error?: unknown }).error;
  if (!error || typeof error !== "object") return null;
  const { code, message, details } = error as Record<string, unknown>;
  return {
    code: typeof code === "string" ? code : undefined,
    message: typeof message === "string" ? message : undefined,
    details: details && typeof details === "object" ? (details as Record<string, unknown>) : undefined,
  };
}

/** Превращает тело ошибки openapi-fetch и статус ответа в класс ошибки по §7.4. */
export function toApiError(status: number, raw: unknown): ApiError {
  const body = extractBody(raw);
  switch (status) {
    case 401:
      return new NotAuthenticatedError(body);
    case 409:
      return new ConflictError(body);
    case 422:
      return new ValidationError(body);
    case 423:
      return new BudgetPausedError(body);
    case 507:
      return new InsufficientStorageError(body);
    default:
      return new ApiError(status, body);
  }
}

export function networkError(cause?: unknown): ApiError {
  const error = new ApiError(0, null);
  if (cause !== undefined) (error as Error & { cause?: unknown }).cause = cause;
  return error;
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error && error.message) return error.message;
  return "Неизвестная ошибка";
}
