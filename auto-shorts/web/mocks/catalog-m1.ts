/**
 * Демоданные остальных эндпоинтов M1-B (настройки, секреты, провайдеры, аудит, справочники, токены).
 * Значения повторяют дефолты `domain/settings_schema.py` и подписи `domain/enums.py`.
 */
import type { components } from "@/lib/api/schema";

type Schemas = components["schemas"];

const now = () => new Date().toISOString();

export const settings: Schemas["SettingItem"][] = [
  {
    key: "business_timezone",
    value: "Europe/Moscow",
    default: "Europe/Moscow",
    description: "Часовой пояс бизнес-дня",
    group: "general",
    is_overridden: false,
    updated_at: null,
  },
  {
    key: "resource_profile",
    value: "shared_dev",
    default: "shared_dev",
    description: "Профиль ресурсов",
    group: "worker",
    is_overridden: false,
    updated_at: null,
  },
  {
    key: "disk_soft_min_gb",
    value: 12,
    default: 12,
    description: "Мягкий порог свободного места, ГБ",
    group: "worker",
    is_overridden: false,
    updated_at: null,
  },
  {
    key: "disk_hard_min_gb",
    value: 8,
    default: 8,
    description: "Жёсткий порог: тяжёлые задачи откладываются, ГБ",
    group: "worker",
    is_overridden: false,
    updated_at: null,
  },
  {
    key: "ffmpeg_threads",
    value: 2,
    default: 2,
    description: "Потоки ffmpeg",
    group: "render",
    is_overridden: false,
    updated_at: null,
  },
  {
    key: "fx_usd_rub",
    value: 95,
    default: 95,
    description: "Курс USD→RUB для отчётов",
    group: "analytics",
    is_overridden: false,
    updated_at: null,
  },
  {
    key: "allow_mock_in_prod",
    value: false,
    default: false,
    description: "Разрешить mock-провайдеры при APP_ENV=prod",
    group: "security",
    is_overridden: false,
    updated_at: null,
  },
];

export const secrets: Schemas["SecretItem"][] = [
  { name: "LLM_API_KEY", is_set: true, source: "env", last4: "9f2a", updated_at: null, used_by: ["openai_compat"] },
  { name: "FAL_API_KEY", is_set: false, source: null, last4: null, updated_at: null, used_by: ["fal_flux_schnell"] },
  { name: "TELEGRAM_BOT_TOKEN", is_set: true, source: "db", last4: "x7Qe", updated_at: now(), used_by: [] },
];

export const providers: Schemas["Provider"][] = [
  {
    key: "openai_compat",
    kind: "llm",
    adapter: "llm.openai_compat",
    label: "OpenAI-совместимый LLM",
    enabled: true,
    is_default: true,
    params: { model: "gpt-4o-mini" },
    secret_name: "LLM_API_KEY",
    secret_set: true,
    price: { unit: "1k_tokens", usd: 0.0006 },
    license_note: null,
    resource_class: "light",
    last_ok_at: now(),
    last_error: null,
  },
  {
    key: "edge_tts",
    kind: "tts",
    adapter: "tts.edge",
    label: "edge-tts",
    enabled: true,
    is_default: true,
    params: { voice: "ru-RU-SvetlanaNeural" },
    secret_name: null,
    secret_set: false,
    price: null,
    license_note: "Неофициальный доступ к Microsoft Edge TTS",
    resource_class: "light",
    last_ok_at: null,
    last_error: "NoAudioReceived",
  },
  {
    key: "fal_flux_schnell",
    kind: "image",
    adapter: "images.fal",
    label: "fal FLUX schnell",
    enabled: true,
    is_default: true,
    params: {},
    secret_name: "FAL_API_KEY",
    secret_set: false,
    price: { unit: "image", usd: 0.003 },
    license_note: null,
    resource_class: "light",
    last_ok_at: null,
    last_error: null,
  },
];

const REJECT_REASONS: Array<[Schemas["RejectReason"], string, string | null]> = [
  ["hook_slabyy", "Слабый хук", "1"],
  ["vizual_artefakty", "Артефакты визуала", "2"],
  ["subtitry_rassinhron", "Рассинхрон субтитров", "3"],
  ["banner_perekryvaet", "Баннер перекрывает смысл", "4"],
  ["zvuk_problema", "Проблема со звуком", "5"],
  ["zapreshchennaya_fraza", "Запрещённая фраза", "6"],
  ["dublikat", "Дубликат", "7"],
  ["ne_ta_dlitelnost", "Не та длительность", "8"],
  ["drugoe", "Другое", "9"],
  ["avtorskiy_sloy_slabyy", "Слабый авторский слой", null],
  ["prava_somnitelny", "Сомнительные права", null],
  ["povtor_shablona", "Повтор шаблона", "0"],
];

export const rejectReasons: Schemas["RejectReasonList"] = {
  items: REJECT_REASONS.map(([code, label, hotkey]) => ({ code, label, hotkey })),
};

export const safeZones: Schemas["SafeZoneList"] = {
  items: [
    {
      platform: "youtube",
      canvas: { w: 1080, h: 1920 },
      dead_zones: [
        { id: "top_bar", x1: 0, y1: 0, x2: 1080, y2: 170 },
        { id: "bottom_ui", x1: 0, y1: 1500, x2: 1080, y2: 1920 },
        { id: "right_rail", x1: 930, y1: 0, x2: 1080, y2: 1920 },
      ],
      conservative_safe_rect: { x1: 90, y1: 380, x2: 990, y2: 1540 },
      source_note: "ТЗ §6, community-derived",
    },
    {
      platform: "vk",
      canvas: { w: 1080, h: 1920 },
      dead_zones: [
        { id: "top_10pct", x1: 0, y1: 0, x2: 1080, y2: 192 },
        { id: "bottom_20pct", x1: 0, y1: 1536, x2: 1080, y2: 1920 },
      ],
      conservative_safe_rect: { x1: 90, y1: 192, x2: 990, y2: 1536 },
      source_note: "ТЗ §6: VK Клипы — верхние 10% и нижние 20% кадра заняты интерфейсом",
    },
  ],
};

export const apiTokens: Schemas["ApiToken"][] = [
  {
    id: 1,
    name: "umka ingest",
    prefix: "ast_k3d9",
    scopes: ["ingest"],
    created_at: now(),
    last_used_at: null,
    expires_at: null,
    revoked_at: null,
  },
];

export const auditLog: Schemas["AuditEntry"][] = [];

let auditId = 1000;
export function audit(entry: Omit<Schemas["AuditEntry"], "id" | "occurred_at" | "request_id">) {
  auditLog.unshift({ ...entry, id: auditId++, occurred_at: now(), request_id: null });
}
