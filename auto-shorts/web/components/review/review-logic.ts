/**
 * Чистая логика экрана приёмки (без React и DOM): клавиши, тексты конфликтов 409, выбор следующего
 * ролика, караоке и окна баннера. docs/ARCHITECTURE.md §6.2 (переходы, undo), §7.3 «Приёмка».
 */
import type { Schemas } from "@/lib/api/client";

type JobStatus = Schemas["JobStatus"];
type JobStage = Schemas["JobStage"];
type JobKind = Schemas["JobKind"];
type DecidedVia = Schemas["DecidedVia"];
type ReviewDecision = Schemas["ReviewDecision"];

/** Окно отмены решения на сервере (services/review.py UNDO_WINDOW_MIN). */
export const UNDO_WINDOW_MIN = 10;
/** Тост «Отменить» после решения (§7.3). */
export const UNDO_TOAST_MS = 6_000;
/** Окно хука на дорожке: первые секунды ролика, где решается, досмотрят ли. */
export const HOOK_WINDOW_SEC = 3;

export const DECISION_LABELS: Record<ReviewDecision, string> = {
  approve: "Одобрить",
  rework: "На доработку",
  reject: "Отклонить",
  override_qc: "Принять вопреки QC",
  undo: "Отмена решения",
};

/** Итог решения в прошедшем времени — для тостов и 409. */
export const DECISION_DONE_LABELS: Record<ReviewDecision, string> = {
  approve: "одобрен",
  rework: "отправлен на доработку",
  reject: "отклонён",
  override_qc: "принят вопреки QC",
  undo: "решение отменено",
};

export const DECIDED_VIA_LABELS: Record<DecidedVia, string> = {
  web: "в вебе",
  telegram: "в Telegram",
  api: "через API",
};

export const JOB_STATUS_LABELS: Record<JobStatus, string> = {
  draft: "Черновик",
  queued: "В очереди",
  processing: "В работе",
  qc_failed: "Не прошёл QC",
  pending_review: "Ждёт приёмки",
  rework: "На доработке",
  approved: "Одобрен",
  published: "Опубликован",
  rejected: "Отклонён",
  failed: "Сбой",
  cancelled: "Отменён",
};

export const REVIEWABLE_STATUSES: readonly JobStatus[] = ["pending_review", "qc_failed"];

export function isReviewable(status: JobStatus | null | undefined): status is "pending_review" | "qc_failed" {
  return status === "pending_review" || status === "qc_failed";
}

/** Порядок стадий трека (domain/enums.py GENERATE_STAGES, CLIP_STAGES). */
export const STAGES_BY_KIND: Record<JobKind, readonly JobStage[]> = {
  generate: ["script", "voice", "visuals", "render", "qc"],
  clip: ["clip_fetch", "clip_analyze", "author_script", "author_voice", "render", "qc"],
};

/** Стадия доработки по умолчанию (services/jobs.py DEFAULT_REWORK_STAGE). */
export const DEFAULT_REWORK_STAGE: Record<JobKind, JobStage> = {
  generate: "script",
  clip: "author_script",
};

// ---------------------------------------------------------------- 409

const timeFmt = new Intl.DateTimeFormat("ru-RU", { timeZone: "Europe/Moscow", hour: "2-digit", minute: "2-digit" });

function asString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function isDecidedVia(value: unknown): value is DecidedVia {
  return value === "web" || value === "telegram" || value === "api";
}

function isDecision(value: unknown): value is ReviewDecision {
  return typeof value === "string" && value in DECISION_DONE_LABELS;
}

function isJobStatus(value: unknown): value is JobStatus {
  return typeof value === "string" && value in JOB_STATUS_LABELS;
}

/** «Оператор смены, 12:03 МСК» — кто и когда решил (из `details` ответа 409). */
function whoAndWhen(details: Record<string, unknown>): string {
  const who = asString(details.decided_by);
  const atRaw = asString(details.decided_at);
  const at = atRaw && !Number.isNaN(Date.parse(atRaw)) ? `${timeFmt.format(new Date(atRaw))} МСК` : null;
  return [who, at].filter(Boolean).join(", ");
}

/** «одобрен в Telegram (Оператор смены, 12:03 МСК)» из `details` ответа 409 или null. */
export function describeDecision(details: Record<string, unknown>): string | null {
  const decision = isDecision(details.decision) ? details.decision : null;
  if (!decision) return null;
  const via = isDecidedVia(details.decided_via) ? ` ${DECIDED_VIA_LABELS[details.decided_via]}` : "";
  const tail = whoAndWhen(details);
  return `${DECISION_DONE_LABELS[decision]}${via}${tail ? ` (${tail})` : ""}`;
}

/**
 * 409 `job_state_conflict` на решении: кто-то (чаще — бот в Telegram) решил раньше.
 * Текст для оператора: что случилось, кем, где и когда; что его решение не применено.
 */
