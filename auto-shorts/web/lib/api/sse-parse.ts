/**
 * Разбор SSE-кадров (docs/ARCHITECTURE.md §5.1): `event: <тип>\ndata: <JSON>\n\n`.
 * Источник идеи: retentionos/apps/web/lib/sse-parse.ts (скопировано 26.09.2026), дополнено
 * типами событий из контракта (`components["schemas"]["SseEvent"]`) и многострочным `data:`.
 *
 * Модуль без "@/"-импортов: `parseSseFrame` нужен и для fetch-стримов (живой лог задачи
 * `/tasks/{id}/log/stream`), и для проверки сообщений EventSource (`decodeSseMessage`).
 */
import type { components } from "./schema";

export type SseFrame = components["schemas"]["SseEvent"];
export type SseEventName = SseFrame["event"];
export type SseData<E extends SseEventName> = Extract<SseFrame, { event: E }>["data"];

/** Все типы событий `/api/v1/events/stream`, включая служебный `ping` (раз в 15 с). */
export const SSE_EVENT_NAMES = [
  "job_status",
  "job_progress",
  "task_progress",
  "review_queue",
  "packages",
  "alert",
  "worker",
  "budget",
  "source_status",
  "publication_metrics",
  "ping",
] as const satisfies readonly SseEventName[];

// Проверка полноты на этапе компиляции: если контракт добавит событие, здесь будет ошибка.
type MissingEvents = Exclude<SseEventName, (typeof SSE_EVENT_NAMES)[number]>;
const _exhaustive: MissingEvents extends never ? true : false = true;
void _exhaustive;

const KNOWN = new Set<string>(SSE_EVENT_NAMES);

export function isSseEventName(name: string): name is SseEventName {
  return KNOWN.has(name);
}

/**
 * Проверяет и типизирует одно сообщение EventSource: `event` должен быть из контракта,
 * `data` — JSON-объект. Иначе `null` (битый кадр не должен ронять обработчик).
 */
export function decodeSseMessage(event: string, data: string): SseFrame | null {
  if (!isSseEventName(event)) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(data);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
  return { event, data: parsed } as SseFrame;
}

export interface RawSseFrame {
  event: string;
  data: string;
  id?: string;
}

/**
 * Один кадр (всё между разделителями `\n\n`, без самого разделителя). Несколько строк `data:`
 * склеиваются через `\n` (так по спецификации SSE). Комментарии `:` пропускаются.
 * Кадр без `data:` → `null`.
 */
export function parseRawSseFrame(frame: string): RawSseFrame | null {
  let event = "message";
  let id: string | undefined;
  const data: string[] = [];
  for (const rawLine of frame.split(/\r?\n/)) {
    if (!rawLine || rawLine.startsWith(":")) continue;
    const colon = rawLine.indexOf(":");
    const field = colon === -1 ? rawLine : rawLine.slice(0, colon);
    let value = colon === -1 ? "" : rawLine.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value.trim();
    else if (field === "data") data.push(value);
    else if (field === "id") id = value;
  }
  if (data.length === 0) return null;
  return { event, data: data.join("\n"), id };
}

/** Кадр события из контракта или `null`. */
export function parseSseFrame(frame: string): SseFrame | null {
  const raw = parseRawSseFrame(frame);
  return raw ? decodeSseMessage(raw.event, raw.data) : null;
}

/** Делит накопленный буфер fetch-стрима на готовые кадры и хвост. */
export function splitSseBuffer(buffer: string): { frames: string[]; rest: string } {
  const normalized = buffer.replace(/\r\n/g, "\n");
  const parts = normalized.split("\n\n");
  const rest = parts.pop() ?? "";
  return { frames: parts.filter((part) => part.length > 0), rest };
}
