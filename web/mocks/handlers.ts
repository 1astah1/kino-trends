/**
 * MSW-обработчики эндпоинтов M1 (auth, users, system, budget, alerts, events/stream) по типам
 * контракта `lib/api/schema.d.ts`. Поведение повторяет §5.1: формат ошибок, 401 без сессии,
 * 403 `csrf_failed` без `X-CSRF-Token`, роли. Области M2 — `handlers/catalog.ts` и
 * `handlers/production.ts`. Всё остальное под `/api/v1` отвечает 501, как заглушки M1-0.
 */
import { http, HttpResponse, sse } from "msw";

import type { components } from "@/lib/api/schema";

import { apiTokens, audit, auditLog, providers, rejectReasons, safeZones, secrets, settings } from "./catalog-m1";
import {
  alerts,
  allocateUserId,
  budget,
  currentUserOf,
  type MockSession,
  type MockUser,
  publicUser,
  randomToken,
  readSession,
  systemStatus,
  users,
  writeSession,
} from "./db";
import { autopublishHandlers } from "./handlers/autopublish";
import { catalogHandlers } from "./handlers/catalog";
import { clippingHandlers } from "./handlers/clipping";
import { packagesHandlers } from "./handlers/packages";
import { productionHandlers } from "./handlers/production";

type Schemas = components["schemas"];
type Role = Schemas["Role"];

const API = "/api/v1";
const LOGIN_RE = /^[a-z0-9][a-z0-9_.-]{2,31}$/;

function error(status: number, code: string, message: string, details: Record<string, unknown> = {}) {
  return HttpResponse.json({ error: { code, message, details } } satisfies Schemas["ErrorResponse"], { status });
}

function validation(errors: Array<{ loc: Array<string | number>; msg: string; type: string }>) {
  return error(422, "validation_error", "Проверьте поля формы", { errors });
}

type Guard = { session: MockSession; user: MockUser } | { response: Response };

function authenticate(request: Request, { roles, csrf = true }: { roles?: Role[]; csrf?: boolean } = {}): Guard {
  const session = readSession();
  const user = session ? users.find((item) => item.id === session.userId && item.is_active) : undefined;
  if (!session || !user) return { response: error(401, "not_authenticated", "Нужно войти") };
  const unsafe = !["GET", "HEAD", "OPTIONS"].includes(request.method.toUpperCase());
  if (csrf && unsafe && request.headers.get("X-CSRF-Token") !== session.csrf) {
    return { response: error(403, "csrf_failed", "Нет или неверный X-CSRF-Token") };
  }
  if (roles && !roles.includes(user.role)) return { response: error(403, "forbidden", "Недостаточно прав") };
  return { session, user };
}

function authState(user: MockUser, session: MockSession): Schemas["AuthState"] {
  return { user: currentUserOf(user), csrf_token: session.csrf };
}

function csrfCookie(value: string, maxAge?: number) {
  return `as_csrf=${value}; Path=/; SameSite=Lax${maxAge === undefined ? "" : `; Max-Age=${maxAge}`}`;
}