export function decisionConflictMessage(details: Record<string, unknown>, videoUid?: string | null): string {
  const subject = videoUid ? `Ролик ${videoUid}` : "Этот ролик";
  const decision = isDecision(details.decision) ? details.decision : null;
  if (decision) {
    const via = isDecidedVia(details.decided_via) ? ` ${DECIDED_VIA_LABELS[details.decided_via]}` : "";
    const tail = whoAndWhen(details);
    return `${subject} уже решён${via}: ${DECISION_DONE_LABELS[decision]}${tail ? ` (${tail})` : ""}. Ваше решение не применено.`;
  }
  const status = isJobStatus(details.status) ? JOB_STATUS_LABELS[details.status].toLowerCase() : null;
  return status
    ? `${subject} уже не ждёт приёмки: статус «${status}». Ваше решение не применено.`
    : `${subject} уже решён в другом окне или в Telegram. Ваше решение не применено.`;
}

export type UndoConflictReason =
  | "nothing_to_undo"
  | "status_changed"
  | "published"
  | "undo_window_expired"
  | "package_downloaded"
  | "rework_in_progress";

const UNDO_REASON_TEXT: Record<UndoConflictReason, string> = {
  nothing_to_undo: "отменять нечего: последнего решения нет или оно уже отменено",
  status_changed: "статус ролика уже изменился",
  published: "ролик уже опубликован",
  undo_window_expired: `прошло больше ${UNDO_WINDOW_MIN} минут`,
  package_downloaded: "пакет уже скачан для заливки",
  rework_in_progress: "доработка уже выполняется",
};

/** 409 на `/review/undo`: причина из `details.reason`. */
export function undoConflictMessage(details: Record<string, unknown>, videoUid?: string | null): string {
  const reason = typeof details.reason === "string" ? (details.reason as UndoConflictReason) : null;
  const text = reason && reason in UNDO_REASON_TEXT ? UNDO_REASON_TEXT[reason] : "решение уже нельзя отменить";
  const subject = videoUid ? ` ${videoUid}` : "";
  const decided = reason === "status_changed" ? describeDecision(details) : null;
  return `Отменить решение по ролику${subject} нельзя: ${text}${decided ? ` (последнее решение: ${decided})` : ""}.`;
}

// ---------------------------------------------------------------- клавиши

export type ReviewMode = "idle" | "reject" | "rework" | "override" | "help";

export type ReviewKeyAction =
  | { type: "approve" }
  | { type: "open_rework" }
  | { type: "open_reject" }
  | { type: "reject_with_hotkey"; hotkey: string }
  | { type: "rework_with_index"; index: number }
  | { type: "confirm" }
  | { type: "cancel_mode" }
  | { type: "undo" }
  | { type: "next" }
  | { type: "prev" }
  | { type: "play_pause" }
  | { type: "seek"; deltaSec: number }
  | { type: "frame"; delta: 1 | -1 }
  | { type: "toggle_dead_zones" }
  | { type: "toggle_platform" }
  | { type: "toggle_banner" }
  | { type: "fullscreen" }
  | { type: "toggle_loop" }
  | { type: "toggle_mute" }
  | { type: "volume"; delta: number }
  | { type: "help" };

export interface KeyLike {
  code: string;
  key: string;
  shiftKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
  altKey: boolean;
}

const DECISION_CODES = new Set(["KeyA", "KeyR", "KeyX", "KeyU", "KeyJ", "KeyK"]);

/** Цифра клавиши (верхний ряд или цифровой блок) — `"0"…"9"` или null. */
export function digitOf(event: Pick<KeyLike, "code" | "key">): string | null {
  const match = /^(?:Digit|Numpad)([0-9])$/.exec(event.code);
  if (match) return match[1];
  return /^[0-9]$/.test(event.key) ? event.key : null;
}

/**
 * Клавиша → действие приёмки (§7.3). Берётся `event.code` — физическая клавиша, поэтому A/R/X/U
 * работают и в русской раскладке (Ф/К/Ч/Г). Сочетания с Ctrl/Cmd/Alt не перехватываются.
 */
