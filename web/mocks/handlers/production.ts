/**
 * MSW-моки Конвейера и Приёмки (поток M2-D1) по типам контракта `lib/api/schema.d.ts`.
 *
 * Повторяют поведение services/jobs.py и services/review.py, важное для экранов:
 * - решения по ролику: разрешённые переходы, 422 (approve из qc_failed, reject без причины),
 *   403 (override_qc не у владельца), 409 `job_state_conflict` c `details.decided_by/decided_via`;
 *   ролик `REVIEW_CONFLICT_JOB_ID` «уже решён в Telegram» — любое решение по нему даёт 409;
 * - отмена решения ≤ 10 минут, 409 с `details.reason` (nothing_to_undo, undo_window_expired, …);
 * - список с фильтрами и сортировкой, лента стадий, таймлайн задач, история, сцены, лог задачи (SSE);
 * - создание (одна тема, пачка, клип по диапазону) с `Idempotency-Key`, 423 при паузе бюджета.
 * Видео в моках нет: `media/preview|final` отвечают 404, постер и картинки сцен — SVG.
 *
 * Подключение: `handlers.ts` должен развернуть `productionHandlers` **до** общего 501-обработчика.
 * Каналы и кампании здесь — минимальный набор для формы «Новый ролик»; если каталог (M2-D2) даст свои
 * моки этих адресов, их нужно поставить раньше, а эти удалить.
 */
import { http, HttpResponse, sse } from "msw";

import type { components } from "@/lib/api/schema";

import { budget, readSession, users } from "../db";

type S = components["schemas"];
type Role = S["Role"];
type JobStatus = S["JobStatus"];
type JobStage = S["JobStage"];
type JobKind = S["JobKind"];
type TaskStatus = S["TaskStatus"];
type QcCheck = S["QcCheck"];
type ReviewDecision = S["ReviewDecision"];

const API = "/api/v1";
const START = Date.now();
const MIN = 60_000;
const HOUR = 60 * MIN;
const iso = (offsetMs: number) => new Date(START + offsetMs).toISOString();
const nowIso = () => new Date().toISOString();

/** Ролик, решённый «в Telegram» раньше оператора: на любое решение — 409 (проверка экрана приёмки). */
export const REVIEW_CONFLICT_JOB_ID = 1205;

// ---------------------------------------------------------------- доступ и ошибки

function error(status: number, code: string, message: string, details: Record<string, unknown> = {}) {
  return HttpResponse.json({ error: { code, message, details } } satisfies S["ErrorResponse"], { status });
}

function invalid(field: string, msg: string, type = "value_error") {
  return error(422, "validation_error", msg, { errors: [{ loc: ["body", field], msg, type }] });
}

interface Caller {
  userId: number;
  role: Role;
  name: string;
}

function guard(request: Request, roles?: Role[]): Caller | Response {
  const session = readSession();
  const user = session ? users.find((item) => item.id === session.userId && item.is_active) : undefined;
  if (!session || !user) return error(401, "not_authenticated", "Нужно войти");
  const unsafe = !["GET", "HEAD", "OPTIONS"].includes(request.method.toUpperCase());
  if (unsafe && request.headers.get("X-CSRF-Token") !== session.csrf) {
    return error(403, "csrf_failed", "Нет или неверный X-CSRF-Token");
  }
  if (roles && !roles.includes(user.role)) return error(403, "forbidden", "Недостаточно прав");
  return { userId: user.id, role: user.role, name: user.display_name };
}

const OPERATORS: Role[] = ["admin", "operator"];

// ---------------------------------------------------------------- каталог (минимум для формы)

function channel(
  id: string,
  platform: S["Platform"],
  nicheId: string,
  title: string,
  status: S["ChannelStatus"],
): S["Channel"] {
  return {
    id,
    platform,
    niche_id: nicheId,
    cluster: nicheId,
    title,
    platform_channel_ref: null,
    url: null,
    status,
    replaced_by: null,
    default_product_id: "umka",
    profile: {},
    publishing_plan: { videos_per_day: 3 },
    advanced_features: platform === "youtube",
    subscribers: platform === "youtube" ? 1240 : 310,
    notes: null,
    profile_duplicate_of: [],
    created_at: iso(-40 * 24 * HOUR),
    updated_at: iso(-2 * 24 * HOUR),
  };
}

const CHANNELS: S["Channel"][] = [
  channel("taro_main", "youtube", "taro", "Таро — выбери карту", "active"),
  channel("astro_main", "youtube", "astro", "Астро на каждый день", "active"),
  channel("taro_vk", "vk", "taro", "Таро · VK Клипы", "warming"),
];

const CAMPAIGNS: S["Campaign"][] = [
  {
    id: 7,
    product_id: "umka",
    name: "Umka · осень",
    status: "active",
    banner_template_id: null,
    allocation: "weighted",
    priority: 100,
    targeting: { niche_ids: ["taro", "astro"] },
    starts_at: null,
    ends_at: null,
    notes: null,
    variants_count: 2,
    created_at: iso(-10 * 24 * HOUR),
    updated_at: iso(-1 * 24 * HOUR),
  },
];

function variant(id: number, code: string, name: string): S["BannerVariant"] {
  return {
    id,
    campaign_id: 7,
    product_id: "umka",
    code,
    name,
    status: "active",
    weight: 50,
    layers: [],
    timing: {
      mode: "impulse",
      hook_silence_sec: 3,
      impulse_window_pct: [0.4, 0.6],
      impulse_duration_sec: 5,
      impulse_min_sec: 3,
      fade_in_ms: 200,
      fade_out_ms: 200,
      final_repeat: true,
      final_repeat_sec: 2.5,
      merge_gap_sec: 1,
      persistent_fade_in_ms: 300,
    },
    ad_marking: { required: false, erid: null, advertiser_text: null },
    code_url_template: null,
    effective_code_url_template: "u.umka.chat/{code}",
    voice_cta_text: null,
    pinned_comment_template: null,
    description_cta_template: null,
    notes: null,
    creatives: [],
    version: 1,
    created_at: iso(-10 * 24 * HOUR),
    updated_at: iso(-1 * 24 * HOUR),
  };
}

const VARIANTS: S["BannerVariant"][] = [variant(21, "A", "Домен + расклад"), variant(22, "B", "Креатив «карта дня»")];

// ---------------------------------------------------------------- ролики

const GENERATE_STAGES: JobStage[] = ["script", "voice", "visuals", "render", "qc"];
const CLIP_STAGES: JobStage[] = ["clip_fetch", "clip_analyze", "author_script", "author_voice", "render", "qc"];
const STAGE_COST: Partial<Record<JobStage, number>> = { script: 0.0021, visuals: 0.018, author_script: 0.0018 };
const TERMINAL = new Set<JobStatus>(["published", "rejected", "cancelled"]);
const EDITABLE = new Set<JobStatus>(["draft", "queued", "rework"]);
const REVIEW_STATUSES = new Set<JobStatus>(["pending_review", "qc_failed"]);
const ALLOWED_FROM: Record<"approve" | "rework" | "reject" | "override_qc", JobStatus[]> = {
  approve: ["pending_review"],
  rework: ["pending_review", "qc_failed"],
  reject: ["pending_review", "qc_failed"],
  override_qc: ["qc_failed"],
};
const DEFAULT_REWORK: Record<JobKind, JobStage> = { generate: "script", clip: "author_script" };
const UNDO_WINDOW_MS = 10 * MIN;

interface MockTask {
  id: number;
  stage: JobStage;
  status: TaskStatus;
  attempts: number;
  progress: number | null;
  started_at: string | null;
  finished_at: string | null;
  cost_usd: number;
  error: string | null;
  created_at: string;
}

type MockReview = S["ReviewDecisionOut"] & { undoes_id?: number | null };

