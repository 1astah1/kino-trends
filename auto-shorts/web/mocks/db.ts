/**
 * Состояние моков M1 (в памяти вкладки). Сессия переживает перезагрузку страницы через
 * sessionStorage — настоящий сервер держит её в HttpOnly-cookie `as_session`, которую мок поставить
 * не может. Данные — демонстрационные, только для разработки и e2e на моках.
 */
import type { components } from "@/lib/api/schema";

type Schemas = components["schemas"];

export type MockUser = Schemas["User"] & { password: string };

export interface MockSession {
  userId: number;
  csrf: string;
  createdAt: string;
}

const SESSION_KEY = "as-mock-session";
const START = Date.now();

function iso(offsetMs: number): string {
  return new Date(START + offsetMs).toISOString();
}

export const MOCK_CREDENTIALS = {
  admin: { login: "admin", password: "admin-demo-password" },
  operator: { login: "operator", password: "operator-demo-pass" },
  viewer: { login: "viewer", password: "viewer-demo-pass1" },
} as const;

export const users: MockUser[] = [
  {
    id: 1,
    login: MOCK_CREDENTIALS.admin.login,
    password: MOCK_CREDENTIALS.admin.password,
    display_name: "Владелец",
    role: "admin",
    tg_user_id: 100000001,
    is_active: true,
    locked_until: null,
    last_login_at: iso(-3 * 3600_000),
    created_at: iso(-30 * 86400_000),
    updated_at: iso(-2 * 86400_000),
  },
  {
    id: 2,
    login: MOCK_CREDENTIALS.operator.login,
    password: MOCK_CREDENTIALS.operator.password,
    display_name: "Оператор смены",
    role: "operator",
    tg_user_id: 100000002,
    is_active: true,
    locked_until: null,
    last_login_at: iso(-26 * 3600_000),
    created_at: iso(-20 * 86400_000),
    updated_at: iso(-20 * 86400_000),
  },
  {
    id: 3,
    login: MOCK_CREDENTIALS.viewer.login,
    password: MOCK_CREDENTIALS.viewer.password,
    display_name: "Наблюдатель",
    role: "viewer",
    tg_user_id: null,
    is_active: true,
    locked_until: null,
    last_login_at: null,
    created_at: iso(-5 * 86400_000),
    updated_at: iso(-5 * 86400_000),
  },
];

export const alerts: Schemas["Alert"][] = [
  {
    id: 41,
    code: "disk_low",
    severity: "warning",
    status: "open",
    title: "Свободно меньше 12 ГБ на диске медиа",
    details: { free_gb: 11.4 },
    opened_at: iso(-40 * 60_000),
    last_seen_at: iso(-60_000),
    resolved_at: null,
    acked_by: null,
  },
  {
    id: 42,
    code: "provider_failing",
    severity: "critical",
    status: "open",
    title: "edge-tts: 5 ошибок подряд",
    details: { provider: "edge_tts" },
    opened_at: iso(-12 * 60_000),
    last_seen_at: iso(-30_000),
    resolved_at: null,
    acked_by: null,
  },
];

export const budget: Schemas["Budget"] = {
  day: new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Moscow" }).format(new Date()),
  daily_limit_usd: 5,
  spent_today_usd: 1.8421,
  reserved_usd: 0.12,
  paused: false,
  pause_reason: null,
  alert_threshold_pct: 80,
};

let nextUserId = 4;
export function allocateUserId(): number {
  return nextUserId++;
}

export function publicUser(user: MockUser): Schemas["User"] {
  const { password, ...rest } = user;
  void password;
  return rest;
}

export function currentUserOf(user: MockUser): Schemas["CurrentUser"] {
  return {
    id: user.id,
    login: user.login,
    display_name: user.display_name,
    role: user.role,
    tg_user_id: user.tg_user_id,
  };
}

export function readSession(): MockSession | null {
  try {
    const raw = window.sessionStorage.getItem(SESSION_KEY);
    return raw ? (JSON.parse(raw) as MockSession) : null;
  } catch {
    return null;
  }
}

export function writeSession(session: MockSession | null) {
  try {
    if (session) window.sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
    else window.sessionStorage.removeItem(SESSION_KEY);
  } catch {
    // без хранилища сессия живёт до перезагрузки
  }
}

export function randomToken(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

/**
 * Живая картинка воркеров: тяжёлый слот 40 с занят рендером (процент растёт), 20 с свободен.
 * Детерминирована от времени — одинакова во всех вкладках.
 */
export function systemStatus(now: number = Date.now()): Schemas["SystemStatus"] {
  const cycle = (now / 1000) % 60;
  const busy = cycle < 40;
  const pct = busy ? Math.round((cycle / 40) * 100) : null;
  const heartbeat = new Date(now - 4000).toISOString();
  const started = new Date(START - 6 * 3600_000).toISOString();
  return {
    workers: [
      {
        id: "box-2101-heavy",
        resource_class: "heavy",
        hostname: "box",
        pid: 2101,
        version: "0.1.0",
        started_at: started,
        heartbeat_at: heartbeat,
        current_task_id: busy ? 1207 : null,
        state: busy ? "busy" : "idle",
      },
      {
        id: "box-2102-light",
        resource_class: "light",
        hostname: "box",
        pid: 2102,
        version: "0.1.0",
        started_at: started,
        heartbeat_at: heartbeat,
        current_task_id: null,
        state: "idle",
      },
    ],
    heavy_slot: {
      owner: busy ? "box-2101-heavy" : null,
      task_id: busy ? 1207 : null,
      stage: busy ? "render" : null,
      pct,
    },
    disk: { free_gb: 11.4, thresholds: { soft_gb: 12, hard_gb: 8, emergency_gb: 5 } },
    load1: 1.37,
    psi: { cpu10: 3.2, mem10: 0.4 },
    mem_available_mb: 1840,
    queue: {
      heavy: { queued: busy ? 2 : 3, running: busy ? 1 : 0, deferred: 0 },
      light: { queued: 0, running: 0, deferred: 0 },
    },
    budget,
    versions: { app: "0.1.0", db_schema: 9 },
  };
}
