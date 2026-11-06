import { expect, test } from "@playwright/test";

import { connectLiveEvents, LIVE_TIMEOUT_MS, type LiveStatus } from "@/lib/api/sse";
import { decodeSseMessage, parseSseFrame, splitSseBuffer, SSE_EVENT_NAMES } from "@/lib/api/sse-parse";

test.describe("sse-parse", () => {
  test("кадр события из контракта", () => {
    const frame = parseSseFrame('event: budget\ndata: {"spent_usd":1.5,"reserved_usd":0,"limit_usd":5,"paused":false}');
    expect(frame).toEqual({ event: "budget", data: { spent_usd: 1.5, reserved_usd: 0, limit_usd: 5, paused: false } });
  });

  test("многострочный data склеивается через перевод строки, комментарии пропускаются", () => {
    const frame = parseSseFrame(': keep-alive\nevent: review_queue\ndata: {"count":\ndata: 3}');
    expect(frame).toEqual({ event: "review_queue", data: { count: 3 } });
  });

  test("неизвестное событие, битый JSON и кадр без data → null", () => {
    expect(parseSseFrame('event: job.updated\ndata: {"job_id":1}')).toBeNull();
    expect(parseSseFrame("event: budget\ndata: {oops")).toBeNull();
    expect(parseSseFrame("event: ping")).toBeNull();
    expect(decodeSseMessage("budget", "[1,2]")).toBeNull();
  });

  test("буфер fetch-стрима режется по пустой строке, хвост остаётся", () => {
    const { frames, rest } = splitSseBuffer("event: ping\ndata: {}\n\nevent: budget\r\ndata: {}\r\n\r\nevent: al");
    expect(frames).toEqual(["event: ping\ndata: {}", "event: budget\ndata: {}"]);
    expect(rest).toBe("event: al");
  });

  test("все имена событий латиницей snake_case (§5.1)", () => {
    for (const name of SSE_EVENT_NAMES) expect(name).toMatch(/^[a-z0-9_]+$/);
    expect(SSE_EVENT_NAMES).toContain("ping");
  });
});

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  listeners = new Map<string, Array<(event: MessageEvent<string>) => void>>();
  closed = false;
  constructor(
    public url: string,
    public init?: EventSourceInit,
  ) {
    FakeEventSource.instances.push(this);
  }
  addEventListener(name: string, fn: (event: MessageEvent<string>) => void) {
    this.listeners.set(name, [...(this.listeners.get(name) ?? []), fn]);
  }
  emit(name: string, data: string) {
    for (const fn of this.listeners.get(name) ?? []) fn({ data } as MessageEvent<string>);
  }
  close() {
    this.closed = true;
    this.readyState = 2;
  }
}

test.describe("connectLiveEvents", () => {
  test("connecting → live → polling после 45 с тишины → live на следующем событии", async () => {
    FakeEventSource.instances = [];
    let now = 1_000_000;
    const statuses: LiveStatus[] = [];
    const events: string[] = [];
    const originalSetInterval = globalThis.setInterval;
    const watchdog: { tick?: () => void } = {};
    // Ручной таймер сторожа: управляем «тишиной» без реального ожидания.
    globalThis.setInterval = ((fn: () => void) => {
      watchdog.tick = fn;
      return 1 as unknown as ReturnType<typeof setInterval>;
    }) as typeof setInterval;
    try {
      const connection = connectLiveEvents({
        EventSourceImpl: FakeEventSource as unknown as typeof EventSource,
        now: () => now,
        onStatus: (status) => statuses.push(status),
        onEvent: (frame) => events.push(frame.event),
      });
      const es = FakeEventSource.instances[0];
      expect(es.url).toBe("/api/v1/events/stream");
      expect(es.init).toEqual({ withCredentials: true });
      expect(statuses).toEqual(["connecting"]);

      es.readyState = 1;
      es.onopen?.();
      expect(statuses.at(-1)).toBe("live");

      es.emit("alert", '{"alert_id":1,"code":"disk_low","severity":"warning"}');
      expect(events).toEqual(["alert"]);

      now += LIVE_TIMEOUT_MS + 1;
      watchdog.tick?.();
      expect(statuses.at(-1)).toBe("polling");

      es.emit("ping", "{}");
      expect(statuses.at(-1)).toBe("live");
      expect(events).toEqual(["alert", "ping"]);

      connection.close();
      expect(es.closed).toBe(true);
    } finally {
      globalThis.setInterval = originalSetInterval;
    }
  });

  test("закрытое соединение (401/5xx на открытии) → polling", () => {
    FakeEventSource.instances = [];
    const statuses: LiveStatus[] = [];
    const connection = connectLiveEvents({
      EventSourceImpl: FakeEventSource as unknown as typeof EventSource,
      onStatus: (status) => statuses.push(status),
      onEvent: () => undefined,
    });
    const es = FakeEventSource.instances[0];
    es.readyState = 2;
    es.onerror?.();
    expect(statuses).toEqual(["connecting", "polling"]);
    connection.close();
  });
});
