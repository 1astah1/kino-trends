/**
 * Одно соединение `EventSource('/api/v1/events/stream')` на вкладку (docs/ARCHITECTURE.md §5.1, §7.4).
 *
 * Статусы для tally-точки в шапке:
 * - `connecting` — соединение ещё не открыто;
 * - `live` — события (или `ping` раз в 15 с) приходят;
 * - `polling` — событий нет дольше 45 с или соединение закрыто: видимые экраны переходят на
 *   `refetchInterval` 10 с (lib/hooks/use-live-refetch.ts), точка становится серой.
 *
 * Браузер сам переподключает EventSource после обрыва (`readyState = CONNECTING`). Если соединение
 * закрыто окончательно (`CLOSED`, например 401 или 5xx на открытии), переподключаемся сами
 * с нарастающей паузой 5 → 10 → 20 → 40 → 60 с.
 */
import { decodeSseMessage, SSE_EVENT_NAMES, type SseFrame } from "./sse-parse";

export const SSE_URL = "/api/v1/events/stream";
export const LIVE_TIMEOUT_MS = 45_000;
export const POLLING_INTERVAL_MS = 10_000;
const WATCHDOG_TICK_MS = 5_000;
const RECONNECT_STEPS_MS = [5_000, 10_000, 20_000, 40_000, 60_000];

export type LiveStatus = "connecting" | "live" | "polling";

type EventSourceCtor = new (url: string, init?: EventSourceInit) => EventSource;

export interface LiveConnectionOptions {
  onEvent: (frame: SseFrame) => void;
  onStatus: (status: LiveStatus) => void;
  url?: string;
  now?: () => number;
  EventSourceImpl?: EventSourceCtor;
  liveTimeoutMs?: number;
}

export interface LiveConnection {
  close: () => void;
  /** Время последнего события (мс), 0 — событий ещё не было. */
  lastEventAt: () => number;
}

export function connectLiveEvents(options: LiveConnectionOptions): LiveConnection {
  const now = options.now ?? (() => Date.now());
  const url = options.url ?? SSE_URL;
  const liveTimeout = options.liveTimeoutMs ?? LIVE_TIMEOUT_MS;
  const Impl: EventSourceCtor | undefined =
    options.EventSourceImpl ?? (typeof EventSource === "undefined" ? undefined : EventSource);

  let source: EventSource | null = null;
  let status: LiveStatus | null = null;
  let lastEventAt = 0;
  // Отсчёт «тишины»: от последнего события, а до первого — от старта соединения.
  let lastActivityAt = now();
  let reconnectAttempt = 0;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let closed = false;

  const setStatus = (next: LiveStatus) => {
    if (status === next) return;
    status = next;
    options.onStatus(next);
  };

  const markAlive = () => {
    lastEventAt = now();
    lastActivityAt = lastEventAt;
    reconnectAttempt = 0;
    setStatus("live");
  };

  const scheduleReconnect = () => {
    if (closed || reconnectTimer) return;
    const delay = RECONNECT_STEPS_MS[Math.min(reconnectAttempt, RECONNECT_STEPS_MS.length - 1)];
    reconnectAttempt += 1;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      open();
    }, delay);
  };

  function open() {
    if (closed) return;
    if (!Impl) {
      setStatus("polling");
      return;
    }
    source?.close();
    if (status === null) setStatus("connecting");
    const es = new Impl(url, { withCredentials: true });
    source = es;
    es.onopen = () => markAlive();
    es.onerror = () => {
      if (es.readyState === 2 /* CLOSED */) {
        setStatus("polling");
        scheduleReconnect();
      }
    };
    for (const name of SSE_EVENT_NAMES) {
      es.addEventListener(name, (event) => {
        markAlive();
        const frame = decodeSseMessage(name, (event as MessageEvent<string>).data ?? "{}");
        if (frame) options.onEvent(frame);
      });
    }
  }

  const watchdog = setInterval(() => {
    if (closed || status === "polling") return;
    if (now() - lastActivityAt > liveTimeout) setStatus("polling");
  }, WATCHDOG_TICK_MS);

  open();

  return {
    close() {
      closed = true;
      clearInterval(watchdog);
      if (reconnectTimer) clearTimeout(reconnectTimer);
      source?.close();
      source = null;
    },
    lastEventAt: () => lastEventAt,
  };
}