interface MockJob {
  id: number;
  video_uid: string;
  kind: JobKind;
  status: JobStatus;
  stage: JobStage | null;
  channel_id: string;
  topic: string | null;
  priority: number;
  duration_sec: number | null;
  author_share: number | null;
  attempt_count: number;
  banner_variant_id: number | null;
  campaign_id: number | null;
  parent_job_id: number | null;
  error: string | null;
  error_class: string | null;
  qc_failed_checks: QcCheck[];
  qc_warnings: S["QcWarning"][];
  flags: S["ReviewFlag"][];
  banner_issue: boolean;
  hook_pattern: string | null;
  voiceover: string | null;
  scene_count: number;
  created_at: string;
  updated_at: string;
  ready_at: string | null;
  tasks: MockTask[];
  reviews: MockReview[];
  history: S["JobHistoryItem"][];
  /** Уже решён в Telegram, но экран об этом ещё не знает: первое решение из веба получит 409. */
  decided_elsewhere: { decision: ReviewDecision; by: string; byId: number; status: JobStatus } | null;
  scheduled_for: string | null;
}

let nextJobId = 1300;
let nextTaskId = 90_000;
let nextReviewId = 5000;

function uidFor(id: number): string {
  let h = (id * 2654435761) >>> 0;
  let out = "";
  for (let i = 0; i < 12; i += 1) {
    h = (h * 1103515245 + 12345) >>> 0;
    out += "0123456789abcdef"[(h >>> 16) & 15];
  }
  return out;
}

const SLUG_RE = /^[a-z0-9][a-z0-9_-]{1,39}$/;

/**
 * Канал по id. Список каналов в интегрированных моках отдаёт каталог (M2-D2, его обработчики стоят
 * раньше) — там могут быть каналы, которых нет здесь: такой канал считается YouTube-каналом ниши taro.
 */
function channelOf(id: string): S["Channel"] {
  const known = CHANNELS.find((item) => item.id === id);
  if (known) return known;
  const platform: S["Platform"] = id.endsWith("_vk") ? "vk" : "youtube";
  return channel(id, platform, id.startsWith("astro") ? "astro" : "taro", id, "active");
}

function stagesOf(kind: JobKind): JobStage[] {
  return kind === "clip" ? CLIP_STAGES : GENERATE_STAGES;
}

/** Задачи стадий, согласованные со статусом ролика. */
function buildTasks(job: MockJob, progress: number | null, baseAt: number): MockTask[] {
  const order = stagesOf(job.kind);
  const tasks: MockTask[] = [];
  const allDone = !["draft", "queued", "processing", "failed", "cancelled", "rework"].includes(job.status);
  const upto = allDone ? order.length : job.stage ? order.indexOf(job.stage) : -1;
  let t = baseAt;
  for (let i = 0; i < order.length; i += 1) {
    const stage = order[i];
    if (!allDone && i > upto) break;
    if (job.status === "draft") break;
    let status: TaskStatus = "succeeded";
    if (!allDone && i === upto) {
      status =
        job.status === "processing"
          ? "running"
          : job.status === "failed"
            ? "failed"
            : job.status === "cancelled"
              ? "cancelled"
              : job.status === "rework"
                ? "succeeded"
                : "queued";
    }
    const duration = stage === "render" ? 95_000 : stage === "qc" ? 21_000 : stage === "visuals" ? 34_000 : 6_000;
    tasks.push({
      id: nextTaskId++,
      stage,
      status,
      attempts: status === "failed" ? 3 : 1,
      progress: status === "running" ? progress : status === "succeeded" ? 100 : null,
      started_at: status === "queued" ? null : new Date(t).toISOString(),
      finished_at: status === "running" || status === "queued" ? null : new Date(t + duration).toISOString(),
      cost_usd: status === "succeeded" ? (STAGE_COST[stage] ?? 0) : 0,
      error: status === "failed" ? job.error : null,
      created_at: new Date(t - 2000).toISOString(),
    });
    t += duration + 3000;
  }
  return tasks;
}

interface Seed {
  id: number;
  kind?: JobKind;
  status: JobStatus;
  stage?: JobStage | null;
  channel: string;
  topic: string | null;
  duration?: number | null;
  ageMin: number;
  readyMin?: number;
  progress?: number | null;
  variant?: number | null;
  attempt?: number;
  error?: string;
  errorClass?: string;
  failed?: QcCheck[];
  warnings?: S["QcWarning"][];
  flags?: S["ReviewFlag"][];
  authorShare?: number | null;
  bannerIssue?: boolean;
  priority?: number;
  hook?: string;
  voiceover?: string;
  parent?: number | null;
}

const VOICEOVERS = [
  "Остановись. Эта карта выпала тебе не случайно. Сегодня тот, о ком ты думаешь, тоже вспоминает тебя. Но первым он не напишет. Выбери карту, и я скажу, стоит ли делать шаг.",
  "Меркурий развернулся, и деньги снова в игре. Три знака почувствуют это уже на этой неделе. Проверь, есть ли ты в списке, и не пропусти свой момент.",
  "Башня пугает, но это не конец. Это расчистка места под новое. Если она выпала тебе сегодня, отпусти то, что держит. Полный расклад по ссылке.",
];

function seedJob(seed: Seed): MockJob {
  const kind = seed.kind ?? "generate";
  const created = START - seed.ageMin * MIN;
  const job: MockJob = {
    id: seed.id,
    video_uid: uidFor(seed.id),
    kind,
    status: seed.status,
    stage:
      seed.stage === undefined
        ? REVIEW_STATUSES.has(seed.status) ||
          seed.status === "approved" ||
          seed.status === "published" ||
          seed.status === "rejected"
          ? "qc"
          : null
        : seed.stage,
    channel_id: seed.channel,
    topic: seed.topic,
    priority: seed.priority ?? 100,
    duration_sec: seed.duration ?? null,
    author_share: seed.authorShare ?? (kind === "clip" ? 0.34 : null),
    attempt_count: seed.attempt ?? 1,
    banner_variant_id: seed.variant ?? null,
    campaign_id: seed.variant ? 7 : null,
    parent_job_id: seed.parent ?? null,
    error: seed.error ?? null,
    error_class: seed.errorClass ?? null,
    qc_failed_checks: seed.failed ?? [],
    qc_warnings: seed.warnings ?? [],
    flags: seed.flags ?? [],
    banner_issue: seed.bannerIssue ?? false,
    hook_pattern: seed.hook ?? (kind === "generate" ? "incomplete_visual" : "question"),
    voiceover: seed.voiceover ?? VOICEOVERS[seed.id % VOICEOVERS.length],
    scene_count: kind === "generate" ? 6 : 0,
    created_at: new Date(created).toISOString(),
    updated_at: iso(-(seed.readyMin ?? Math.max(1, seed.ageMin - 5)) * MIN),
    ready_at: seed.readyMin !== undefined ? iso(-seed.readyMin * MIN) : null,
    tasks: [],
    reviews: [],
    history: [],
    decided_elsewhere: null,
    scheduled_for: null,
  };
  job.tasks = buildTasks(job, seed.progress ?? null, created + 20_000);
  job.history = [
    { at: job.created_at, kind: "status", from: null, to: "queued", by: "Владелец", via: "web", comment: null },
    ...job.tasks
      .filter((task) => task.started_at)
      .map((task) => ({
        at: task.started_at as string,
        kind: "task" as const,
        from: null,
        to: task.stage,
        by: "system",
        via: null,
        comment: task.status === "failed" ? task.error : null,
      })),
  ];
  if (job.ready_at) {
    job.history.push({
      at: job.ready_at,
      kind: "status",
      from: "processing",
      to: seed.status === "qc_failed" ? "qc_failed" : "pending_review",
      by: "system",
      via: null,
      comment: null,
    });
  }
  return job;
}

