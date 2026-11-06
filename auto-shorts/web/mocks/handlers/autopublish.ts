/**
 * MSW-моки подключения канала к YouTube (0019, `api/routers/autopublish.py`) по типам `lib/api/schema.d.ts`:
 * статус подключения и план автозалива, ссылка на экран согласия Google (409 `oauth_client_missing`),
 * отключение (admin).
 *
 * Состояния на сидах каталога:
 * - `taro_main` — подключён, право youtube.upload, план «авто» по слотам;
 * - `taro_second` — OAuth-клиент есть (общий `YOUTUBE_OAUTH_CLIENT`), канал не подключён;
 * - `astro_main` — у куста astro свой клиент `YT_OAUTH_CLIENT_ASTRO`, он не записан → «не настроен».
 *   Запись секрета (мок `PUT /secrets/{name}` в handlers.ts) переводит канал в «настроен».
 *
 * Канал (площадка, куст, план) читается из мока каталога запросом `GET /channels/{id}` — как мок нарезки
 * читает ролики Конвейера. Колбэк Google — навигация, MSW её не видит: возврат проверяется `?oauth=…`.
 *
 * Подключение: `handlers.ts` разворачивает `autopublishHandlers` до общего 501-обработчика.
 */
import { http, HttpResponse } from "msw";

import type { components } from "@/lib/api/schema";

import { secrets } from "../catalog-m1";
import { readSession, users } from "../db";

type S = components["schemas"];
type Role = S["Role"];

const API = "/api/v1";
const START = Date.now();
const HOUR = 3_600_000;
const DAY = 24 * HOUR;
const iso = (offsetMs: number) => new Date(START + offsetMs).toISOString();

const AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth";
const CALLBACK_PATH = "/api/v1/oauth/youtube/callback";
const SCOPE_UPLOAD = "https://www.googleapis.com/auth/youtube.upload";
const SCOPE_READONLY = "https://www.googleapis.com/auth/youtube.readonly";
const DEFAULT_CLIENT_SECRET = "YOUTUBE_OAUTH_CLIENT";
const DEFAULT_TZ = "Europe/Moscow";
const DEFAULT_CATEGORY_ID = "22";

/** Свой OAuth-клиент куста (`clusters.youtube_oauth_client_secret`); в сидах /clusters поле пустое. */
const CLUSTER_CLIENT_SECRET: Record<string, string> = { astro: "YT_OAUTH_CLIENT_ASTRO" };

// Общий клиент записан: без него «настроен-не подключён» не показать.
if (!secrets.some((item) => item.name === DEFAULT_CLIENT_SECRET)) {
  secrets.push({
    name: DEFAULT_CLIENT_SECRET,
    is_set: true,
    source: "db",
    last4: "s3cr",
    updated_at: iso(-14 * DAY),
    used_by: [],
  });
}

interface MockToken {
  scopes: string[];
  granted_at: string;
  last_ok_at: string | null;
  last_error: string | null;
  google_channel_id: string | null;
  google_channel_title: string | null;
}

const tokens = new Map<string, MockToken>([
  [
    "taro_main",
    {
      scopes: [SCOPE_UPLOAD, SCOPE_READONLY],
      granted_at: iso(-12 * DAY),
      last_ok_at: iso(-2 * HOUR),
      last_error: null,
      google_channel_id: null, // из platform_channel_ref канала
      google_channel_title: null,
    },
  ],
]);

/** План поверх `publishing_plan` канала (в сидах каталога там только videos_per_day). */
const PLAN_SEED: Record<string, S["PublishingPlan"]> = {
  taro_main: { mode: "auto", slots: ["09:00", "13:00", "19:30"], privacy: "public", category_id: "24", language: "ru" },
};

function error(status: number, code: string, message: string, details: Record<string, unknown> = {}) {
  return HttpResponse.json({ error: { code, message, details } } satisfies S["ErrorResponse"], { status });
}

function guard(request: Request, roles?: Role[]): { userId: number; role: Role } | Response {
  const session = readSession();
  const user = session ? users.find((item) => item.id === session.userId && item.is_active) : undefined;
  if (!session || !user) return error(401, "not_authenticated", "Нужно войти");
  const unsafe = !["GET", "HEAD", "OPTIONS"].includes(request.method.toUpperCase());
  if (unsafe && request.headers.get("X-CSRF-Token") !== session.csrf) {
    return error(403, "csrf_failed", "Нет или неверный X-CSRF-Token");
  }
  if (roles && !roles.includes(user.role)) return error(403, "forbidden", "Недостаточно прав");
  return { userId: user.id, role: user.role };
}

async function channelOf(channelId: string): Promise<S["Channel"] | null> {
  const response = await fetch(`${API}/channels/${encodeURIComponent(channelId)}`, {
    headers: { Accept: "application/json" },
  });
  return response.ok ? ((await response.json()) as S["Channel"]) : null;
}

