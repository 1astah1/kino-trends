import { expect, test } from "@playwright/test";

import { buildNextParam, loginHref, safeNextPath } from "@/lib/auth";
import {
  breadcrumbsFor,
  NAV_GROUPS,
  NAV_ROUTES,
  resolveNavRoute,
  routeHref,
  SETTINGS_TABS,
  SIDEBAR_ROUTES,
} from "@/lib/nav";
import { resolvePaletteQuery } from "@/lib/palette";
import { deriveWorkerState, type SystemStatus } from "@/lib/worker-state";

test.describe("вход: next без открытого редиректа", () => {
  test("чужие и опасные адреса → /", () => {
    for (const bad of [
      "https://evil.example",
      "//evil.example",
      "/\\evil.example",
      "javascript:alert(1)",
      "/login/",
      "/login?next=/x",
      "",
    ]) {
      expect(safeNextPath(bad)).toBe("/");
    }
    expect(safeNextPath("/settings/?tab=users")).toBe("/settings/?tab=users");
  });

  test("next строится в каноничной форме со слэшем", () => {
    expect(buildNextParam("/settings", "?tab=system")).toBe("/settings/?tab=system");
    expect(loginHref("/settings/?tab=system")).toBe("/login/?next=%2Fsettings%2F%3Ftab%3Dsystem");
    expect(loginHref("/")).toBe("/login/");
  });
});

test.describe("карта маршрутов (§7.3, §10.1 п. 2)", () => {
  test("все экраны v1 на месте, пути и параметры латиницей", () => {
    const paths = NAV_ROUTES.map((route) => route.path);
    for (const path of [
      "/",
      "/production",
      "/review",
      "/packages",
      "/clipping",
      "/clipping/source",
      "/trends",
      "/products",
      "/products/view",
      "/products/banner",
      "/analytics",
      "/analytics/video",
      "/niches",
      "/niches/edit",
      "/channels",
      "/settings",
    ]) {
      expect(paths).toContain(path);
    }
    for (const route of NAV_ROUTES) {
      expect(route.path).toMatch(/^\/[a-z0-9/-]*$/);
      for (const key of route.urlState) expect(key).toMatch(/^[a-z0-9_]+$/);
      expect(NAV_GROUPS.map((g) => g.id)).toContain(route.group);
    }
    for (const tab of SETTINGS_TABS) expect(tab.id).toMatch(/^[a-z0-9_]+$/);
    expect(SIDEBAR_ROUTES.every((route) => !route.parent)).toBe(true);
  });

  test("самый длинный путь побеждает, крошки с родителем", () => {
    expect(resolveNavRoute("/clipping/source/")?.path).toBe("/clipping/source");
    expect(resolveNavRoute("/clipping/")?.path).toBe("/clipping");
    expect(resolveNavRoute("/")?.path).toBe("/");
    expect(breadcrumbsFor("/products/banner/").map((c) => c.label)).toEqual([
      "Продвижение",
      "Продукты и баннеры",
      "Редактор баннера",
    ]);
    expect(routeHref("/settings", { tab: "users", empty: "" })).toBe("/settings/?tab=users");
  });
});

test.describe("палитра Ctrl+K", () => {
  test("video_uid, алиасы публикаций, код пакета, канал", () => {
    expect(resolvePaletteQuery("a1b2c3d4e5f6").map((t) => t.href)).toEqual(["/production/?q=a1b2c3d4e5f6"]);
    expect(resolvePaletteQuery("yt_dQw4w9WgXcQ")[0]?.href).toBe("/analytics/?tab=videos&q=yt_dQw4w9WgXcQ");
    expect(resolvePaletteQuery("vk_-12345_678")[0]?.label).toBe("Публикация vk_-12345_678");
    expect(resolvePaletteQuery("k7m2q").map((t) => t.id)).toEqual(["package:k7m2q", "channel:k7m2q"]);
    expect(resolvePaletteQuery("taro_main").map((t) => t.href)).toEqual(["/channels/?channel=taro_main"]);
    expect(resolvePaletteQuery("  ")).toEqual([]);
  });
});

function status(overrides: Partial<SystemStatus> = {}, now = Date.parse("2026-09-26T12:00:00Z")): SystemStatus {
  const heartbeat = new Date(now - 5000).toISOString();
  return {
    workers: [
      {
        id: "h",
        resource_class: "heavy",
        hostname: "b",
        pid: 1,
        version: null,
        started_at: heartbeat,
        heartbeat_at: heartbeat,
        current_task_id: null,
        state: "idle",
      },
      {
        id: "l",
        resource_class: "light",
        hostname: "b",
        pid: 2,
        version: null,
        started_at: heartbeat,
        heartbeat_at: heartbeat,
        current_task_id: null,
        state: "idle",
      },
    ],
    heavy_slot: { owner: null, task_id: null, stage: null, pct: null },
    disk: { free_gb: 40, thresholds: { soft_gb: 12, hard_gb: 8, emergency_gb: 5 } },
    load1: 0.5,
    psi: { cpu10: 0, mem10: 0 },
    mem_available_mb: 2000,
    queue: { heavy: { queued: 0, running: 0, deferred: 0 }, light: { queued: 0, running: 0, deferred: 0 } },
    budget: {
      day: "2026-09-26",
      daily_limit_usd: 5,
      spent_today_usd: 0,
      reserved_usd: 0,
      paused: false,
      pause_reason: null,
      alert_threshold_pct: 80,
    },
    versions: { app: "0.1.0", db_schema: 9 },
    ...overrides,
  };
}

test.describe("индикатор воркера (§7.3)", () => {
  const now = Date.parse("2026-09-26T12:00:00Z");

  test("idle / рендер с процентом / отложено / down", () => {
    expect(deriveWorkerState(status({}, now), now).kind).toBe("idle");

    const busy = deriveWorkerState(
      status({ heavy_slot: { owner: "h", task_id: 991, stage: "render", pct: 41.6 } }, now),
      now,
    );
    expect(busy).toMatchObject({ kind: "busy", label: "#991 · Рендер · 42%", pct: 42 });

    const base = status({}, now);
    base.workers[0].state = "deferred:mem_low";
    expect(deriveWorkerState(base, now)).toMatchObject({ kind: "deferred", label: "отложено: мало памяти" });

    const stale = status({}, now);
    for (const worker of stale.workers) worker.heartbeat_at = new Date(now - 10 * 60_000).toISOString();
    expect(deriveWorkerState(stale, now).kind).toBe("down");
    expect(deriveWorkerState(status({ workers: [] }, now), now).kind).toBe("down");
    expect(deriveWorkerState(undefined, now).kind).toBe("unknown");
  });
});