const JOBS: MockJob[] = [
  seedJob({
    id: 1201,
    status: "pending_review",
    channel: "taro_main",
    topic: "отношения после ссоры",
    duration: 21.4,
    ageMin: 70,
    readyMin: 42,
    variant: 21,
    hook: "incomplete_visual",
  }),
  seedJob({
    id: 1202,
    status: "pending_review",
    channel: "astro_main",
    topic: "ретроградный Меркурий и деньги",
    duration: 19.8,
    ageMin: 64,
    readyMin: 36,
    variant: 22,
    warnings: [{ code: "library_similarity", detail: "похож на ролик 1150", job_id: 1150, similarity: 0.91 }],
    flags: [{ code: "library_similarity", severity: "warn", detail: "похож на ролик 1150" }],
  }),
  seedJob({
    id: 1203,
    status: "qc_failed",
    channel: "taro_main",
    topic: "карта дня: Башня",
    duration: 24.9,
    ageMin: 58,
    readyMin: 30,
    variant: 21,
    failed: ["loudness"],
    bannerIssue: true,
    flags: [{ code: "banner_in_dead_zone", severity: "warn", detail: "creative_in_bottom_dead_zone" }],
  }),
  seedJob({
    id: 1204,
    kind: "clip",
    status: "pending_review",
    channel: "astro_main",
    topic: "Эфир 12.09: про Сатурн",
    duration: 28.2,
    ageMin: 55,
    readyMin: 24,
    variant: 22,
    authorShare: 0.34,
    hook: "question",
    voiceover:
      "Сатурн уходит из твоего знака, и вот что это значит. Ведущая объясняет, почему следующие три месяца решают всё. Мой вывод: не спеши с решениями до декабря.",
  }),
  seedJob({
    id: REVIEW_CONFLICT_JOB_ID,
    status: "pending_review",
    channel: "taro_vk",
    topic: "три карты на неделю",
    duration: 44.0,
    ageMin: 50,
    readyMin: 18,
    variant: 21,
  }),
  seedJob({
    id: 1210,
    status: "processing",
    stage: "render",
    channel: "taro_main",
    topic: "знак от вселенной",
    ageMin: 12,
    progress: 42,
  }),
  seedJob({
    id: 1211,
    status: "processing",
    stage: "script",
    channel: "astro_main",
    topic: "луна в Скорпионе",
    ageMin: 3,
    progress: null,
  }),
  seedJob({ id: 1212, status: "queued", stage: "script", channel: "taro_main", topic: "таро на выходные", ageMin: 2 }),
  seedJob({
    id: 1213,
    status: "draft",
    stage: null,
    channel: "taro_vk",
    topic: "карта-совет на завтра",
    ageMin: 1,
    priority: 90,
  }),
  seedJob({
    id: 1214,
    status: "failed",
    stage: "voice",
    channel: "astro_main",
    topic: "полнолуние и сны",
    ageMin: 95,
    error: "edge-tts: NoAudioReceived после 3 попыток",
    errorClass: "provider_error",
  }),
  seedJob({
    id: 1215,
    status: "approved",
    channel: "taro_main",
    topic: "что он думает обо мне",
    duration: 18.6,
    ageMin: 300,
    readyMin: 240,
    variant: 21,
  }),
  seedJob({
    id: 1216,
    status: "published",
    channel: "taro_main",
    topic: "расклад на любовь",
    duration: 22.1,
    ageMin: 1500,
    readyMin: 1440,
    variant: 22,
  }),
  seedJob({
    id: 1217,
    status: "rejected",
    channel: "astro_main",
    topic: "гороскоп на пятницу",
    duration: 26.3,
    ageMin: 700,
    readyMin: 650,
  }),
  seedJob({
    id: 1218,
    status: "rework",
    stage: "script",
    channel: "taro_main",
    topic: "карта года",
    duration: 23.0,
    ageMin: 500,
    readyMin: 480,
    attempt: 2,
  }),
  seedJob({
    id: 1219,
    kind: "clip",
    status: "processing",
    stage: "author_voice",
    channel: "astro_main",
    topic: "Эфир 12.09: про Венеру",
    ageMin: 8,
    progress: 70,
  }),
  seedJob({
    id: 1220,
    status: "cancelled",
    stage: "visuals",
    channel: "taro_vk",
    topic: "дубль: карта дня",
    ageMin: 400,
  }),
  seedJob({
    id: 1150,
    status: "published",
    channel: "astro_main",
    topic: "деньги и Меркурий (сентябрь)",
    duration: 20.2,
    ageMin: 9000,
    readyMin: 8900,
    variant: 21,
  }),
];

function addReview(
  job: MockJob,
  decision: ReviewDecision,
  by: { id: number | null; name: string | null },
  via: S["DecidedVia"],
  extra: Partial<MockReview> = {},
  at: string = nowIso(),
): MockReview {
  const review: MockReview = {
    id: nextReviewId++,
    decision,
    reject_reason: null,
    rework_from_stage: null,
    comment: null,
    decided_by: by.id,
    decided_by_name: by.name,
    decided_via: via,
    created_at: at,
    ...extra,
  };
  job.reviews.push(review);
  job.history.push({
    at,
    kind: "review",
    from: null,
    to: decision,
    by: by.name ?? "bot",
    via,
    comment: review.comment,
  });
  return review;
}

// Прошлые решения засеянных роликов (для истории и «прошлых попыток»).
{
  const owner = { id: 1, name: "Владелец" };
  const at = (job: MockJob) => job.ready_at ?? job.updated_at;
  const byId = (id: number) => JOBS.find((job) => job.id === id) as MockJob;
  addReview(byId(1215), "approve", owner, "web", {}, iso(-230 * MIN));
  addReview(byId(1216), "approve", owner, "telegram", {}, iso(-1430 * MIN));
  addReview(
    byId(1217),
    "reject",
    owner,
    "web",
    { reject_reason: "hook_slabyy", comment: "хук не цепляет, долгий заход" },
    iso(-640 * MIN),
  );
  addReview(
    byId(1218),
    "rework",
    owner,
    "web",
    { rework_from_stage: "script", comment: "CTA слишком навязчивый" },
    at(byId(1218)),
  );
  const reworked = byId(1204);
  reworked.attempt_count = 2;
  addReview(
    reworked,
    "rework",
    owner,
    "telegram",
    { rework_from_stage: "author_script", comment: "вывод в конце слабый" },
    iso(-40 * MIN),
  );
  byId(REVIEW_CONFLICT_JOB_ID).decided_elsewhere = {
    decision: "approve",
    by: "Оператор смены",
    byId: 2,
    status: "approved",
  };
}

function findJob(raw: unknown): MockJob | undefined {
  const id = Number(raw);
  return JOBS.find((job) => job.id === id);
}

function touch(job: MockJob, from: JobStatus | null, comment: string | null = null, by = "Владелец") {
  job.updated_at = nowIso();
  if (from !== job.status) {
    job.history.push({ at: job.updated_at, kind: "status", from, to: job.status, by, via: "web", comment });
  }
}

// ---------------------------------------------------------------- представления

function mediaOf(job: MockJob): S["JobMedia"] {
  const rendered =
    job.duration_sec !== null && job.tasks.some((task) => task.stage === "render" && task.status === "succeeded");
  return {
    preview_url: rendered ? `${API}/jobs/${job.id}/media/preview` : null,
    final_url: rendered ? `${API}/jobs/${job.id}/media/final` : null,
    poster_url: rendered || job.status !== "draft" ? `${API}/jobs/${job.id}/media/poster` : null,
  };
}

function qcChecks(job: MockJob): S["QcCheckResult"][] {
  const failed = new Set(job.qc_failed_checks);
  return [
    {
      code: "loudness",
      ok: !failed.has("loudness"),
      measured: failed.has("loudness") ? "-19.8 LUFS" : "-14.2 LUFS",
      threshold: "-14±1.5",
    },
    { code: "silence", ok: !failed.has("silence"), measured: "0.4 с", threshold: "≤ 1.5 с" },
    { code: "black_frames", ok: true, measured: "0", threshold: "0" },
    { code: "frozen_frames", ok: true, measured: "0.0 с", threshold: "≤ 2 с" },
    { code: "duration", ok: true, measured: `${(job.duration_sec ?? 0).toFixed(1)} с`, threshold: "≤ 180 с" },
    { code: "subtitle_sync", ok: true, measured: "38 мс", threshold: "≤ 120 мс" },
    ...(job.kind === "clip"
      ? [
          {
            code: "author_share" as const,
            ok: (job.author_share ?? 0) >= 0.3,
            measured: `${Math.round((job.author_share ?? 0) * 100)}%`,
            threshold: "≥ 30%",
          },
        ]
      : []),
  ];
}

function hasQc(job: MockJob): boolean {
  return job.tasks.some((task) => task.stage === "qc" && task.status === "succeeded");
}

function variantRef(job: MockJob): S["BannerVariantRef"] | null {
  const found = VARIANTS.find((item) => item.id === job.banner_variant_id);
  return found ? { id: found.id, code: found.code, name: found.name } : null;
}