/** Как `even_slots` сервиса: n слотов равномерно с 09:00 до 21:00, один — в полдень, шаг 5 минут. */
function evenSlots(n: number): string[] {
  if (n <= 1) return ["12:00"];
  const out = new Set<string>();
  for (let i = 0; i < n; i += 1) {
    const minutes = 9 * 60 + Math.round((12 * 60 * i) / (n - 1) / 5) * 5;
    const hh = String(Math.min(23, Math.floor(minutes / 60))).padStart(2, "0");
    out.add(`${hh}:${String(minutes % 60).padStart(2, "0")}`);
  }
  return [...out].sort();
}

function clientSecretName(channel: S["Channel"]): string {
  return CLUSTER_CLIENT_SECRET[channel.cluster] ?? DEFAULT_CLIENT_SECRET;
}

function clientConfigured(channel: S["Channel"]): boolean {
  const name = clientSecretName(channel);
  return secrets.some((item) => item.name === name && item.is_set);
}

function redirectUri(): string {
  return `${location.origin}${CALLBACK_PATH}`;
}

function connectionOf(channel: S["Channel"]): S["YoutubeConnection"] {
  // Явный null в плане канала (сняли язык, сбросили пояс) перекрывает сид — как слияние на сервере.
  const plan = { ...PLAN_SEED[channel.id], ...definedOnly(channel.publishing_plan) };
  const perDay = plan.videos_per_day ?? null;
  const token = tokens.get(channel.id);
  return {
    channel_id: channel.id,
    platform: channel.platform,
    client_configured: clientConfigured(channel),
    client_secret_name: clientSecretName(channel),
    redirect_uri: redirectUri(),
    connected: !!token,
    can_upload: !!token?.scopes.includes(SCOPE_UPLOAD),
    scopes: token?.scopes ?? [],
    granted_at: token?.granted_at ?? null,
    last_ok_at: token?.last_ok_at ?? null,
    last_error: token?.last_error ?? null,
    google_channel_id: token ? (token.google_channel_id ?? channel.platform_channel_ref) : null,
    google_channel_title: token ? (token.google_channel_title ?? channel.title) : null,
    mode: plan.mode ?? "manual",
    slots: plan.slots?.length ? [...plan.slots].sort() : evenSlots(perDay ?? 3),
    timezone: plan.timezone ?? DEFAULT_TZ,
    privacy: plan.privacy ?? "public",
    videos_per_day: perDay,
    category_id: plan.category_id ?? DEFAULT_CATEGORY_ID,
    language: plan.language ?? null,
  };
}

function definedOnly(plan: S["PublishingPlan"]): S["PublishingPlan"] {
  return Object.fromEntries(Object.entries(plan).filter(([, value]) => value !== undefined)) as S["PublishingPlan"];
}

export const autopublishHandlers = [
  http.get(`${API}/channels/:channelId/oauth/youtube/start`, async ({ request, params }) => {
    const who = guard(request, ["admin", "operator"]);
    if (who instanceof Response) return who;
    const channel = await channelOf(String(params.channelId));
    if (!channel) return error(404, "channel_not_found", "Канал не найден");
    if (channel.platform !== "youtube") {
      const message = "Автозалив пока только для YouTube: VK Клипы идут вторым контуром";
      return error(422, "autopublish_platform_unsupported", message, { platform: channel.platform });
    }
    if (!clientConfigured(channel)) {
      const secretName = clientSecretName(channel);
      return error(
        409,
        "oauth_client_missing",
        `Нет OAuth-клиента Google: запишите в секрет ${secretName} JSON клиента из Google Cloud Console (тип Web application)`,
        { secret_name: secretName, redirect_uri: redirectUri() },
      );
    }
    const query = new URLSearchParams({
      client_id: "1234567890-mock.apps.googleusercontent.com",
      redirect_uri: redirectUri(),
      response_type: "code",
      scope: `${SCOPE_UPLOAD} ${SCOPE_READONLY}`,
      access_type: "offline",
      prompt: "consent select_account",
      include_granted_scopes: "true",
      state: `mock.${channel.id}.${Math.random().toString(36).slice(2, 10)}`,
    });
    return HttpResponse.json({
      authorize_url: `${AUTH_URL}?${query.toString()}`,
      redirect_uri: redirectUri(),
    } satisfies S["OAuthStart"]);
  }),
  http.get(`${API}/channels/:channelId/oauth/youtube`, async ({ request, params }) => {
    const who = guard(request);
    if (who instanceof Response) return who;
    const channel = await channelOf(String(params.channelId));
    if (!channel) return error(404, "channel_not_found", "Канал не найден");
    return HttpResponse.json(connectionOf(channel));
  }),
  http.delete(`${API}/channels/:channelId/oauth/youtube`, async ({ request, params }) => {
    const who = guard(request, ["admin"]);
    if (who instanceof Response) return who;
    const channel = await channelOf(String(params.channelId));
    if (!channel) return error(404, "channel_not_found", "Канал не найден");
    tokens.delete(channel.id);
    return new HttpResponse(null, { status: 204 });
  }),
];