export function reviewKeyAction(event: KeyLike, mode: ReviewMode): ReviewKeyAction | null {
  if (event.ctrlKey || event.metaKey || event.altKey) return null;
  const { code } = event;

  if (code === "Escape") return mode === "idle" ? null : { type: "cancel_mode" };

  if (mode === "reject") {
    const digit = digitOf(event);
    if (digit !== null) return { type: "reject_with_hotkey", hotkey: digit };
    if (code === "KeyX") return { type: "cancel_mode" };
  }
  if (mode === "rework") {
    const digit = digitOf(event);
    if (digit !== null && digit !== "0") return { type: "rework_with_index", index: Number(digit) - 1 };
    if (code === "Enter" || code === "NumpadEnter") return { type: "confirm" };
    if (code === "KeyR") return { type: "cancel_mode" };
  }
  if (mode === "override") {
    if (code === "KeyA" || code === "Enter" || code === "NumpadEnter") return { type: "confirm" };
  }
  if (mode === "help" && (code === "Slash" || code === "KeyH")) return { type: "cancel_mode" };
  // В режиме выбора причины/стадии/подтверждения решения другие решения и переходы не срабатывают:
  // случайная A после X не должна одобрить ролик. Плеер и слои оверлея работают как обычно.
  if ((mode === "reject" || mode === "rework" || mode === "override") && DECISION_CODES.has(code)) return null;

  switch (code) {
    case "KeyA":
      return { type: "approve" };
    case "KeyR":
      return { type: "open_rework" };
    case "KeyX":
      return { type: "open_reject" };
    case "KeyU":
      return { type: "undo" };
    case "KeyJ":
      return { type: "next" };
    case "KeyK":
      return { type: "prev" };
    case "Space":
      return { type: "play_pause" };
    case "ArrowLeft":
      return { type: "seek", deltaSec: event.shiftKey ? -5 : -1 };
    case "ArrowRight":
      return { type: "seek", deltaSec: event.shiftKey ? 5 : 1 };
    case "Comma":
      return { type: "frame", delta: -1 };
    case "Period":
      return { type: "frame", delta: 1 };
    case "KeyS":
      return { type: "toggle_dead_zones" };
    case "KeyP":
      return { type: "toggle_platform" };
    case "KeyB":
      return { type: "toggle_banner" };
    case "KeyF":
      return { type: "fullscreen" };
    case "KeyL":
      return { type: "toggle_loop" };
    case "KeyM":
      return { type: "toggle_mute" };
    case "ArrowUp":
      return { type: "volume", delta: 0.1 };
    case "ArrowDown":
      return { type: "volume", delta: -0.1 };
    case "Slash":
      return event.shiftKey || event.key === "?" ? { type: "help" } : null;
    default:
      return null;
  }
}

/** Цель события — поле ввода: клавиши приёмки молчат, пока оператор печатает. */
export function isTypingTarget(target: EventTarget | null): boolean {
  if (!target || typeof (target as HTMLElement).tagName !== "string") return false;
  const element = target as HTMLElement;
  const tag = element.tagName.toUpperCase();
  if (tag === "TEXTAREA" || tag === "SELECT") return true;
  if (tag === "INPUT") {
    const type = ((element as HTMLInputElement).type || "text").toLowerCase();
    return !["checkbox", "radio", "button", "submit", "reset", "range"].includes(type);
  }
  return element.isContentEditable === true;
}

// ---------------------------------------------------------------- очередь

/** Какой ролик открыть после решения по `currentId`: следующий, иначе предыдущий, иначе ничего. */
export function nextAfter(ids: readonly number[], currentId: number): number | null {
  const index = ids.indexOf(currentId);
  if (index === -1) return ids[0] ?? null;
  return ids[index + 1] ?? ids[index - 1] ?? null;
}

/** Соседний ролик для J/K. */
export function neighbour(ids: readonly number[], currentId: number | null, delta: 1 | -1): number | null {
  if (ids.length === 0) return null;
  if (currentId === null) return ids[0];
  const index = ids.indexOf(currentId);
  if (index === -1) return ids[0];
  const next = index + delta;
  if (next < 0 || next >= ids.length) return null;
  return ids[next];
}

// ---------------------------------------------------------------- время

export interface TimedWord {
  word: string;
  start_sec: number;
  end_sec: number;
}

/** Индекс слова, звучащего в момент `t` (бинарный поиск по отсортированным словам), или -1. */
export function activeWordIndex(words: readonly TimedWord[], t: number): number {
  let lo = 0;
  let hi = words.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (words[mid].start_sec <= t) {
      found = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  if (found === -1) return -1;
  // Между словами (пауза) подсвечиваем последнее прозвучавшее ещё 0,25 с.
  return t <= words[found].end_sec + 0.25 ? found : -1;
}

export interface TimeWindow {
  start_sec: number;
  end_sec: number;
}

export function inWindow(windows: readonly TimeWindow[], t: number): boolean {
  return windows.some((w) => t >= w.start_sec && t < w.end_sec);
}

/** Таймкод `00:12.40` (§7.6: JetBrains Mono). */
export function formatTimecode(sec: number | null | undefined): string {
  if (sec === null || sec === undefined || !Number.isFinite(sec) || sec < 0) return "--:--.--";
  const total = Math.round(sec * 100);
  const minutes = Math.floor(total / 6000);
  const seconds = Math.floor((total % 6000) / 100);
  const hundredths = total % 100;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(hundredths).padStart(2, "0")}`;
}