function stageInfos(job: MockJob): S["JobStageInfo"][] {
  const latest = new Map<JobStage, MockTask>();
  for (const task of job.tasks) latest.set(task.stage, task);
  return [...latest.values()].map((task) => ({
    stage: task.stage,
    status: task.status,
    task_id: task.id,
    attempts: task.attempts,
    duration_ms:
      task.started_at && task.finished_at ? Date.parse(task.finished_at) - Date.parse(task.started_at) : null,
    cost_usd: task.cost_usd,
    error: task.status === "failed" || task.status === "cancelled" ? task.error : null,
  }));
}

function costOf(job: MockJob): number {
  return Math.round(job.tasks.reduce((sum, task) => sum + task.cost_usd, 0) * 1e6) / 1e6;
}

function scriptOf(job: MockJob): S["JobScript"] | null {
  if (
    !job.tasks.some(
      (task) => (task.stage === "script" || task.stage === "author_script") && task.status === "succeeded",
    )
  ) {
    return null;
  }
  const text = job.voiceover ?? "";
  const sentences = text.split(/(?<=[.!?])\s+/);
  return {
    hook_text: sentences[0] ?? null,
    body_text: sentences.slice(1, -1).join(" ") || null,
    cta_text: "umka.chat — бесплатный расклад",
    voice_cta_text: sentences[sentences.length - 1] ?? null,
    voiceover_text: text,
  };
}

function jobView(job: MockJob): S["Job"] {
  const ch = channelOf(job.channel_id);
  const qc = hasQc(job)
    ? {
        passed: job.qc_failed_checks.length === 0,
        failed_checks: job.qc_failed_checks,
        checks: qcChecks(job),
        warnings: job.qc_warnings,
      }
    : null;
  return {
    id: job.id,
    video_uid: job.video_uid,
    kind: job.kind,
    status: job.status,
    stage: job.stage,
    channel: { id: ch.id, title: ch.title, platform: ch.platform },
    niche_id: ch.niche_id,
    niche_version: 3,
    product_id: job.banner_variant_id ? "umka" : null,
    campaign_id: job.campaign_id,
    banner_variant: variantRef(job),
    topic: job.topic,
    priority: job.priority,
    scheduled_for: job.scheduled_for,
    duration_sec: job.duration_sec,
    cost_usd: costOf(job),
    author_share: job.author_share,
    qc,
    script: scriptOf(job),
    words: null,
    stages: stageInfos(job),
    media: mediaOf(job),
    reviews: job.reviews.map((review) => ({
      id: review.id,
      decision: review.decision,
      reject_reason: review.reject_reason,
      rework_from_stage: review.rework_from_stage,
      comment: review.comment,
      decided_by: review.decided_by,
      decided_by_name: review.decided_by_name,
      decided_via: review.decided_via,
      created_at: review.created_at,
    })),
    package: null,
    publications:
      job.status === "published"
        ? [
            {
              id: 900 + job.id,
              platform: ch.platform,
              url: null,
              status: "live",
            },
          ]
        : [],
    source:
      job.kind === "clip"
        ? {
            source_id: 5,
            title: "Эфир 12.09",
            clip_candidate_id: 88,
            start_sec: 1204.3,
            end_sec: 1204.3 + (job.duration_sec ?? 22),
          }
        : null,
    parent_job_id: job.parent_job_id,
    error_class: job.error_class,
    error: job.error,
    created_at: job.created_at,
    updated_at: job.updated_at,
  };
}

function rowView(job: MockJob): S["JobRow"] {
  const running = job.tasks.find((task) => task.status === "running");
  return {
    id: job.id,
    video_uid: job.video_uid,
    kind: job.kind,
    status: job.status,
    stage: job.stage,
    channel_id: job.channel_id,
    niche_id: channelOf(job.channel_id).niche_id,
    product_id: job.banner_variant_id ? "umka" : null,
    campaign_id: job.campaign_id,
    banner_variant_id: job.banner_variant_id,
    topic: job.topic,
    priority: job.priority,
    progress: running?.progress ?? null,
    duration_sec: job.duration_sec,
    cost_usd: costOf(job),
    author_share: job.author_share,
    error: job.error,
    created_at: job.created_at,
    updated_at: job.updated_at,
  };
}

function words(job: MockJob): S["ReviewWord"][] {
  const list = (job.voiceover ?? "").split(/\s+/).filter(Boolean);
  const duration = job.duration_sec ?? 20;
  const span = Math.max(1, duration - 0.6);
  const step = span / Math.max(1, list.length);
  return list.map((word, index) => ({
    word,
    start_sec: Math.round((0.3 + index * step) * 100) / 100,
    end_sec: Math.round((0.3 + index * step + step * 0.85) * 100) / 100,
  }));
}

function sceneCuts(job: MockJob): number[] {
  const duration = job.duration_sec ?? 20;
  if (job.kind === "clip") return [3.1, Math.round(duration * 0.55 * 10) / 10, Math.round((duration - 4) * 10) / 10];
  const cuts = [1.2];
  const rest = (duration - 1.2) / 5;
  for (let i = 1; i < 5; i += 1) cuts.push(Math.round((1.2 + rest * i) * 100) / 100);
  return cuts;
}

function bannerOf(job: MockJob): S["ReviewBanner"] {
  const duration = job.duration_sec ?? 20;
  const variantInfo = variantRef(job);
  if (!variantInfo) {
    return {
      variant_id: null,
      variant_code: null,
      creative_sha256s: [],
      fallback_used: false,
      windows: [],
      boxes: [],
      captions_zone: null,
      warnings: [],
    };
  }
  const impulseStart = Math.round(duration * 0.4 * 10) / 10;
  const creativeBottom = job.banner_issue ? 1580 : 1370;
  return {
    variant_id: variantInfo.id,
    variant_code: variantInfo.code,
    creative_sha256s: ["3f9a1c0d5e7b2a4c6e8f0a1b3c5d7e9f1a2b3c4d5e6f708192a3b4c5d6e7f809"],
    fallback_used: false,
    windows: [
      { group: "persistent", start_sec: 0, end_sec: duration },
      { group: "with_creative", start_sec: impulseStart, end_sec: Math.round((impulseStart + 5) * 10) / 10 },
      { group: "with_creative", start_sec: Math.round((duration - 2.5) * 10) / 10, end_sec: duration },
    ],
    boxes: [
      { layer_id: "micro_brand", kind: "micro_brand", x1: 65, y1: 198, x2: 470, y2: 256 },
      { layer_id: "creative_main", kind: "creative", x1: 65, y1: creativeBottom - 250, x2: 890, y2: creativeBottom },
      { layer_id: "text_code", kind: "text_code", x1: 65, y1: creativeBottom + 10, x2: 420, y2: creativeBottom + 58 },
    ],
    captions_zone: { x1: 90, y1: 960, x2: 990, y2: 1100 },
    warnings: job.banner_issue
      ? [
          {
            code: "creative_in_bottom_dead_zone",
            severity: "warn",
            layer_id: "creative_main",
            profile_id: "youtube_shorts",
            message: "Креатив заходит в нижнюю зону интерфейса YouTube Shorts (подпись, кнопки).",
            details: {},
          },
        ]
      : [],
  };
}

function contextOf(job: MockJob): S["ReviewContext"] {
  const media = mediaOf(job);
  const nearest = job.qc_warnings.find((warning) => warning.code === "library_similarity");
  return {
    job: {
      id: job.id,
      video_uid: job.video_uid,
      kind: job.kind,
      status: job.status,
      channel_id: job.channel_id,
      niche_id: channelOf(job.channel_id).niche_id,
      product_id: job.banner_variant_id ? "umka" : null,
      banner_variant_id: job.banner_variant_id,
      attempt_count: job.attempt_count,
    },
    media: { ...media, duration_sec: job.duration_sec },
    script: {
      topic: job.topic,
      hook_pattern: job.hook_pattern,
      voiceover_text: job.voiceover,
      cta_text: "umka.chat — бесплатный расклад",
      words: job.duration_sec ? words(job) : [],
    },
    banner: bannerOf(job),
    scene_cuts_sec: job.duration_sec ? sceneCuts(job) : [],
    qc: hasQc(job) ? { passed: job.qc_failed_checks.length === 0, checks: qcChecks(job) } : null,
    flags: job.flags,
    similarity: nearest
      ? { nearest_job_id: nearest.job_id ?? null, score: nearest.similarity ?? null, method: "dhash" }
      : null,
    cost_usd: costOf(job),
    previous_attempts: job.reviews
      .filter((review) => review.decision === "rework" || review.decision === "reject")
      .filter((review) => !job.reviews.some((other) => other.undoes_id === review.id))
      .map((review) => ({
        job_id: job.id,
        decision: review.decision,
        reject_reason: review.reject_reason,
        comment: review.comment,
      })),
  };
}