export const handlers = [
  http.get(`${API}/health`, () => HttpResponse.json({ status: "ok" } satisfies Schemas["HealthResponse"])),
  http.get(`${API}/health/ready`, () =>
    HttpResponse.json({
      status: "ok",
      checks: {
        db: { ok: true, detail: null },
        migrations: { ok: true, detail: "schema 11 / 11" },
        media_writable: { ok: true, detail: null },
        disk: { ok: true, detail: "11,4 ГБ свободно — ниже мягкого порога 12 ГБ" },
      },
    } satisfies Schemas["ReadyResponse"]),
  ),

  // ---------- auth ----------
  http.post(`${API}/auth/login`, async ({ request }) => {
    const body = (await request.json().catch(() => null)) as Partial<Schemas["LoginRequest"]> | null;
    if (!body?.login || !body?.password) {
      return validation([{ loc: ["body", "login"], msg: "Обязательное поле", type: "missing" }]);
    }
    const login = body.login.trim().toLowerCase();
    const user = users.find((item) => item.login === login);
    if (!user || user.password !== body.password || !user.is_active) {
      return error(401, "not_authenticated", "Неверный логин или пароль");
    }
    const session: MockSession = { userId: user.id, csrf: randomToken(), createdAt: new Date().toISOString() };
    writeSession(session);
    user.last_login_at = session.createdAt;
    return HttpResponse.json(authState(user, session), { headers: { "Set-Cookie": csrfCookie(session.csrf) } });
  }),
  http.post(`${API}/auth/logout`, ({ request }) => {
    const guard = authenticate(request);
    if ("response" in guard) return guard.response;
    writeSession(null);
    return new HttpResponse(null, { status: 204, headers: { "Set-Cookie": csrfCookie("", 0) } });
  }),
  http.get(`${API}/auth/me`, ({ request }) => {
    const guard = authenticate(request);
    if ("response" in guard) return guard.response;
    return HttpResponse.json(authState(guard.user, guard.session));
  }),
  http.get(`${API}/auth/sessions`, ({ request }) => {
    const guard = authenticate(request);
    if ("response" in guard) return guard.response;
    return HttpResponse.json({
      items: [
        {
          id: 1,
          created_at: guard.session.createdAt,
          last_seen_at: new Date().toISOString(),
          user_agent: navigator.userAgent,
          current: true,
        },
      ],
    } satisfies Schemas["SessionList"]);
  }),
  http.post(`${API}/auth/change-password`, async ({ request }) => {
    const guard = authenticate(request);
    if ("response" in guard) return guard.response;
    const body = (await request.json()) as Schemas["ChangePasswordRequest"];
    if (body.current_password !== guard.user.password) {
      return validation([{ loc: ["body", "current_password"], msg: "Неверный текущий пароль", type: "value_error" }]);
    }
    if (body.new_password.length < 12) {
      return validation([{ loc: ["body", "new_password"], msg: "Не короче 12 символов", type: "string_too_short" }]);
    }
    guard.user.password = body.new_password;
    return new HttpResponse(null, { status: 204 });
  }),

  // ---------- users (admin) ----------
  http.get(`${API}/users`, ({ request }) => {
    const guard = authenticate(request, { roles: ["admin"] });
    if ("response" in guard) return guard.response;
    return HttpResponse.json({ items: users.map(publicUser) } satisfies Schemas["UserList"]);
  }),
  http.post(`${API}/users`, async ({ request }) => {
    const guard = authenticate(request, { roles: ["admin"] });
    if ("response" in guard) return guard.response;
    const body = (await request.json()) as Schemas["UserCreate"];
    const issues: Array<{ loc: Array<string | number>; msg: string; type: string }> = [];
    if (!LOGIN_RE.test(body.login))
      issues.push({ loc: ["body", "login"], msg: "Недопустимый логин", type: "string_pattern_mismatch" });
    if (!body.password || body.password.length < 12)
      issues.push({ loc: ["body", "password"], msg: "Не короче 12 символов", type: "string_too_short" });
    if (issues.length) return validation(issues);
    if (users.some((item) => item.login === body.login)) return error(409, "login_taken", "Такой логин уже есть");
    const now = new Date().toISOString();
    const user: MockUser = {
      id: allocateUserId(),
      login: body.login,
      password: body.password,
      display_name: body.display_name,
      role: body.role,
      tg_user_id: body.tg_user_id ?? null,
      is_active: true,
      locked_until: null,
      last_login_at: null,
      created_at: now,
      updated_at: now,
    };
    users.push(user);
    return HttpResponse.json(publicUser(user), { status: 201 });
  }),
  http.patch(`${API}/users/:userId`, async ({ request, params }) => {
    const guard = authenticate(request, { roles: ["admin"] });
    if ("response" in guard) return guard.response;
    const user = users.find((item) => item.id === Number(params.userId));
    if (!user) return error(404, "user_not_found", "Пользователь не найден");
    const body = (await request.json()) as Schemas["UserUpdate"];
    if (body.display_name !== undefined && body.display_name !== null) user.display_name = body.display_name;
    if (body.role) user.role = body.role;
    if (body.is_active !== undefined && body.is_active !== null) user.is_active = body.is_active;
    if (body.tg_user_id !== undefined) user.tg_user_id = body.tg_user_id;
    user.updated_at = new Date().toISOString();
    return HttpResponse.json(publicUser(user));
  }),
  http.post(`${API}/users/:userId/reset-password`, async ({ request, params }) => {
    const guard = authenticate(request, { roles: ["admin"] });
    if ("response" in guard) return guard.response;
    const user = users.find((item) => item.id === Number(params.userId));
    if (!user) return error(404, "user_not_found", "Пользователь не найден");
    const body = (await request.json()) as Schemas["ResetPasswordRequest"];
    if (!body.new_password || body.new_password.length < 12) {
      return validation([{ loc: ["body", "new_password"], msg: "Не короче 12 символов", type: "string_too_short" }]);
    }
    user.password = body.new_password;
    return new HttpResponse(null, { status: 204 });
  }),

  // ---------- system, budget, alerts ----------
  http.get(`${API}/system/status`, ({ request }) => {
    const guard = authenticate(request);
    if ("response" in guard) return guard.response;
    return HttpResponse.json(systemStatus());
  }),
  http.get(`${API}/budget`, ({ request }) => {
    const guard = authenticate(request);
    if ("response" in guard) return guard.response;
    return HttpResponse.json(budget);
  }),
  http.get(`${API}/alerts`, ({ request }) => {
    const guard = authenticate(request);
    if ("response" in guard) return guard.response;
    const statuses = new URL(request.url).searchParams.getAll("status");
    const items = statuses.length ? alerts.filter((item) => statuses.includes(item.status)) : alerts;
    return HttpResponse.json({ items } satisfies Schemas["AlertList"]);
  }),
  http.post(`${API}/alerts/:alertId/ack`, ({ request, params }) => {
    const guard = authenticate(request, { roles: ["admin", "operator"] });
    if ("response" in guard) return guard.response;
    const alert = alerts.find((item) => item.id === Number(params.alertId));
    if (!alert) return error(404, "alert_not_found", "Алерт не найден");
    alert.status = "acked";
    alert.acked_by = guard.user.id;
    return new HttpResponse(null, { status: 204 });
  }),

  // ---------- budget (admin меняет лимиты и паузу) ----------
  http.patch(`${API}/budget`, async ({ request }) => {
    const guard = authenticate(request, { roles: ["admin"] });
    if ("response" in guard) return guard.response;
    const body = (await request.json()) as Schemas["BudgetPatch"];
    if (body.daily_limit_usd !== undefined && body.daily_limit_usd !== null)
      budget.daily_limit_usd = body.daily_limit_usd;
    if (body.alert_threshold_pct !== undefined && body.alert_threshold_pct !== null) {
      budget.alert_threshold_pct = body.alert_threshold_pct;
    }
    audit({
      actor_kind: "user",
      actor_user_id: guard.user.id,
      action: "budget.update",
      entity_type: "budget",
      entity_id: null,
      summary: null,
      diff: { ...body },
    });
    return HttpResponse.json(budget);
  }),
  http.post(`${API}/budget/pause`, async ({ request }) => {
    const guard = authenticate(request, { roles: ["admin"] });
    if ("response" in guard) return guard.response;
    const body = (await request.json()) as Schemas["BudgetPause"];
    budget.paused = true;
    budget.pause_reason = body.reason;
    return HttpResponse.json(budget);
  }),
  http.post(`${API}/budget/resume`, ({ request }) => {
    const guard = authenticate(request, { roles: ["admin"] });
    if ("response" in guard) return guard.response;
    budget.paused = false;
    budget.pause_reason = null;
    return HttpResponse.json(budget);
  }),

  // ---------- settings / secrets / providers (admin) ----------
  http.get(`${API}/settings`, ({ request }) => {
    const guard = authenticate(request, { roles: ["admin", "operator"] });
    if ("response" in guard) return guard.response;
    return HttpResponse.json({ items: settings } satisfies Schemas["SettingsList"]);
  }),
  http.patch(`${API}/settings`, async ({ request }) => {
    const guard = authenticate(request, { roles: ["admin"] });
    if ("response" in guard) return guard.response;
    const body = (await request.json()) as Record<string, unknown>;
    const unknownKeys = Object.keys(body).filter((key) => !settings.some((item) => item.key === key));
    if (unknownKeys.length) {
      return validation(
        unknownKeys.map((key) => ({ loc: ["body", key], msg: "Неизвестная настройка", type: "extra_forbidden" })),
      );
    }
    for (const item of settings) {
      if (item.key in body) {
        item.value = body[item.key];
        item.is_overridden = JSON.stringify(item.value) !== JSON.stringify(item.default);
        item.updated_at = new Date().toISOString();
      }
    }
    return HttpResponse.json({ items: settings } satisfies Schemas["SettingsList"]);
  }),
  http.get(`${API}/secrets`, ({ request }) => {
    const guard = authenticate(request, { roles: ["admin"] });
    if ("response" in guard) return guard.response;
    return HttpResponse.json({ items: secrets } satisfies Schemas["SecretList"]);
  }),
  http.put(`${API}/secrets/:secretName`, async ({ request, params }) => {
    const guard = authenticate(request, { roles: ["admin"] });
    if ("response" in guard) return guard.response;
    const name = String(params.secretName);
    if (!/^[A-Z][A-Z0-9_]{2,63}$/.test(name)) return error(422, "validation_error", "Имя секрета: A–Z, 0–9, _");
    const body = (await request.json()) as Schemas["SecretWrite"];
    const existing = secrets.find((item) => item.name === name);
    const record = existing ?? { name, is_set: false, source: null, last4: null, updated_at: null, used_by: [] };
    record.is_set = true;
    record.source = "db";
    record.last4 = body.value.slice(-4);
    record.updated_at = new Date().toISOString();
    if (!existing) secrets.push(record);
    audit({
      actor_kind: "user",
      actor_user_id: guard.user.id,
      action: "secret.set",
      entity_type: "secret",
      entity_id: name,
      summary: null,
      diff: null,
    });
    return new HttpResponse(null, { status: 204 });
  }),
  http.delete(`${API}/secrets/:secretName`, ({ request, params }) => {
    const guard = authenticate(request, { roles: ["admin"] });
    if ("response" in guard) return guard.response;
    const record = secrets.find((item) => item.name === String(params.secretName));
    if (!record) return error(404, "secret_not_found", "Секрет не найден");
    Object.assign(record, { is_set: false, source: null, last4: null, updated_at: new Date().toISOString() });
    return new HttpResponse(null, { status: 204 });
  }),
  http.get(`${API}/providers`, ({ request }) => {
    const guard = authenticate(request);
    if ("response" in guard) return guard.response;
    return HttpResponse.json({ items: providers } satisfies Schemas["ProviderList"]);
  }),
  http.post(`${API}/providers/:providerKey/test`, ({ request, params }) => {
    const guard = authenticate(request, { roles: ["admin"] });
    if ("response" in guard) return guard.response;
    const provider = providers.find((item) => item.key === String(params.providerKey));
    if (!provider) return error(404, "provider_not_found", "Провайдер не найден");
    const ok = !provider.secret_name || provider.secret_set;
    return HttpResponse.json({
      ok,
      latency_ms: ok ? 240 : null,
      detail: ok ? null : `Не задан секрет ${provider.secret_name}`,
    } satisfies Schemas["ProviderTestResult"]);
  }),

  // ---------- справочники, аудит, токены, воркеры ----------
  http.get(`${API}/meta/reject-reasons`, ({ request }) => {
    const guard = authenticate(request);
    if ("response" in guard) return guard.response;
    return HttpResponse.json(rejectReasons);
  }),
  http.get(`${API}/meta/safe-zones`, ({ request }) => {
    const guard = authenticate(request);
    if ("response" in guard) return guard.response;
    return HttpResponse.json(safeZones);
  }),
  http.get(`${API}/audit-log`, ({ request }) => {
    const guard = authenticate(request, { roles: ["admin", "operator"] });
    if ("response" in guard) return guard.response;
    const url = new URL(request.url);
    const limit = Math.min(200, Number(url.searchParams.get("limit") ?? 50));
    const beforeId = Number(url.searchParams.get("before_id") ?? Number.MAX_SAFE_INTEGER);
    const items = auditLog.filter((entry) => entry.id < beforeId).slice(0, limit);
    const nextBeforeId = items.length === limit ? items[items.length - 1].id : null;
    return HttpResponse.json({ items, next_before_id: nextBeforeId } satisfies Schemas["AuditJournal"]);
  }),
  http.get(`${API}/api-tokens`, ({ request }) => {
    const guard = authenticate(request, { roles: ["admin"] });
    if ("response" in guard) return guard.response;
    return HttpResponse.json({ items: apiTokens } satisfies Schemas["ApiTokenList"]);
  }),
  http.get(`${API}/workers`, ({ request }) => {
    const guard = authenticate(request);
    if ("response" in guard) return guard.response;
    return HttpResponse.json({ items: systemStatus().workers } satisfies Schemas["WorkerList"]);
  }),

  // ---------- SSE ----------
  sse(`${API}/events/stream`, ({ client, request }) => {
    const guard = authenticate(request, { csrf: false });
    if ("response" in guard) {
      client.error();
      return;
    }
    const send = <E extends Schemas["SseEvent"]["event"]>(
      event: E,
      data: Extract<Schemas["SseEvent"], { event: E }>["data"],
    ) => client.send({ event, data: JSON.stringify(data) } as never);

    send("ping", {});
    let lastBusy: boolean | null = null;
    const timer = setInterval(() => {
      send("ping", {});
      const status = systemStatus();
      const busy = status.heavy_slot.task_id !== null;
      if (busy !== lastBusy) {
        lastBusy = busy;
        send("worker", {
          worker_id: "box-2101-heavy",
          resource_class: "heavy",
          state: busy ? "busy" : "idle",
          task_id: status.heavy_slot.task_id,
        });
      }
      budget.spent_today_usd = Math.round((budget.spent_today_usd + 0.0031) * 1e4) / 1e4;
      send("budget", {
        spent_usd: budget.spent_today_usd,
        reserved_usd: budget.reserved_usd,
        limit_usd: budget.daily_limit_usd,
        paused: budget.paused,
      });
    }, 5000);
    request.signal.addEventListener("abort", () => clearInterval(timer));
  }),

  // Области M2: каталог и редактор баннера (M2-D2) раньше производства и приёмки (M2-D1) —
  // их /channels и /campaigns главнее минимальных дублей в production.ts.
  ...catalogHandlers,
  ...productionHandlers,
  ...packagesHandlers,
  // Трек нарезки (M5-C): источники, tus, кандидаты, авторский слой; ролики создаёт мок Конвейера.
  ...clippingHandlers,
  // Автозалив YouTube (0019): OAuth-подключение канала и план.
  ...autopublishHandlers,

  // Остальное — как заглушки M1-0: 501 в формате ошибок.
  http.all(`${API}/*`, () => error(501, "not_implemented", "Эндпоинт появится в следующих этапах (мок M1)")),
];