function taskView(job: MockJob, task: MockTask): S["Task"] {
  const heavy = task.stage === "render" || task.stage === "qc" || task.stage === "clip_analyze";
  return {
    id: task.id,
    kind: "job_stage",
    resource_class: heavy ? "heavy" : "light",
    status: task.status,
    priority: task.stage === "qc" ? 20 : task.stage === "render" ? 30 : 50,
    run_after: task.created_at,
    job_id: job.id,
    source_id: null,
    stage: task.stage,
    payload: {},
    dedup_key: `job:${job.id}:${task.stage}:${job.attempt_count}`,
    attempts: task.attempts,
    max_attempts: heavy ? 2 : 3,
    locked_by: task.status === "running" ? "box-2101-heavy" : null,
    heartbeat_at: task.status === "running" ? nowIso() : null,
    cancel_requested: false,
    progress: task.progress,
    started_at: task.started_at,
    finished_at: task.finished_at,
    error_class: task.status === "failed" ? job.error_class : null,
    last_error: task.status === "failed" ? task.error : null,
    defer_reason: null,
    result: task.status === "succeeded" ? { ok: true } : null,
    created_at: task.created_at,
    updated_at: task.finished_at ?? task.started_at ?? task.created_at,
  };
}

function logLines(job: MockJob, task: MockTask): string[] {
  const stamp = (offset: number) =>
    new Date(Date.parse(task.started_at ?? task.created_at) + offset).toISOString().slice(11, 19);
  const lines = [
    `${stamp(0)} INFO  task #${task.id} job=${job.video_uid} stage=${task.stage} attempt=${task.attempts}`,
    `${stamp(400)} INFO  допуск: диск 11.4 ГБ, load1 1.9, psi.mem10 0.4 — ок`,
  ];
  switch (task.stage) {
    case "script":
      lines.push(
        `${stamp(900)} INFO  llm openai_compat (umka.chat/v1) → 812 токенов, 1.9 с`,
        `${stamp(1300)} INFO  словарный фильтр: 0 совпадений`,
      );
      break;
    case "voice":
      lines.push(`${stamp(700)} INFO  edge-tts ru-RU-SvetlanaNeural, boundary=WordBoundary`);
      if (task.status === "failed") lines.push(`${stamp(9000)} ERROR NoAudioReceived (попытка 3/3)`);
      else lines.push(`${stamp(4800)} INFO  голос 21.4 с, слов 41`);
      break;
    case "visuals":
      for (let i = 1; i <= 6; i += 1)
        lines.push(`${stamp(i * 5000)} INFO  fal flux-schnell сцена ${i}/6 seed=${4100 + i}`);
      break;
    case "render":
      lines.push(`${stamp(1000)} INFO  ffmpeg -threads 2 (nice 10), сцены 6, петля ок`);
      for (let pct = 10; pct <= (task.status === "running" ? (task.progress ?? 40) : 100); pct += 10) {
        lines.push(`${stamp(1000 + pct * 900)} INFO  progress ${pct}% frame=${Math.round(pct * 6.4)} speed=0.9x`);
      }
      break;
    case "qc":
      lines.push(
        `${stamp(1200)} INFO  ebur128 -14.2 LUFS, тишина 0.4 с, чёрные кадры 0`,
        `${stamp(5200)} INFO  dHash против 200 роликов канала: max 0.62`,
      );
      break;
    default:
      lines.push(`${stamp(1500)} INFO  готово`);
  }
  if (task.status === "succeeded") lines.push(`${stamp(60_000)} INFO  стадия завершена`);
  return lines;
}

function findTask(raw: unknown): { job: MockJob; task: MockTask } | undefined {
  const id = Number(raw);
  for (const job of JOBS) {
    const task = job.tasks.find((item) => item.id === id);
    if (task) return { job, task };
  }
  return undefined;
}

function latestDecision(job: MockJob): MockReview | undefined {
  const undone = new Set(
    job.reviews.map((review) => review.undoes_id).filter((id): id is number => typeof id === "number"),
  );
  return [...job.reviews].reverse().find((review) => review.decision !== "undo" && !undone.has(review.id));
}

function conflictDetails(job: MockJob, extra: Record<string, unknown> = {}) {
  const last = latestDecision(job);
  return {
    job_id: job.id,
    status: job.status,
    ...(last
      ? {
          decision: last.decision,
          decided_by: last.decided_by_name,
          decided_by_id: last.decided_by,
          decided_via: last.decided_via,
          decided_at: last.created_at,
        }
      : {}),
    ...extra,
  };
}

/** Перезапуск со стадии: задачи стадий ≥ stage заменяются новой очередной. */
function restartFrom(job: MockJob, stage: JobStage) {
  const order = stagesOf(job.kind);
  const index = order.indexOf(stage);
  job.tasks = job.tasks.filter((task) => order.indexOf(task.stage) < index);
  job.tasks.push({
    id: nextTaskId++,
    stage,
    status: "queued",
    attempts: 0,
    progress: null,
    started_at: null,
    finished_at: null,
    cost_usd: 0,
    error: null,
    created_at: nowIso(),
  });
  job.stage = stage;
}

function posterSvg(job: MockJob): string {
  const hue = (job.id * 47) % 360;
  const title = (job.topic ?? "без темы").replace(/[<&>]/g, "");
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1920" viewBox="0 0 1080 1920">
<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="hsl(${hue},45%,22%)"/><stop offset="1" stop-color="hsl(${(hue + 60) % 360},40%,8%)"/></linearGradient></defs>
<rect width="1080" height="1920" fill="url(#g)"/>
<circle cx="540" cy="760" r="260" fill="hsl(${hue},60%,55%)" opacity="0.25"/>
<text x="540" y="760" font-family="sans-serif" font-size="64" font-weight="700" fill="#fff" text-anchor="middle">${title.slice(0, 26)}</text>
<text x="540" y="850" font-family="monospace" font-size="40" fill="#fff" opacity="0.7" text-anchor="middle">${job.video_uid} · мок</text>
</svg>`;
}

function sceneSvg(job: MockJob, idx: number): string {
  const hue = (job.id * 31 + idx * 55) % 360;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="540" height="960" viewBox="0 0 540 960">
<rect width="540" height="960" fill="hsl(${hue},35%,20%)"/><circle cx="270" cy="420" r="${120 + idx * 12}" fill="hsl(${hue},55%,50%)" opacity="0.5"/>
<text x="270" y="880" font-family="monospace" font-size="36" fill="#fff" text-anchor="middle">сцена ${idx + 1}</text></svg>`;
}

const idempotency = new Map<string, unknown>();

function createJob(
  caller: Caller,
  body: {
    kind: JobKind;
    channel_id: string;
    topic?: string | null;
    priority?: number;
    start?: boolean;
    banner_variant_id?: number | null;
    campaign_id?: number | null;
    duration?: number | null;
  },
): MockJob {
  const id = nextJobId++;
  const start = body.start !== false;
  const job: MockJob = {
    ...seedJob({
      id,
      kind: body.kind,
      status: start ? "queued" : "draft",
      stage: start ? stagesOf(body.kind)[body.kind === "clip" ? 1 : 0] : null,
      channel: body.channel_id,
      topic: body.topic ?? null,
      ageMin: 0,
      priority: body.priority ?? 100,
      variant: body.banner_variant_id ?? null,
    }),
  };
  job.created_at = nowIso();
  job.updated_at = job.created_at;
  job.history = [
    { at: job.created_at, kind: "status", from: null, to: job.status, by: caller.name, via: "web", comment: null },
  ];
  job.campaign_id = body.campaign_id ?? (body.banner_variant_id ? 7 : null);
  JOBS.unshift(job);
  return job;
}

// ---------------------------------------------------------------- обработчики

export const productionHandlers = [
  // ---------- справочники формы «Новый ролик»
  http.get(`${API}/channels`, ({ request }) => {
    const g = guard(request);
    if (g instanceof Response) return g;
    const url = new URL(request.url);
    const statuses = url.searchParams.getAll("status");
    const platform = url.searchParams.get("platform");
    const items = CHANNELS.filter(
      (item) => (!statuses.length || statuses.includes(item.status)) && (!platform || item.platform === platform),
    );
    return HttpResponse.json({ items, total: items.length, limit: 200, offset: 0 } satisfies S["ChannelPage"]);
  }),
  http.get(`${API}/campaigns`, ({ request }) => {
    const g = guard(request);
    if (g instanceof Response) return g;
    const statuses = new URL(request.url).searchParams.getAll("status");
    const items = CAMPAIGNS.filter((item) => !statuses.length || statuses.includes(item.status));
    return HttpResponse.json({ items, total: items.length, limit: 200, offset: 0 } satisfies S["CampaignPage"]);
  }),
  http.get(`${API}/campaigns/:campaignId/variants`, ({ request, params }) => {
    const g = guard(request);
    if (g instanceof Response) return g;
    const items = VARIANTS.filter((item) => item.campaign_id === Number(params.campaignId));
    return HttpResponse.json({ items } satisfies S["BannerVariantList"]);
  }),

  // ---------- список, лента стадий, создание
  http.get(`${API}/jobs/stage-counts`, ({ request }) => {
    const g = guard(request);
    if (g instanceof Response) return g;
    const counts = new Map<string, S["StageCount"]>();
    for (const job of JOBS) {
      const key = `${job.status}:${job.stage ?? ""}`;
      const item = counts.get(key) ?? { status: job.status, stage: job.stage, count: 0 };
      item.count += 1;
      counts.set(key, item);
    }
    return HttpResponse.json({ items: [...counts.values()] } satisfies S["StageCountList"]);
  }),
  http.get(`${API}/jobs`, ({ request }) => {
    const g = guard(request);
    if (g instanceof Response) return g;
    const url = new URL(request.url);
    const statuses = url.searchParams.getAll("status");
    const stages = url.searchParams.getAll("stage");
    const kind = url.searchParams.get("kind");
    const channelId = url.searchParams.get("channel_id");
    const q = (url.searchParams.get("q") ?? "").trim().toLowerCase();
    const sort = url.searchParams.get("sort") ?? "-created_at";
    const limit = Math.min(200, Number(url.searchParams.get("limit") ?? 50));
    const offset = Number(url.searchParams.get("offset") ?? 0);
    let items = JOBS.filter(
      (job) =>
        (!statuses.length || statuses.includes(job.status)) &&
        (!stages.length || (job.stage !== null && stages.includes(job.stage))) &&
        (!kind || job.kind === kind) &&
        (!channelId || job.channel_id === channelId) &&
        (!q || job.video_uid.includes(q) || (job.topic ?? "").toLowerCase().includes(q) || String(job.id) === q),
    ).map(rowView);
    const field = sort.replace(/^-/, "") as keyof S["JobRow"];
    const dir = sort.startsWith("-") ? -1 : 1;
    items = items.sort((a, b) => {
      const av = a[field] ?? -Infinity;
      const bv = b[field] ?? -Infinity;
      return (av < bv ? -1 : av > bv ? 1 : b.id - a.id) * dir;
    });
    return HttpResponse.json({
      items: items.slice(offset, offset + limit),
      total: items.length,
      limit,
      offset,
    } satisfies S["JobPage"]);
  }),
  http.post(`${API}/jobs/batch`, async ({ request }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const key = request.headers.get("Idempotency-Key");
    if (key && idempotency.has(`batch:${key}`))
      return HttpResponse.json(idempotency.get(`batch:${key}`) as S["JobList"]);
    if (budget.paused) return error(423, "budget_paused", "Платные шаги на паузе");
    const body = (await request.json()) as S["JobBatchCreate"];
    if (!SLUG_RE.test(body.channel_id)) return invalid("channel_id", "Недопустимый id канала");
    if (!body.topics?.length || body.topics.length > 20) return invalid("topics", "От 1 до 20 тем");
    const items = body.topics.map((topic) =>
      jobView(
        createJob(g, {
          kind: "generate",
          channel_id: body.channel_id,
          topic,
          start: body.start,
          banner_variant_id: body.banner_variant_id,
          campaign_id: body.campaign_id,
        }),
      ),
    );
    const out: S["JobList"] = { items };
    if (key) idempotency.set(`batch:${key}`, out);
    return HttpResponse.json(out);
  }),
  http.post(`${API}/jobs`, async ({ request }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const key = request.headers.get("Idempotency-Key");
    if (key && idempotency.has(`jobs:${key}`))
      return HttpResponse.json(idempotency.get(`jobs:${key}`) as S["Job"], { status: 201 });
    if (budget.paused) return error(423, "budget_paused", "Платные шаги на паузе");
    const body = (await request.json()) as S["JobCreate"];
    if (!SLUG_RE.test(body.channel_id)) return invalid("channel_id", "Недопустимый id канала");
    const ch = channelOf(body.channel_id);
    if (body.kind === "clip") {
      const src = body.source;
      if (!src && !body.clip_candidate_id) return invalid("source", "Нужен кандидат или диапазон источника");
      if (src) {
        if (src.source_id !== 5) return error(404, "source_not_found", "Источник не найден (в моках есть только №5)");
        const length = src.end_sec - src.start_sec;
        const limit = ch.platform === "vk" ? 60 : 180;
        if (length < 3 || length > limit) return invalid("source.end_sec", `Длина клипа 3–${limit} с`);
      }
    }
    const job = createJob(g, {
      kind: body.kind,
      channel_id: body.channel_id,
      topic: body.kind === "generate" ? (body.topic ?? null) : "клип: Эфир 12.09",
      priority: body.priority,
      start: body.start,
      banner_variant_id: body.banner_variant_id,
      campaign_id: body.campaign_id,
    });
    const out = jobView(job);
    if (key) idempotency.set(`jobs:${key}`, out);
    return HttpResponse.json(out, { status: 201 });
  }),

  // ---------- приёмка
  http.get(`${API}/review-queue`, ({ request }) => {
    const g = guard(request);
    if (g instanceof Response) return g;
    const channelId = new URL(request.url).searchParams.get("channel_id");
    const items = JOBS.filter((job) => REVIEW_STATUSES.has(job.status) && (!channelId || job.channel_id === channelId))
      .sort(
        (a, b) =>
          (a.ready_at ?? a.updated_at).localeCompare(b.ready_at ?? b.updated_at) ||
          a.priority - b.priority ||
          a.id - b.id,
      )
      .map((job): S["ReviewQueueItem"] => ({
        job_id: job.id,
        video_uid: job.video_uid,
        kind: job.kind,
        status: job.status,
        channel_id: job.channel_id,
        niche_id: channelOf(job.channel_id).niche_id,
        poster_url: mediaOf(job).poster_url,
        preview_url: mediaOf(job).preview_url,
        duration_sec: job.duration_sec,
        attempt_count: job.attempt_count,
        flags: job.flags,
        ready_at: job.ready_at,
      }));
    return HttpResponse.json({ items } satisfies S["ReviewQueue"]);
  }),
  http.get(`${API}/jobs/:jobId/review-context`, ({ request, params }) => {
    const g = guard(request);
    if (g instanceof Response) return g;
    const job = findJob(params.jobId);
    if (!job) return error(404, "job_not_found", "Ролик не найден");
    return HttpResponse.json(contextOf(job));
  }),
  http.post(`${API}/jobs/:jobId/review/undo`, ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const job = findJob(params.jobId);
    if (!job) return error(404, "job_not_found", "Ролик не найден");
    const last = latestDecision(job);
    if (!last)
      return error(409, "job_state_conflict", "Нечего отменять", conflictDetails(job, { reason: "nothing_to_undo" }));
    const expected: JobStatus[] =
      last.decision === "approve" || last.decision === "override_qc"
        ? ["approved"]
        : last.decision === "reject"
          ? ["rejected"]
          : ["rework", "processing"];
    if (!expected.includes(job.status)) {
      return error(
        409,
        "job_state_conflict",
        "Статус ролика уже изменился",
        conflictDetails(job, { reason: job.status === "published" ? "published" : "status_changed" }),
      );
    }
    if (Date.now() - Date.parse(last.created_at) > UNDO_WINDOW_MS) {
      return error(
        409,
        "job_state_conflict",
        "Отменить можно в течение 10 минут",
        conflictDetails(job, { reason: "undo_window_expired" }),
      );
    }
    if (
      last.decision === "rework" &&
      job.status === "processing" &&
      job.tasks.some((task) => task.status === "running")
    ) {
      return error(
        409,
        "job_state_conflict",
        "Доработка уже выполняется — отменить нельзя",
        conflictDetails(job, { reason: "rework_in_progress" }),
      );
    }
    const before = job.status;
    job.status = last.decision === "override_qc" || job.qc_failed_checks.length ? "qc_failed" : "pending_review";
    job.stage = "qc";
    if (last.decision === "rework") {
      job.attempt_count = Math.max(1, job.attempt_count - 1);
      job.tasks = buildTasks(job, null, Date.parse(job.created_at) + 20_000);
    }
    addReview(job, "undo", { id: g.userId, name: g.name }, "web", { undoes_id: last.id });
    touch(job, before, null, g.name);
    return HttpResponse.json(jobView(job));
  }),
  http.post(`${API}/jobs/:jobId/review`, async ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const job = findJob(params.jobId);
    if (!job) return error(404, "job_not_found", "Ролик не найден");
    const body = (await request.json()) as S["ReviewRequest"];
    const allowed = ALLOWED_FROM[body.decision];
    if (!allowed) return invalid("decision", "Неизвестное решение", "decision");
    if (!allowed.includes(body.expected_status)) {
      return invalid(
        "expected_status",
        body.decision === "approve"
          ? "Ролик с проваленным QC принимает только admin (override_qc)"
          : `Решение ${body.decision} невозможно из ${body.expected_status}`,
        "decision_not_allowed",
      );
    }
    if (body.decision === "reject" && !body.reject_reason)
      return invalid("reject_reason", "Укажите причину отклонения", "reject_reason_required");
    if (body.decision === "override_qc" && g.role !== "admin") {
      return error(403, "forbidden", "Принять вопреки QC может только admin", { required_role: "admin" });
    }
    if (job.decided_elsewhere) {
      // Кнопка в Telegram нажата раньше: сервер уже сменил статус — у веба 409 с тем, кто решил.
      const other = job.decided_elsewhere;
      job.decided_elsewhere = null;
      const before = job.status;
      job.status = other.status;
      addReview(
        job,
        other.decision,
        { id: other.byId, name: other.by },
        "telegram",
        {},
        new Date(Date.now() - 20_000).toISOString(),
      );
      touch(job, before, null, other.by);
    }
    if (job.status !== body.expected_status) {
      return error(409, "job_state_conflict", "Решение по ролику уже принято", conflictDetails(job));
    }
    const before = job.status;
    let stage: JobStage | null = null;
    if (body.decision === "approve" || body.decision === "override_qc") {
      job.status = "approved";
    } else if (body.decision === "reject") {
      job.status = "rejected";
    } else {
      stage = body.rework_from_stage ?? DEFAULT_REWORK[job.kind];
      if (!stagesOf(job.kind).includes(stage))
        return invalid("rework_from_stage", `Стадия ${stage} не относится к ролику`, "stage_not_in_track");
      job.attempt_count += 1;
      if (body.auto_restart !== false) {
        job.status = "processing";
        restartFrom(job, stage);
      } else {
        job.status = "rework";
        job.stage = stage;
      }
    }
    addReview(job, body.decision, { id: g.userId, name: g.name }, "web", {
      reject_reason: body.reject_reason ?? null,
      rework_from_stage: stage,
      comment: body.comment ?? null,
    });
    touch(job, before, body.comment ?? null, g.name);
    return HttpResponse.json(jobView(job));
  }),

  // ---------- ролик
  http.get(`${API}/jobs/:jobId/tasks`, ({ request, params }) => {
    const g = guard(request);
    if (g instanceof Response) return g;
    const job = findJob(params.jobId);
    if (!job) return error(404, "job_not_found", "Ролик не найден");
    return HttpResponse.json({
      items: job.tasks.map((task) => ({
        stage: task.stage,
        status: task.status,
        task_id: task.id,
        attempts: task.attempts,
        started_at: task.started_at,
        finished_at: task.finished_at,
        duration_ms:
          task.started_at && task.finished_at ? Date.parse(task.finished_at) - Date.parse(task.started_at) : null,
        cost_usd: task.cost_usd,
        error: task.error,
        progress: task.progress,
      })),
    } satisfies S["JobTaskList"]);
  }),
  http.get(`${API}/jobs/:jobId/history`, ({ request, params }) => {
    const g = guard(request);
    if (g instanceof Response) return g;
    const job = findJob(params.jobId);
    if (!job) return error(404, "job_not_found", "Ролик не найден");
    const items = [...job.history].sort((a, b) => a.at.localeCompare(b.at));
    return HttpResponse.json({ items } satisfies S["JobHistory"]);
  }),
  http.get(`${API}/jobs/:jobId/similar`, ({ request, params }) => {
    const g = guard(request);
    if (g instanceof Response) return g;
    const job = findJob(params.jobId);
    if (!job) return error(404, "job_not_found", "Ролик не найден");
    const items = job.qc_warnings
      .filter((warning) => warning.code === "library_similarity" && warning.job_id)
      .map((warning) => ({
        job_id: warning.job_id as number,
        video_uid: uidFor(warning.job_id as number),
        similarity: warning.similarity ?? 0,
        channel_id: job.channel_id,
      }));
    return HttpResponse.json({ items } satisfies S["SimilarJobList"]);
  }),
  http.get(`${API}/jobs/:jobId/scenes/:sceneIdx/image`, ({ params }) => {
    const job = findJob(params.jobId);
    if (!job) return new HttpResponse(null, { status: 404 });
    return new HttpResponse(sceneSvg(job, Number(params.sceneIdx)), { headers: { "Content-Type": "image/svg+xml" } });
  }),
  http.post(`${API}/jobs/:jobId/scenes/:sceneIdx/regenerate`, ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const job = findJob(params.jobId);
    if (!job) return error(404, "job_not_found", "Ролик не найден");
    if (!["pending_review", "qc_failed", "rework", "failed"].includes(job.status)) {
      return error(409, "job_state_conflict", "Сцену можно перегенерировать после рендера (приёмка, доработка, сбой)", {
        status: job.status,
      });
    }
    if (budget.paused) return error(423, "budget_paused", "Платные шаги на паузе");
    const before = job.status;
    job.status = "processing";
    restartFrom(job, "visuals");
    touch(job, before, null, g.name);
    const task = job.tasks[job.tasks.length - 1];
    return HttpResponse.json({ task_id: task.id } satisfies S["TaskAccepted"], { status: 202 });
  }),
  http.get(`${API}/jobs/:jobId/scenes`, ({ request, params }) => {
    const g = guard(request);
    if (g instanceof Response) return g;
    const job = findJob(params.jobId);
    if (!job) return error(404, "job_not_found", "Ролик не найден");
    const ready = job.tasks.some((task) => task.stage === "visuals" && task.status === "succeeded");
    const duration = job.duration_sec ?? 20;
    const items: S["Scene"][] = ready
      ? Array.from({ length: job.scene_count }, (_, idx) => ({
          idx,
          prompt: `tarot card on velvet table, candle light, cinematic, scene ${idx + 1}, dark mystical mood`,
          image_url: `${API}/jobs/${job.id}/scenes/${idx}/image`,
          seed: 4100 + idx,
          duration_sec: idx === 0 ? 1.2 : Math.round(((duration - 1.2) / (job.scene_count - 1)) * 100) / 100,
        }))
      : [];
    return HttpResponse.json({ items } satisfies S["SceneList"]);
  }),
  http.get(`${API}/jobs/:jobId/media/:mediaKind`, ({ params }) => {
    const job = findJob(params.jobId);
    if (!job) return new HttpResponse(null, { status: 404 });
    if (params.mediaKind === "poster") {
      return new HttpResponse(posterSvg(job), { headers: { "Content-Type": "image/svg+xml" } });
    }
    // Видео в моках нет: плеер показывает постер и «Видео не загрузилось».
    return error(404, "media_not_found", "В моках нет видеофайлов");
  }),
  http.post(`${API}/jobs/:jobId/start`, ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const job = findJob(params.jobId);
    if (!job) return error(404, "job_not_found", "Ролик не найден");
    if (job.status !== "draft" && job.status !== "rework") {
      return error(409, "job_state_conflict", "Запустить можно черновик или ролик на доработке", {
        status: job.status,
      });
    }
    if (budget.paused) return error(423, "budget_paused", "Платные шаги на паузе");
    const before = job.status;
    const stage =
      job.status === "rework"
        ? (job.stage ?? DEFAULT_REWORK[job.kind])
        : stagesOf(job.kind)[job.kind === "clip" ? 1 : 0];
    job.status = before === "draft" ? "queued" : "processing";
    restartFrom(job, stage);
    touch(job, before, null, g.name);
    return HttpResponse.json(jobView(job));
  }),
  http.post(`${API}/jobs/:jobId/cancel`, ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const job = findJob(params.jobId);
    if (!job) return error(404, "job_not_found", "Ролик не найден");
    if (TERMINAL.has(job.status))
      return error(409, "job_state_conflict", "Ролик уже в конечном статусе", { status: job.status });
    const before = job.status;
    job.status = "cancelled";
    for (const task of job.tasks) if (task.status === "running" || task.status === "queued") task.status = "cancelled";
    touch(job, before, null, g.name);
    return HttpResponse.json(jobView(job));
  }),
  http.post(`${API}/jobs/:jobId/retry`, async ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const job = findJob(params.jobId);
    if (!job) return error(404, "job_not_found", "Ролик не найден");
    if (job.status !== "failed")
      return error(409, "job_state_conflict", "Перезапуск со стадии — только для упавшего ролика", {
        status: job.status,
      });
    const body = (await request.json().catch(() => ({}))) as S["JobRetry"];
    const stage = body.from_stage ?? job.stage ?? stagesOf(job.kind)[0];
    if (!stagesOf(job.kind).includes(stage)) return invalid("from_stage", "Стадия не относится к ролику");
    const before = job.status;
    job.status = "processing";
    job.error = null;
    job.error_class = null;
    restartFrom(job, stage);
    touch(job, before, null, g.name);
    return HttpResponse.json(jobView(job));
  }),
  http.post(`${API}/jobs/:jobId/clone`, async ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const parent = findJob(params.jobId);
    if (!parent) return error(404, "job_not_found", "Ролик не найден");
    const body = (await request.json().catch(() => ({}))) as S["JobClone"];
    const overrides = body.overrides ?? { start: false };
    const job = createJob(g, {
      kind: parent.kind,
      channel_id: overrides.channel_id ?? parent.channel_id,
      topic: overrides.topic ?? parent.topic,
      priority: overrides.priority ?? parent.priority,
      start: overrides.start ?? false,
      banner_variant_id: overrides.banner_variant_id ?? parent.banner_variant_id,
    });
    job.parent_job_id = parent.id;
    return HttpResponse.json(jobView(job), { status: 201 });
  }),
  http.post(`${API}/jobs/:jobId/adapt`, async ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const parent = findJob(params.jobId);
    if (!parent) return error(404, "job_not_found", "Ролик не найден");
    if (parent.status !== "approved" && parent.status !== "published") {
      return error(409, "job_state_conflict", "Адаптировать можно одобренный или опубликованный ролик", {
        status: parent.status,
      });
    }
    const body = (await request.json()) as S["JobAdapt"];
    if (!SLUG_RE.test(body.channel_id)) return invalid("channel_id", "Недопустимый id канала");
    const target = channelOf(body.channel_id);
    if (target.platform === channelOf(parent.channel_id).platform) {
      return error(422, "platform_mismatch", "Адаптация нужна под другую площадку", { platform: target.platform });
    }
    const job = createJob(g, {
      kind: parent.kind,
      channel_id: target.id,
      topic: parent.topic,
      start: true,
      banner_variant_id: parent.banner_variant_id,
    });
    job.parent_job_id = parent.id;
    job.status = "processing";
    job.voiceover = parent.voiceover;
    job.tasks = parent.tasks
      .filter((task) => task.stage !== "render" && task.stage !== "qc")
      .map((task) => ({ ...task, id: nextTaskId++ }));
    restartFrom(job, "render");
    return HttpResponse.json(jobView(job), { status: 201 });
  }),
  http.patch(`${API}/jobs/:jobId`, async ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const job = findJob(params.jobId);
    if (!job) return error(404, "job_not_found", "Ролик не найден");
    if (!EDITABLE.has(job.status))
      return error(409, "job_state_conflict", "Править можно только черновик, очередь или доработку", {
        status: job.status,
      });
    const body = (await request.json()) as S["JobPatch"];
    if (body.priority !== undefined && body.priority !== null) {
      if (body.priority < 0 || body.priority > 1000) return invalid("priority", "0–1000");
      job.priority = body.priority;
    }
    if (body.topic !== undefined) job.topic = body.topic;
    job.updated_at = nowIso();
    job.history.push({
      at: job.updated_at,
      kind: "edit",
      from: null,
      to: "priority",
      by: g.name,
      via: "web",
      comment: null,
    });
    return HttpResponse.json(jobView(job));
  }),
  http.get(`${API}/jobs/:jobId`, ({ request, params }) => {
    const g = guard(request);
    if (g instanceof Response) return g;
    const job = findJob(params.jobId);
    if (!job) return error(404, "job_not_found", "Ролик не найден");
    return HttpResponse.json(jobView(job));
  }),

  // ---------- задачи
  http.get(`${API}/tasks`, ({ request }) => {
    const g = guard(request);
    if (g instanceof Response) return g;
    const url = new URL(request.url);
    const jobId = url.searchParams.get("job_id");
    const statuses = url.searchParams.getAll("status");
    const items = JOBS.filter((job) => !jobId || job.id === Number(jobId))
      .flatMap((job) => job.tasks.map((task) => taskView(job, task)))
      .filter((task) => !statuses.length || statuses.includes(task.status));
    return HttpResponse.json({ items, total: items.length, limit: 50, offset: 0 } satisfies S["TaskPage"]);
  }),
  sse<{ message: string; end: string }>(`${API}/tasks/:taskId/log/stream`, ({ client, params, request }) => {
    const found = findTask(params.taskId);
    if (!found || !readSession()) {
      client.error();
      return;
    }
    for (const line of logLines(found.job, found.task)) client.send({ data: line });
    if (found.task.status !== "running") {
      client.send({ event: "end", data: JSON.stringify({ task_id: found.task.id }) });
      client.close();
      return;
    }
    let pct = found.task.progress ?? 40;
    const timer = setInterval(() => {
      pct = Math.min(99, pct + 3);
      found.task.progress = pct;
      client.send({ data: `${new Date().toISOString().slice(11, 19)} INFO  progress ${pct}% speed=0.9x` });
    }, 1500);
    request.signal.addEventListener("abort", () => clearInterval(timer));
  }),
  http.get(`${API}/tasks/:taskId`, ({ request, params }) => {
    const g = guard(request);
    if (g instanceof Response) return g;
    const found = findTask(params.taskId);
    if (!found) return error(404, "task_not_found", "Задача не найдена");
    return HttpResponse.json({
      task: taskView(found.job, found.task),
      log_tail: logLines(found.job, found.task),
    } satisfies S["TaskDetail"]);
  }),
  http.post(`${API}/tasks/:taskId/retry`, ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const found = findTask(params.taskId);
    if (!found) return error(404, "task_not_found", "Задача не найдена");
    found.task.status = "queued";
    found.task.error = null;
    return HttpResponse.json(taskView(found.job, found.task));
  }),
  http.post(`${API}/tasks/:taskId/cancel`, ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const found = findTask(params.taskId);
    if (!found) return error(404, "task_not_found", "Задача не найдена");
    found.task.status = "cancelled";
    return HttpResponse.json(taskView(found.job, found.task));
  }),
];
