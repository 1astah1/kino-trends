/**
 * MSW-моки трека нарезки (поток M5-C) по типам контракта `lib/api/schema.d.ts`: источники с правами,
 * tus-загрузки, транскрипт, peaks, раскадровка, склейки, кандидаты, авторский слой, голос автора и
 * оценка оригинальности. Поведение повторяет services/{sources,uploads_tus,clipping}.py там, где это
 * видно экранам: права по правилам движка (422 `rights_rejected`), снаппинг границ к словам,
 * блокирующие флаги (409 `clip_candidate_blocked`), `expected_version` (409 `version_conflict`).
 *
 * Ключа LLM в моках нет: отбор — эвристика, черновик авторского текста — шаблон, оба подписаны.
 * Видео нет (прокси отвечает 404): студия работает «вхолостую» по часам. Ролики создаёт мок Конвейера:
 * `POST /clip-candidates/{id}/jobs` пересылает запрос на `POST /jobs` (kind=clip) — так новый ролик
 * виден в Конвейере и Приёмке.
 *
 * Подключение: `handlers.ts` разворачивает `clippingHandlers` до общего 501-обработчика.
 */
import { http, HttpResponse, type PathParams } from "msw";

import { EMPTY_RIGHTS, type RightsFormValues, validateRights } from "@/components/clipping/rights-rules";
import type { components } from "@/lib/api/schema";

import { readSession, users } from "../db";

type S = components["schemas"];
type Role = S["Role"];
type Word = S["TranscriptWord"];
type Sentence = S["TranscriptSentence"];

const API = "/api/v1";
const START = Date.now();
const MIN = 60_000;
const HOUR = 60 * MIN;
const iso = (offsetMs: number) => new Date(START + offsetMs).toISOString();
const nowIso = () => new Date().toISOString();
const OPERATORS: Role[] = ["admin", "operator"];
const TUS_HEADERS = { "Tus-Resumable": "1.0.0" };

// ---------------------------------------------------------------- доступ и ошибки

function error(status: number, code: string, message: string, details: Record<string, unknown> = {}) {
  return HttpResponse.json({ error: { code, message, details } } satisfies S["ErrorResponse"], { status });
}

function invalid(loc: Array<string | number>, msg: string, type = "value_error") {
  return error(422, "validation_error", "Проверьте поля формы", { errors: [{ loc: ["body", ...loc], msg, type }] });
}

interface Caller {
  userId: number;
  role: Role;
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
  return { userId: user.id, role: user.role };
}

// ---------------------------------------------------------------- детерминированный генератор

function rng(seed: number) {
  let state = seed >>> 0 || 1;
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 4294967296;
  };
}

const TALK_TARO = [
  "Сегодня разберём, почему карта дня — это не предсказание, а подсказка.",
  "Многие думают, что Таро говорит, что будет, но это не так.",
  "Карта показывает, на что обратить внимание прямо сейчас.",
  "Вот пример: выпала Башня, и человек сразу пугается.",
  "А на самом деле Башня про то, что старое больше не держит.",
  "Самая частая ошибка — задавать вопрос с ответом да или нет.",
  "Попробуйте спросить иначе: что мне важно понять в этой ситуации?",
  "Тогда расклад начинает работать как разговор, а не как приговор.",
  "Второй вопрос из чата: сколько раз в день можно тянуть карту?",
  "Честно, одного раза достаточно, иначе вы просто ищете удобный ответ.",
  "Если карта не нравится, запишите её и вернитесь вечером.",
  "Обычно к вечеру становится понятно, о чём она была.",
  "Теперь про старшие арканы, их двадцать два.",
  "Шут открывает путь, а Мир его завершает.",
  "Между ними — вся история о том, как человек взрослеет.",
  "И вот что интересно: люди чаще всего вытягивают Луну осенью.",
  "Луна про сомнения и про то, что не всё видно сразу.",
  "Не нужно принимать решения в день Луны, лучше собрать факты.",
  "Меня часто спрашивают, можно ли гадать самому себе.",
  "Можно, но с одним условием: вы готовы услышать неудобное.",
  "Если ответ заранее известен, карты только подтвердят страх.",
  "Поэтому я советую вести дневник раскладов хотя бы месяц.",
  "Через месяц вы увидите, какие карты приходят к вам снова и снова.",
  "Это и есть ваш личный язык Таро, он у каждого свой.",
  "Последний вопрос на сегодня: верить ли онлайн-раскладам?",
  "Онлайн-расклад честен ровно настолько, насколько честен ваш вопрос.",
  "Случайность одинаковая, что в колоде, что в приложении.",
  "Важна не колода, а внимание, с которым вы смотрите на карту.",
  "Спасибо, что были на эфире, пишите вопросы в комментариях.",
  "В следующий раз разберём расклад на неделю и типичные ловушки.",
];

const TALK_RENDER = [
  "Добрый день, сегодня поговорим о рендере в реальном времени.",
  "Десять лет назад кадр считался минутами, сейчас — миллисекундами.",
  "Главный фокус не в железе, а в том, что мы перестали считать лишнее.",
  "Возьмём тени: раньше их трассировали для каждого пикселя.",
  "Теперь мы кэшируем освещение и обновляем только то, что изменилось.",
  "Это экономит девяносто процентов работы в типичной сцене.",
  "Но у кэша есть цена: он врёт, когда свет двигается быстро.",
  "Поэтому мы смешиваем кэш и трассировку по маске движения.",
  "Покажу на примере: вот коридор с мигающей лампой.",
  "Слева старый подход, справа новый, разница видна сразу.",
  "Самое неожиданное открытие — люди не замечают шум в тенях.",
  "Зато они сразу замечают, если тень запаздывает на кадр.",
  "Отсюда правило: лучше шумно, но вовремя, чем чисто, но поздно.",
  "Вопрос из зала: работает ли это на телефонах?",
  "Да, мы проверяли на среднем телефоне прошлого года.",
  "Тридцать кадров стабильно, если не включать отражения.",
  "Отражения — отдельная история, про них будет следующий доклад.",
  "Спасибо за внимание, слайды выложу в открытый доступ.",
];

interface MockTranscript {
  words: Word[];
  sentences: Sentence[];
  duration: number;
}

function buildTranscript(lines: readonly string[], targetSec: number, seed: number): MockTranscript {
  const rand = rng(seed);
  const words: Word[] = [];
  const sentences: Sentence[] = [];
  let t = 1.2;
  let sid = 0;
  let li = 0;
  while (t < targetSec - 6) {
    const text = lines[li % lines.length];
    li += 1;
    const parts = text.split(/\s+/).filter(Boolean);
    const w0 = words.length;
    const start = t;
    for (const part of parts) {
      const dur = 0.14 + part.replace(/[^\p{L}\p{N}]/gu, "").length * 0.055 + rand() * 0.06;
      words.push({ w: part, s: round3(t), e: round3(t + dur), sid, p: round3(0.82 + rand() * 0.17) });
      t += dur + 0.05 + rand() * 0.08;
    }
    sentences.push({ sid, s: round3(start), e: words[words.length - 1].e, text, w0, w1: words.length - 1 });
    sid += 1;
    t += 0.45 + rand() * 0.9;
  }
  return { words, sentences, duration: round3(t + 3) };
}

function round3(value: number): number {
  return Math.round(value * 1000) / 1000;
}

function buildPeaks(tr: MockTranscript, seed: number): S["SourcePeaks"] {
  const rand = rng(seed);
  const perMin = 2000;
  const step = 60 / perMin;
  const length = Math.floor(tr.duration / step);
  const out = new Array<number>(length);
  let wi = 0;
  for (let i = 0; i < length; i += 1) {
    const t = i * step;
    while (wi < tr.words.length - 1 && tr.words[wi].e < t) wi += 1;
    const word = tr.words[wi];
    const speaking = word && t >= word.s && t <= word.e;
    out[i] = round3(speaking ? 0.25 + rand() * 0.7 : 0.01 + rand() * 0.05);
  }
  return {
    version: 1,
    format: "wavesurfer",
    duration: tr.duration,
    sample_rate: 16000,
    points_per_min: perMin,
    samples_per_point: 480,
    length,
    channels: 1,
    max: 1,
    peaks: [out],
  };
}

function buildScenes(duration: number, seed: number): S["SceneSpan"][] {
  const rand = rng(seed);
  const items: S["SceneSpan"][] = [];
  let t = 0;
  while (t < duration) {
    const len = 14 + rand() * 30;
    items.push({ start_sec: round3(t), end_sec: round3(Math.min(duration, t + len)) });
    t += len;
  }
  return items;
}

// ---------------------------------------------------------------- источники

const STORYBOARD = { interval_sec: 2, tile_w: 160, tile_h: 90, cols: 10, rows: 10 };

interface MockSource {
  id: number;
  title: string;
  origin: S["SourceOrigin"];
  origin_url: string | null;
  /** Статус «как сохранён»; для новых источников обработка идёт по часам от `processingFrom`. */
  status: S["SourceStatus"];
  processingFrom: number | null;
  error: string | null;
  niche_id: string | null;
  rights: S["SourceRights"];
  watermark: S["SourceWatermark"] | null;
  platform: S["SourcePlatformInfo"] | null;
  options: S["SourceOptions"];
  lines: readonly string[];
  targetSec: number;
  seed: number;
  transcript: MockTranscript | null;
  selectionMode: S["SelectionMode"];
  selectedAt: number;
  selectTaskId: number | null;
  selectingUntil: number | null;
  file_bytes: number | null;
  created_at: string;
  updated_at: string;
  /** Статичная картинка конвейера для «застрявших» демо-источников. */
  frozenPipeline?: S["SourcePipelineStep"][];
  frozenProgress?: number | null;
}

const DEFAULT_OPTIONS: S["SourceOptions"] = {
  asr_provider: null,
  selector: "auto",
  target_clip_sec: 30,
  min_clip_sec: 15,
  max_clip_sec: 58,
  max_candidates: 12,
  target_count: 6,
  detail_pass: true,
  tighten_pauses: false,
};

const RIGHTS_WARNING_LABELS: Record<string, string> = {
  author_share_gate_applies: "Чужие права: нужен авторский вклад не меньше 30%",
  cc_by_attribution_in_description: "CC BY: атрибуция обязательна в описании ролика",
  content_id_allowlist_not_confirmed: "Канал не подтверждён в allowlist Content ID правообладателя",
  license_version_unknown: "Не указана версия лицензии CC BY",
  public_domain_check_embedded_music: "Общественное достояние: проверьте, нет ли внутри чужой музыки",
};

function rightsRecord(input: S["SourceRightsIn"], at: string, by: number | null): S["SourceRights"] {
  const own = input.rights_type === "own";
  const codes: string[] = [];
  if (!own) codes.push("author_share_gate_applies");
  if (input.rights_type === "cc_by") {
    codes.push("cc_by_attribution_in_description");
    if (!input.license && !input.license_url) codes.push("license_version_unknown");
  }
  if (
    (input.rights_type === "licensed_written" || input.rights_type === "licensed_platform") &&
    input.allowlist_status !== "allowlisted"
  ) {
    codes.push("content_id_allowlist_not_confirmed");
  }
  if (input.rights_type === "public_domain") codes.push("public_domain_check_embedded_music");
  return {
    rights_type: input.rights_type,
    owner_name: input.owner_name,
    owner_channel_url: input.owner_channel_url ?? null,
    proof_note: input.proof_note,
    proof_file_url: null,
    allowlist_status: input.allowlist_status ?? (own ? "not_needed" : "unknown"),
    attribution_text: input.attribution_text ?? null,
    license: input.license ?? null,
    license_url: input.license_url ?? null,
    valid_until: input.valid_until ?? null,
    warnings: codes.map((code) => ({ code, label: RIGHTS_WARNING_LABELS[code] ?? code })),
    gate_applies: !own,
    recorded_by: by,
    recorded_at: at,
    updated_at: null,
  };
}

const SOURCES: MockSource[] = [
  {
    id: 5,
    title: "Эфир 12.09 · разбор вопросов про Таро",
    origin: "upload",
    origin_url: null,
    status: "ready",
    processingFrom: null,
    error: null,
    niche_id: "taro",
    rights: rightsRecord(
      { rights_type: "own", owner_name: "Канал «Таро — выбери карту»", proof_note: "Запись моего эфира 12.09.2026" },
      iso(-3 * 24 * HOUR),
      1,
    ),
    watermark: { suspect: false, severity: "ok", detail: null, frames_checked: 12, logos_checked: 6 },
    platform: null,
    options: DEFAULT_OPTIONS,
    lines: TALK_TARO,
    targetSec: 420,
    seed: 5,
    transcript: null,
    selectionMode: "heuristic",
    selectedAt: START - 3 * 24 * HOUR + 40 * MIN,
    selectTaskId: 4101,
    selectingUntil: null,
    file_bytes: 1_734_000_000,
    created_at: iso(-3 * 24 * HOUR),
    updated_at: iso(-2 * HOUR),
  },
  {
    id: 6,
    title: "Доклад «Рендер в реальном времени» — CG Event (CC BY)",
    origin: "url",
    origin_url: "https://www.youtube.com/watch?v=cgEvent2024",
    status: "ready",
    processingFrom: null,
    error: null,
    niche_id: null,
    rights: rightsRecord(
      {
        rights_type: "cc_by",
        owner_name: "CG Event",
        proof_note: "Лицензия Creative Commons Attribution указана под видео, проверено 26.09.2026",
        attribution_text:
          "CG Event — «Рендер в реальном времени», youtube.com, CC BY 4.0. Изменения: нарезка и авторский комментарий",
        license: "CC BY 4.0",
        license_url: "https://creativecommons.org/licenses/by/4.0/",
      },
      iso(-24 * HOUR),
      1,
    ),
    watermark: {
      suspect: true,
      severity: "hint",
      detail: "Статичный оверлей в правом верхнем углу (логотип конференции?)",
      frames_checked: 12,
      logos_checked: 6,
    },
    platform: {
      extractor: "youtube",
      video_id: "cgEvent2024",
      webpage_url: "https://www.youtube.com/watch?v=cgEvent2024",
      uploader: "CG Event",
      license: "Creative Commons Attribution license (reuse allowed)",
      upload_date: "20240915",
    },
    options: DEFAULT_OPTIONS,
    lines: TALK_RENDER,
    targetSec: 300,
    seed: 6,
    transcript: null,
    selectionMode: "heuristic",
    selectedAt: START - 23 * HOUR,
    selectTaskId: 4150,
    selectingUntil: null,
    file_bytes: null,
    created_at: iso(-24 * HOUR),
    updated_at: iso(-23 * HOUR),
  },
  {
    id: 7,
    title: "Стрим 20.09 · вопросы зрителей",
    origin: "upload",
    origin_url: null,
    status: "transcribing",
    processingFrom: null,
    error: null,
    niche_id: "taro",
    rights: rightsRecord(
      { rights_type: "own", owner_name: "Канал «Таро — выбери карту»", proof_note: "Мой стрим 20.09.2026" },
      iso(-40 * MIN),
      2,
    ),
    watermark: { suspect: false, severity: "ok", detail: null, frames_checked: 12, logos_checked: 6 },
    platform: null,
    options: DEFAULT_OPTIONS,
    lines: TALK_TARO,
    targetSec: 3900,
    seed: 7,
    transcript: null,
    selectionMode: "heuristic",
    selectedAt: 0,
    selectTaskId: null,
    selectingUntil: null,
    file_bytes: 3_210_000_000,
    created_at: iso(-40 * MIN),
    updated_at: iso(-2 * MIN),
    frozenProgress: 46,
    frozenPipeline: [
      {
        step: "prepare",
        status: "succeeded",
        task_id: 4201,
        progress: 100,
        started_at: iso(-38 * MIN),
        finished_at: iso(-31 * MIN),
      },
      {
        step: "transcribe",
        status: "running",
        task_id: 4205,
        progress: 42,
        chunks_done: 3,
        chunks_total: 7,
        queue_position: null,
        started_at: iso(-30 * MIN),
      },
      { step: "select", status: "pending", task_id: null, progress: null },
      { step: "scenes", status: "pending", task_id: null, progress: null },
    ],
  },
  {
    id: 8,
    title: "Интервью с астрологом (лицензия)",
    origin: "url",
    origin_url: "https://vk.com/video-123_456",
    status: "failed",
    processingFrom: null,
    error: "yt-dlp: видео недоступно (403) — проверьте ссылку и доступ",
    niche_id: "astro",
    rights: rightsRecord(
      {
        rights_type: "licensed_written",
        owner_name: "ИП Звёздная",
        proof_note: "Письмо от 20.09.2026",
        attribution_text: "Интервью — канал «Звёздная», по договору от 20.09.2026",
      },
      iso(-5 * HOUR),
      1,
    ),
    watermark: null,
    platform: null,
    options: DEFAULT_OPTIONS,
    lines: TALK_TARO,
    targetSec: 1200,
    seed: 8,
    transcript: null,
    selectionMode: "heuristic",
    selectedAt: 0,
    selectTaskId: null,
    selectingUntil: null,
    file_bytes: null,
    created_at: iso(-5 * HOUR),
    updated_at: iso(-5 * HOUR + 3 * MIN),
    frozenProgress: null,
    frozenPipeline: [
      {
        step: "download",
        status: "failed",
        task_id: 4180,
        progress: null,
        error: "HTTP 403 от площадки",
        started_at: iso(-5 * HOUR),
        finished_at: iso(-5 * HOUR + 3 * MIN),
      },
      { step: "prepare", status: "pending", task_id: null, progress: null },
      { step: "transcribe", status: "pending", task_id: null, progress: null },
      { step: "select", status: "pending", task_id: null, progress: null },
    ],
  },
  {
    id: 9,
    title: "Вебинар 25.09 · расклады на осень",
    origin: "upload",
    origin_url: null,
    status: "queued",
    processingFrom: null,
    error: null,
    niche_id: "taro",
    rights: rightsRecord(
      { rights_type: "own", owner_name: "Канал «Таро — выбери карту»", proof_note: "Мой вебинар 25.09.2026" },
      iso(-10 * MIN),
      2,
    ),
    watermark: null,
    platform: null,
    options: DEFAULT_OPTIONS,
    lines: TALK_TARO,
    targetSec: 2700,
    seed: 9,
    transcript: null,
    selectionMode: "heuristic",
    selectedAt: 0,
    selectTaskId: null,
    selectingUntil: null,
    file_bytes: 2_050_000_000,
    created_at: iso(-10 * MIN),
    updated_at: iso(-10 * MIN),
    frozenProgress: 0,
    frozenPipeline: [
      { step: "prepare", status: "queued", task_id: 4230, progress: null, queue_position: 2 },
      { step: "transcribe", status: "pending", task_id: null, progress: null },
      { step: "select", status: "pending", task_id: null, progress: null },
      { step: "scenes", status: "pending", task_id: null, progress: null },
    ],
  },
];

let nextSourceId = 20;
let nextCandidateId = 500;
let nextTaskId = 5000;

/** Новые источники «обрабатываются» по часам: подготовка 4 с, распознавание 6 с, отбор 3 с. */
const PHASES: Array<{ status: S["SourceStatus"]; step: S["SourceStep"]; sec: number }> = [
  { status: "preparing", step: "prepare", sec: 4 },
  { status: "transcribing", step: "transcribe", sec: 6 },
  { status: "selecting", step: "select", sec: 3 },
];

function liveState(src: MockSource): {
  status: S["SourceStatus"];
  pipeline: S["SourcePipelineStep"][];
  progress: number | null;
} {
  const selecting = src.selectingUntil !== null && Date.now() < src.selectingUntil;
  if (src.processingFrom === null) {
    if (src.frozenPipeline)
      return { status: src.status, pipeline: src.frozenPipeline, progress: src.frozenProgress ?? null };
    const steps: S["SourcePipelineStep"][] = [
      ...(src.origin === "url"
        ? [{ step: "download" as const, status: "succeeded" as const, task_id: src.seed * 10, progress: 100 }]
        : []),
      { step: "prepare", status: "succeeded", task_id: src.seed * 10 + 1, progress: 100 },
      {
        step: "transcribe",
        status: "succeeded",
        task_id: src.seed * 10 + 2,
        progress: 100,
        chunks_done: 1,
        chunks_total: 1,
      },
      {
        step: "select",
        status: selecting ? "running" : "succeeded",
        task_id: src.selectTaskId,
        progress: selecting ? 50 : 100,
      },
      { step: "scenes", status: "succeeded", task_id: src.seed * 10 + 4, progress: 100 },
    ];
    return { status: selecting ? "selecting" : src.status, pipeline: steps, progress: selecting ? 90 : 100 };
  }
  const elapsed = (Date.now() - src.processingFrom) / 1000;
  let acc = 0;
  const total = PHASES.reduce((sum, p) => sum + p.sec, 0);
  const pipeline: S["SourcePipelineStep"][] = [];
  let status: S["SourceStatus"] = "ready";
  for (const phase of PHASES) {
    const from = acc;
    acc += phase.sec;
    if (elapsed >= acc) {
      pipeline.push({ step: phase.step, status: "succeeded", task_id: src.seed + pipeline.length, progress: 100 });
    } else if (elapsed >= from) {
      status = phase.status;
      const pct = Math.round(((elapsed - from) / phase.sec) * 100);
      pipeline.push({
        step: phase.step,
        status: "running",
        task_id: src.seed + pipeline.length,
        progress: pct,
        ...(phase.step === "transcribe" ? { chunks_done: Math.floor(pct / 34), chunks_total: 3 } : {}),
      });
    } else {
      pipeline.push({ step: phase.step, status: "pending", task_id: null, progress: null });
    }
  }
  if (elapsed >= total && src.status !== "ready") {
    src.status = "ready";
    src.processingFrom = null;
    src.selectedAt = Date.now();
    src.updated_at = nowIso();
    ensureCandidates(src);
  }
  return { status, pipeline, progress: status === "ready" ? 100 : Math.min(99, Math.round((elapsed / total) * 100)) };
}

function transcriptOf(src: MockSource): MockTranscript {
  if (!src.transcript) src.transcript = buildTranscript(src.lines, src.targetSec, src.seed);
  return src.transcript;
}

function hasMedia(src: MockSource): boolean {
  return src.processingFrom === null
    ? src.status === "ready" || src.status === "selecting"
    : liveState(src).status !== "preparing";
}

function selectionInfo(src: MockSource): S["SourceSelectionInfo"] | null {
  if (!src.selectTaskId && src.status !== "ready") return null;
  const cands = CANDIDATES.filter((c) => c.source_id === src.id && !c.superseded && c.created_by_kind === "auto");
  return {
    mode: src.selectionMode,
    label:
      src.selectionMode === "llm"
        ? "LLM umka/gpt-4o-mini"
        : src.selectionMode === "mock"
          ? "Заглушка LLM (mock) — только для проверки"
          : "Эвристика без LLM — ключ LLM не задан",
    llm_configured: false,
    fallback: false,
    params: src.options,
    n_candidates: cands.length,
    n_blocked: cands.filter((c) => c.blocked).length,
    n_requests: 0,
    cost_usd: 0,
    warnings: [],
    task_id: src.selectTaskId,
    finished_at: src.selectedAt ? new Date(src.selectedAt).toISOString() : null,
  };
}

function sourceView(src: MockSource): S["Source"] {
  const live = liveState(src);
  const media = hasMedia(src);
  const ready = live.status === "ready" || live.status === "selecting";
  const tr = ready || live.status === "selecting" ? transcriptOf(src) : null;
  const base = `${API}/sources/${src.id}`;
  return {
    id: src.id,
    title: src.title,
    origin: src.origin,
    origin_url: src.origin_url,
    status: live.status,
    error: src.error,
    language: "ru",
    niche_id: src.niche_id,
    duration_sec: tr ? tr.duration : src.targetSec,
    width: 960,
    height: 540,
    fps: 30,
    video_codec: "h264",
    has_audio: true,
    file_bytes: src.file_bytes,
    sha256: null,
    rights: src.rights,
    watermark_suspect: !!src.watermark?.suspect,
    watermark: src.watermark,
    platform: src.platform,
    options: src.options,
    pipeline: live.pipeline,
    progress: live.progress,
    media: {
      proxy_url: media ? `${base}/media/proxy` : null,
      audio_url: media ? `${base}/media/audio` : null,
      peaks_url: media ? `${base}/peaks` : null,
      storyboard_url: media ? `${base}/storyboard` : null,
      transcript_url: tr ? `${base}/transcript` : null,
    },
    transcript: tr
      ? {
          provider_key: "gigaam_v3_onnx",
          model: "gigaam-v3-e2e-ctc",
          language: "ru",
          words_count: tr.words.length,
          sentences_count: tr.sentences.length,
          duration_sec: tr.duration,
          rtfx: 11.4,
          cost_usd: 0,
          created_at: src.created_at,
        }
      : null,
    selection: ready ? selectionInfo(src) : null,
    candidates_count: CANDIDATES.filter((c) => c.source_id === src.id && !c.superseded && c.status !== "dismissed")
      .length,
    jobs_count: CANDIDATES.filter((c) => c.source_id === src.id && c.job_id !== null).length,
    original_retain_until: null,
    proxy_retain_until: null,
    created_by: src.rights.recorded_by,
    created_at: src.created_at,
    updated_at: src.updated_at,
  };
}

function rowView(src: MockSource): S["SourceRow"] {
  const view = sourceView(src);
  const current = view.pipeline.find(
    (step) => step.status === "running" || step.status === "queued" || step.status === "failed",
  );
  return {
    id: view.id,
    title: view.title,
    origin: view.origin,
    status: view.status,
    error: view.error,
    language: view.language,
    niche_id: view.niche_id,
    duration_sec: view.duration_sec,
    rights_type: view.rights.rights_type,
    watermark_suspect: view.watermark_suspect,
    current_step: current?.step ?? null,
    progress: current?.progress ?? view.progress,
    queue_position: current?.queue_position ?? null,
    candidates_count: view.candidates_count,
    jobs_count: view.jobs_count,
    selection_mode: view.selection?.mode ?? null,
    created_at: view.created_at,
    updated_at: view.updated_at,
  };
}

function findSource(raw: unknown): MockSource | undefined {
  return SOURCES.find((item) => item.id === Number(raw));
}

// ---------------------------------------------------------------- кандидаты

const CANDIDATES: S["ClipCandidate"][] = [];

const FLAG_TEXT = /(гарант|вылеч|точно сбуд|100%)/i;

function candidateFrom(
  src: MockSource,
  w0: number,
  w1: number,
  extra: Partial<S["ClipCandidate"]> & { rand?: () => number } = {},
): S["ClipCandidate"] {
  const tr = transcriptOf(src);
  const rand = extra.rand ?? rng(w0 * 31 + w1);
  const first = tr.words[w0];
  const last = tr.words[w1];
  const lead = round3(Math.min(0.35, Math.max(0.05, first.s - (tr.words[w0 - 1]?.e ?? 0) - 0.05)));
  const tail = round3(Math.min(0.45, Math.max(0.05, (tr.words[w1 + 1]?.s ?? tr.duration) - last.e - 0.05)));
  const start = round3(first.s - lead);
  const end = round3(last.e + tail);
  const text = tr.words
    .slice(w0, w1 + 1)
    .map((w) => w.w)
    .join(" ");
  const duration = round3(end - start);
  const lenFit = Math.max(0, 1 - Math.abs(duration - 30) / 30);
  const llm = {
    hook: round3(4 + rand() * 5),
    standalone: round3(4 + rand() * 5),
    payoff: round3(3 + rand() * 6),
    emotion: round3(3 + rand() * 5),
    niche_fit: round3(5 + rand() * 4),
    novelty: round3(3 + rand() * 5),
  };
  const llmPart =
    (0.3 * llm.hook +
      0.2 * llm.standalone +
      0.2 * llm.payoff +
      0.1 * llm.emotion +
      0.15 * llm.niche_fit +
      0.05 * llm.novelty) /
    10;
  const feat = round3(0.55 + rand() * 0.35);
  const final = round3(0.55 * llmPart + 0.2 * feat + 0.15 * lenFit + 0.1 * 0.4);
  const flags: S["CandidateFlag"][] = FLAG_TEXT.test(text) ? ["guarantee_claim"] : [];
  const unblocked = extra.unblocked_flags ?? [];
  const blockReasons = flags.filter(
    (f) => ["medical_claim", "guarantee_claim", "forbidden_phrase"].includes(f) && !unblocked.includes(f),
  );
  const at = nowIso();
  return {
    id: extra.id ?? nextCandidateId++,
    source_id: src.id,
    start_sec: start,
    end_sec: end,
    duration_sec: duration,
    start_sid: first.sid,
    end_sid: last.sid,
    hook_sid: first.sid,
    start_word_idx: w0,
    end_word_idx: w1,
    snap: { w0, w1, lead_sec: lead, tail_sec: tail, raw_start_sec: first.s, raw_end_sec: last.e },
    final_score: final,
    trend_prior: 0.4,
    score_parts: { llm: round3(llmPart), feat, len_fit: round3(lenFit), trend: 0.4 },
    llm_scores: extra.origin === "manual" ? null : llm,
    features: {
      speech_rate_wps: round3((w1 - w0 + 1) / Math.max(1, last.e - first.s)),
      dead_air_ratio: round3(0.04 + rand() * 0.08),
      first_word_latency: lead,
      ends_on_terminal_punct: /[.!?…]$/.test(last.w),
      n_words: w1 - w0 + 1,
    },
    hook_type:
      extra.origin === "manual"
        ? null
        : (["question", "surprising_fact", "mistake_warning", "direct_address", "story"] as const)[
            Math.floor(rand() * 5)
          ],
    reason: extra.origin === "manual" ? null : "Эвристика: плотная речь, законченная мысль, вопрос зрителя в начале.",
    transcript_excerpt: text.length > 220 ? `${text.slice(0, 220)}…` : text,
    title: extra.title ?? null,
    hook_text: extra.hook_text ?? tr.sentences[first.sid]?.text ?? null,
    content_flags: flags,
    forbidden_hits: [],
    unblocked_flags: unblocked,
    blocked: blockReasons.length > 0,
    block_reasons: blockReasons,
    needs_context: false,
    status: extra.status ?? "proposed",
    created_by_kind: extra.origin === "manual" ? "manual" : "auto",
    origin: extra.origin ?? "heuristic",
    origin_label: extra.origin === "manual" ? "Отмечено вручную" : "Эвристика без LLM",
    notes: extra.notes ?? null,
    job_id: extra.job_id ?? null,
    superseded: false,
    created_at: extra.created_at ?? at,
    updated_at: at,
  };
}

/** Эвристический «отбор»: окна по 5–8 предложений, лучшие по оценке. */
function selectCandidates(src: MockSource, max: number) {
  const tr = transcriptOf(src);
  const rand = rng(src.seed * 97 + (Date.now() % 1000));
  const out: S["ClipCandidate"][] = [];
  let sid = 1;
  while (sid < tr.sentences.length - 3 && out.length < max) {
    const span = 3 + Math.floor(rand() * 3);
    const a = tr.sentences[sid];
    const b = tr.sentences[Math.min(tr.sentences.length - 1, sid + span)];
    out.push(
      candidateFrom(src, a.w0, b.w1, {
        rand,
        title: a.text.replace(/[.!?…]+$/, "").slice(0, 80),
      }),
    );
    sid += span + 2 + Math.floor(rand() * 3);
  }
  out.sort((x, y) => (y.final_score ?? 0) - (x.final_score ?? 0));
  CANDIDATES.push(...out);
}

function ensureCandidates(src: MockSource) {
  if (CANDIDATES.some((c) => c.source_id === src.id)) return;
  selectCandidates(src, src.options.max_candidates);
}

// Демо: у источника 5 один момент с «гарантией» (блок) и один отброшенный.
(function seed() {
  const src5 = SOURCES[0];
  ensureCandidates(src5);
  ensureCandidates(SOURCES[1]);
  const tr = transcriptOf(src5);
  const guarantee = tr.sentences.find((s) => /Онлайн-расклад честен/.test(s.text));
  if (guarantee) {
    const blockedCand = candidateFrom(src5, guarantee.w0, Math.min(tr.words.length - 1, guarantee.w1 + 14), {
      title: "Гарантирую: онлайн-расклад точно сбудется",
      hook_text: "Гарантирую, что сбудется",
    });
    blockedCand.content_flags = ["guarantee_claim"];
    blockedCand.blocked = true;
    blockedCand.block_reasons = ["guarantee_claim"];
    blockedCand.final_score = 0.52;
    CANDIDATES.push(blockedCand);
  }
  const src5Cands = CANDIDATES.filter((c) => c.source_id === 5);
  const last = src5Cands[src5Cands.length - 2];
  if (last) last.status = "dismissed";
})();

function candidateList(src: MockSource, includeSuperseded: boolean, statuses: string[]): S["ClipCandidateList"] {
  const items = CANDIDATES.filter(
    (c) =>
      c.source_id === src.id &&
      (includeSuperseded || !c.superseded) &&
      (!statuses.length || statuses.includes(c.status)),
  ).sort((a, b) => (b.final_score ?? 0) - (a.final_score ?? 0));
  return { items, selection: selectionInfo(src) };
}

function wordIndexNear(tr: MockTranscript, t: number, edge: "start" | "end"): number {
  let best = 0;
  let bestDist = Infinity;
  tr.words.forEach((word, index) => {
    const dist = Math.abs((edge === "start" ? word.s : word.e) - t);
    if (dist < bestDist) {
      best = index;
      bestDist = dist;
    }
  });
  return best;
}

// ---------------------------------------------------------------- авторский слой и голос (по роликам)

type Segment = S["AuthorVoiceSegment"];
const SEGMENTS: Segment[] = ["cold_open", "insert_0", "insert_1", "outro"];

interface VoiceEntry {
  kind: S["AuthorVoiceKind"];
  readyAt: number;
  blob: Blob | null;
  mime: string;
  bytes: number;
  uploaded_at: string;
  task_id: number;
}

interface JobAuthor {
  candidateId: number | null;
  sourceId: number;
  start: number;
  end: number;
  script: S["AuthorScriptBody"] | null;
  mode: S["AuthorScriptMode"] | null;
  label: string | null;
  needsEdit: boolean;
  version: number;
  draftUntil: number | null;
  draftTaskId: number | null;
  updated_at: string | null;
  updated_by: number | null;
  voiceSource: S["AuthorVoiceSource"];
  voice: Partial<Record<Segment, VoiceEntry>>;
}

const JOB_AUTHOR = new Map<number, JobAuthor>();

function jobAuthor(jobId: number): JobAuthor {
  let state = JOB_AUTHOR.get(jobId);
  if (!state) {
    // Ролики из мока Конвейера: момент 1204–1226 с источника 5, текста ещё нет.
    state = {
      candidateId: null,
      sourceId: 5,
      start: 60,
      end: 82,
      script: null,
      mode: null,
      label: null,
      needsEdit: false,
      version: 0,
      draftUntil: null,
      draftTaskId: null,
      updated_at: null,
      updated_by: null,
      voiceSource: "tts",
      voice: {},
    };
    JOB_AUTHOR.set(jobId, state);
  }
  return state;
}

function templateScript(src: MockSource, start: number, end: number): S["AuthorScriptBody"] {
  const tr = transcriptOf(src);
  const inside = tr.sentences.filter((s) => s.s >= start - 0.5 && s.e <= end + 0.5);
  const mid = inside[Math.max(0, Math.floor(inside.length / 2) - 1)];
  return {
    cold_open: {
      vo_text: "[черновик без LLM] Вот что важно понять в этом фрагменте.",
      on_screen: "Главное за 30 секунд",
      duck_source: true,
    },
    inserts: mid
      ? [
          {
            after_sid: mid.sid,
            mode: "freeze_zoom",
            vo_text: "[черновик без LLM] Обратите внимание на эту мысль.",
            on_screen: "",
          },
        ]
      : [],
    outro: { vo_text: "[черновик без LLM] А вы как думаете? Напишите в комментариях.", cta_style: "question" },
    on_screen_title: inside[0]?.text.replace(/[.!?…]+$/, "").slice(0, 48) ?? "",
  };
}

function settleDraft(state: JobAuthor) {
  if (state.draftUntil !== null && Date.now() >= state.draftUntil) {
    const src = findSource(state.sourceId) ?? SOURCES[0];
    state.script = templateScript(src, state.start, state.end);
    state.mode = "heuristic";
    state.label = "[черновик без LLM — перепишите своими словами]";
    state.needsEdit = true;
    state.version += 1;
    state.draftUntil = null;
    state.draftTaskId = null;
    state.updated_at = nowIso();
  }
}

function lint(script: S["AuthorScriptBody"] | null): S["AuthorScriptIssue"][] {
  if (!script) return [];
  const issues: S["AuthorScriptIssue"][] = [];
  if (!script.cold_open?.vo_text?.trim())
    issues.push({
      code: "no_hook",
      severity: "warn",
      where: "cold_open",
      detail: "Нет хука голосом: первые секунды решают удержание",
    });
  const all = [script.cold_open?.vo_text, ...(script.inserts ?? []).map((i) => i.vo_text), script.outro?.vo_text].join(
    " ",
  );
  if (/черновик без LLM/.test(all))
    issues.push({
      code: "template_text",
      severity: "warn",
      where: "script",
      detail: "Шаблонный текст: перепишите своими словами",
    });
  if (/https?:\/\//.test(all))
    issues.push({ code: "link", severity: "error", where: "script", detail: "Ссылки в тексте голосом запрещены" });
  if (FLAG_TEXT.test(all))
    issues.push({
      code: "forbidden_phrase",
      severity: "error",
      where: "script",
      detail: "Гарантии результата запрещены правилами ниши",
    });
  return issues;
}

async function jobStatusOf(jobId: number): Promise<{ status: S["JobStatus"]; stage: S["JobStage"] | null } | null> {
  const response = await fetch(`${API}/jobs/${jobId}`, { headers: { Accept: "application/json" } });
  if (!response.ok) return null;
  const job = (await response.json()) as S["Job"];
  return { status: job.status, stage: job.stage };
}

function editableFor(job: { status: S["JobStatus"]; stage: S["JobStage"] | null } | null): boolean {
  if (!job) return false;
  if (["draft", "queued", "rework", "failed"].includes(job.status)) return true;
  return job.status === "processing" && ["clip_fetch", "clip_analyze", "author_script"].includes(job.stage ?? "");
}

function scriptView(jobId: number, state: JobAuthor, editable: boolean): S["JobAuthorScript"] {
  settleDraft(state);
  const issues = lint(state.script);
  return {
    job_id: jobId,
    script: state.script,
    mode: state.mode,
    label: state.label,
    llm_configured: false,
    needs_operator_edit: state.needsEdit,
    blocked: issues.some((i) => i.severity === "error"),
    issues,
    warnings: [],
    version: state.version,
    drafting_task_id: state.draftTaskId,
    editable,
    updated_at: state.updated_at,
    updated_by: state.updated_by,
  };
}

function segmentTextOf(script: S["AuthorScriptBody"] | null, segment: Segment): string | null {
  if (!script) return null;
  const text =
    segment === "cold_open"
      ? script.cold_open?.vo_text
      : segment === "outro"
        ? script.outro?.vo_text
        : script.inserts?.[segment === "insert_0" ? 0 : 1]?.vo_text;
  return text?.trim() ? text.trim() : null;
}

function voiceView(jobId: number, state: JobAuthor): S["JobAuthorVoice"] {
  settleDraft(state);
  let synthetic = false;
  const segments = SEGMENTS.map((segment): S["AuthorVoiceSegmentState"] => {
    const entry = state.voice[segment];
    const text = segmentTextOf(state.script, segment);
    const ready = !!entry && Date.now() >= entry.readyAt;
    if (text && !(entry && entry.kind === "operator" && ready)) synthetic = true;
    return {
      segment,
      text,
      kind: entry ? entry.kind : null,
      status: entry ? (ready ? "ready" : "processing") : "missing",
      duration_sec: entry && ready ? round3(Math.max(1, entry.bytes / 16000)) : null,
      timing: entry && ready ? "asr" : null,
      words_count: entry && ready && text ? text.split(/\s+/).length : null,
      audio_url: entry && ready ? `${API}/jobs/${jobId}/author-voice/${segment}/audio` : null,
      task_id: entry ? entry.task_id : null,
      error: null,
      uploaded_at: entry ? entry.uploaded_at : null,
    };
  });
  return { job_id: jobId, voice: state.voiceSource, segments, synthetic };
}

// ---------------------------------------------------------------- оценка оригинальности

const CHECK_TITLES: Record<S["OriginalityCheckCode"], string> = {
  author_share: "Доля авторского вклада (author_share)",
  voice_balance: "Голос против надписей",
  own_voice: "Голос автора",
  own_text: "Свой текст на экране",
  author_text: "Качество авторского текста",
  author_text_repetition: "Повтор своих реплик",
  repeat_moment: "Момент уже нарезан",
  library_similarity: "Похожесть на свою библиотеку",
  foreign_watermark: "Чужой водяной знак",
  rights: "Права на источник",
};
const VERDICT_LABELS: Record<S["OriginalityVerdict"], string> = {
  pass: "можно заливать",
  warn: "можно заливать после правок из списка",
  fail: "не заливать, пока не исправлено",
};

function speechSec(text: string | undefined | null): number {
  const chars = (text ?? "").trim().length;
  return chars ? Math.max(0.8, chars / 14) : 0;
}

function estimateReport(
  src: MockSource,
  script: S["AuthorScriptBody"] | null,
  clipSec: number,
  operatorSegments: Segment[],
  stage: S["OriginalityStage"],
  template: boolean,
): S["OriginalityReportOut"] {
  const own = src.rights.rights_type === "own";
  const cold = speechSec(script?.cold_open?.vo_text);
  const outro = speechSec(script?.outro?.vo_text);
  const inserts = (script?.inserts ?? []).map((ins) => ({
    sec: speechSec(ins.vo_text),
    freeze: ins.mode === "freeze_zoom",
    text: !!ins.on_screen?.trim(),
  }));
  const freezeSec = inserts.filter((i) => i.freeze).reduce((sum, i) => sum + i.sec, 0);
  const overSec = inserts.filter((i) => !i.freeze).reduce((sum, i) => sum + i.sec, 0);
  const duck = script?.cold_open?.duck_source ?? true;
  const authorOnly = (duck ? cold : 0) + freezeSec + outro;
  const voiceOver = (duck ? 0 : cold) + overSec;
  const textSec =
    (script?.on_screen_title?.trim() ? 3 : 0) +
    (script?.cold_open?.on_screen?.trim() ? 2 : 0) +
    inserts.filter((i) => i.text).length * 2;
  const duration = round3(clipSec + freezeSec + outro);
  const share = duration ? round3((authorOnly + 0.5 * voiceOver + 0.25 * textSec) / duration) : 0;
  const ownVoice = operatorSegments.reduce((sum, seg) => sum + speechSec(segmentTextOf(script, seg)), 0);
  const totalVoice = cold + outro + freezeSec + overSec;
  const min = 0.3;
  const gate = !own;
  const checks: S["OriginalityCheckOut"][] = [];
  const push = (
    code: S["OriginalityCheckCode"],
    status: S["OriginalityCheckStatus"],
    detail: string,
    value: unknown = null,
    threshold: unknown = null,
  ) => checks.push({ code, title: CHECK_TITLES[code], status, value, threshold, detail });
  push(
    "author_share",
    share >= min ? "pass" : gate ? "fail" : "warn",
    share >= min
      ? "Авторского вклада достаточно"
      : `Не хватает ≈ ${Math.max(1, Math.round((min - share) * duration))} с авторского вклада`,
    share,
    min,
  );
  push(
    "own_voice",
    ownVoice > 0 && ownVoice >= totalVoice - 0.1 ? "pass" : totalVoice ? "warn" : "info",
    ownVoice ? "Реплики своим голосом" : "Голос синтезом: в пакете будет AI-метка",
    round3(ownVoice),
  );
  push(
    "own_text",
    textSec ? "pass" : "warn",
    textSec ? "Есть свой текст на экране" : "Нет своего заголовка или тезисов на экране",
    round3(textSec),
  );
  push(
    "author_text",
    template ? (gate ? "fail" : "warn") : script ? "pass" : "warn",
    template ? "Шаблонный черновик без LLM: перепишите своими словами" : script ? "Текст оператора" : "Текста ещё нет",
  );
  push("repeat_moment", "pass", "Этот момент ещё не нарезан");
  push("library_similarity", "skip", stage === "estimate" ? "Считается после рендера" : "Похожих роликов нет");
  if (src.watermark?.suspect) push("foreign_watermark", "warn", src.watermark.detail ?? "Возможен чужой логотип");
  else push("foreign_watermark", "pass", "Чужих логотипов не найдено");
  push(
    "rights",
    own ? "pass" : "info",
    own ? "Своё видео" : `Чужие права: ${src.rights.attribution_text ?? src.rights.owner_name}`,
  );
  const fails = checks.filter((c) => c.status === "fail").map((c) => c.code);
  const warns = checks.filter((c) => c.status === "warn").map((c) => c.code);
  const verdict: S["OriginalityVerdict"] = fails.length ? "fail" : warns.length ? "warn" : "pass";
  const suggestions: string[] = [];
  if (share < min)
    suggestions.push("Добавьте вставку-комментарий со стоп-кадром или удлините хук и вывод своими словами.");
  if (template) suggestions.push("Перепишите шаблонный черновик своими словами: своя мысль — главное в нарезке.");
  if (!ownVoice && totalVoice) suggestions.push("Запишите хук своим голосом — живой голос сильнее синтеза.");
  if (!script?.on_screen_title?.trim()) suggestions.push("Добавьте свой заголовок на экране.");
  return {
    stage,
    verdict,
    verdict_label: VERDICT_LABELS[verdict],
    summary: {
      author_share: share,
      author_share_min: min,
      gate_applies: gate,
      gate_passed: !gate || share >= min,
      voice_share: totalVoice ? round3(ownVoice / totalVoice) : 0,
      own_voice_sec: round3(ownVoice),
      own_text_sec: round3(textSec),
      duration_sec: duration,
      rights_type: src.rights.rights_type,
      author_mode: template ? "heuristic" : script ? "operator" : null,
    },
    fails,
    warns,
    checks,
    suggestions,
    author_share: {
      value: share,
      duration_sec: duration,
      author_only_sec: round3(authorOnly),
      voice_over_sec: round3(voiceOver),
      text_sec: round3(textSec),
      own_voice_sec: round3(ownVoice),
      own_text_sec: round3(textSec),
      freeze_sec: round3(freezeSec),
      voice_share: totalVoice ? round3(ownVoice / totalVoice) : 0,
      text_part: round3(0.25 * textSec),
      text_capped: false,
      weights: { author_only: 1, voice_over: 0.5, text: 0.25 },
      intervals: {},
    },
    rights: { rights_type: src.rights.rights_type, owner_name: src.rights.owner_name, license: src.rights.license },
    notes:
      stage === "estimate" ? ["Оценка до рендера: длительности реплик — по длине текста или готовым записям."] : [],
    computed_at: nowIso(),
  };
}

function isTemplate(script: S["AuthorScriptBody"] | null): boolean {
  if (!script) return false;
  const all = [script.cold_open?.vo_text, ...(script.inserts ?? []).map((i) => i.vo_text), script.outro?.vo_text].join(
    " ",
  );
  return /черновик без LLM/.test(all);
}

// ---------------------------------------------------------------- tus

interface MockUpload {
  id: string;
  length: number;
  offset: number;
  metadata: Record<string, string>;
  status: S["UploadStatus"];
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

const UPLOADS = new Map<string, MockUpload>();

function hexId(): string {
  let out = "";
  for (let i = 0; i < 32; i += 1) out += "0123456789abcdef"[Math.floor(Math.random() * 16)];
  return out;
}

function decodeMetadata(header: string | null): Record<string, string> {
  const out: Record<string, string> = {};
  if (!header) return out;
  for (const pair of header.split(",")) {
    const [key, value] = pair.trim().split(" ");
    if (!key) continue;
    try {
      const bin = value ? atob(value) : "";
      out[key] = new TextDecoder().decode(Uint8Array.from(bin, (ch) => ch.charCodeAt(0)));
    } catch {
      out[key] = "";
    }
  }
  return out;
}

function uploadInfo(upload: MockUpload): S["UploadInfo"] {
  return {
    id: upload.id,
    purpose: (upload.metadata.purpose as S["UploadPurpose"]) || "source",
    filename: upload.metadata.filename ?? "file",
    filetype: upload.metadata.filetype ?? null,
    length_bytes: upload.length,
    received_bytes: upload.offset,
    status: upload.status,
    created_at: upload.created_at,
    updated_at: upload.updated_at,
    expires_at: new Date(Date.parse(upload.updated_at) + 24 * HOUR).toISOString(),
    completed_at: upload.completed_at,
  };
}

// ---------------------------------------------------------------- приём источника

function rightsProblems(input: S["SourceRightsIn"] | undefined): Response | null {
  if (!input) return invalid(["rights"], "Права обязательны: без прав источник не создаётся", "missing");
  const values: RightsFormValues = {
    ...EMPTY_RIGHTS,
    rights_type: input.rights_type,
    owner_name: input.owner_name ?? "",
    proof_note: input.proof_note ?? "",
    owner_channel_url: input.owner_channel_url ?? "",
    attribution_text: input.attribution_text ?? "",
    license: input.license ?? "",
    license_url: input.license_url ?? "",
    valid_until: input.valid_until ?? "",
    allowlist_status: input.allowlist_status ?? "",
  };
  const errs = validateRights(values);
  const keys = Object.keys(errs);
  if (!keys.length) return null;
  const codes = [
    errs.attribution_text ? "attribution_required" : null,
    errs.license ? "license_incompatible" : null,
    errs.valid_until ? "rights_expired" : null,
  ].filter(Boolean);
  if (codes.length) {
    return error(422, "validation_error", "Проверьте поля формы", {
      errors: [
        {
          loc: ["body", "rights"],
          msg: `Права не приняты (${codes.join(",")}): ${keys.map((k) => errs[k as keyof typeof errs]).join("; ")}`,
          type: "rights_rejected",
        },
      ],
    });
  }
  return error(422, "validation_error", "Проверьте поля формы", {
    errors: keys.map((key) => ({
      loc: ["body", "rights", key],
      msg: errs[key as keyof typeof errs],
      type: "value_error",
    })),
  });
}

function newSource(
  caller: Caller,
  body: { title: string; rights: S["SourceRightsIn"]; niche_id?: string | null; options?: S["SourceOptions"] | null },
  origin: S["SourceOrigin"],
  url: string | null,
  bytes: number | null,
): MockSource {
  const at = nowIso();
  const src: MockSource = {
    id: nextSourceId++,
    title: body.title,
    origin,
    origin_url: url,
    status: "preparing",
    processingFrom: Date.now(),
    error: null,
    niche_id: body.niche_id ?? null,
    rights: rightsRecord(body.rights, at, caller.userId),
    watermark: { suspect: false, severity: "ok", detail: null, frames_checked: 12, logos_checked: 6 },
    platform: null,
    options: body.options ?? DEFAULT_OPTIONS,
    lines: TALK_TARO,
    targetSec: 360,
    seed: 100 + nextSourceId,
    transcript: null,
    selectionMode: "heuristic",
    selectedAt: 0,
    selectTaskId: nextTaskId++,
    selectingUntil: null,
    file_bytes: bytes,
    created_at: at,
    updated_at: at,
  };
  SOURCES.unshift(src);
  return src;
}

// ---------------------------------------------------------------- картинки

function storyboardSheetSvg(src: MockSource, sheet: number): string {
  const { tile_w: w, tile_h: h, cols, rows, interval_sec: step } = STORYBOARD;
  const parts: string[] = [];
  for (let r = 0; r < rows; r += 1) {
    for (let c = 0; c < cols; c += 1) {
      const idx = sheet * cols * rows + r * cols + c;
      const t = idx * step;
      const hue = (src.seed * 47 + Math.floor(t / 20) * 23) % 360;
      const mm = String(Math.floor(t / 60)).padStart(2, "0");
      const ss = String(Math.floor(t % 60)).padStart(2, "0");
      parts.push(
        `<rect x="${c * w}" y="${r * h}" width="${w}" height="${h}" fill="hsl(${hue} 35% 22%)"/>` +
          `<circle cx="${c * w + w / 2}" cy="${r * h + h / 2 - 6}" r="16" fill="hsl(${hue} 45% 55%)"/>` +
          `<text x="${c * w + 6}" y="${r * h + h - 8}" font-family="monospace" font-size="14" fill="#fff">${mm}:${ss}</text>`,
      );
    }
  }
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${w * cols}" height="${h * rows}">${parts.join("")}</svg>`;
}

// ---------------------------------------------------------------- побочные эффекты один раз

/**
 * В `next dev` (StrictMode) MockGate может поднять MSW дважды: тогда один и тот же запрос доходит до
 * обработчиков два раза (с разными requestId). Изменяющие обработчики выполняются один раз на
 * одинаковый запрос (метод + адрес + тело) в пределах 2 с, повтор получает тот же ответ — иначе,
 * например, пересылка на `POST /jobs` создала бы два ролика.
 */
const INFLIGHT = new Map<string, { at: number; promise: Promise<Response> }>();
const DEDUPE_MS = 2_000;

async function once(request: Request, run: () => Promise<Response>): Promise<Response> {
  const body = await request
    .clone()
    .text()
    .catch(() => "");
  const key = `${request.method} ${request.url} ${body}`;
  const now = Date.now();
  const hit = INFLIGHT.get(key);
  if (hit && now - hit.at < DEDUPE_MS) return (await hit.promise).clone();
  const promise = run();
  INFLIGHT.set(key, { at: now, promise });
  setTimeout(() => {
    if (INFLIGHT.get(key)?.promise === promise) INFLIGHT.delete(key);
  }, DEDUPE_MS);
  return (await promise).clone();
}

interface ResolverInfo {
  request: Request;
  params: PathParams;
}
type Resolver = (info: ResolverInfo) => Response | Promise<Response>;

const post = (path: string, resolver: Resolver) =>
  http.post(path, (info) => once(info.request, async () => resolver(info)));
const patch = (path: string, resolver: Resolver) =>
  http.patch(path, (info) => once(info.request, async () => resolver(info)));
const put = (path: string, resolver: Resolver) =>
  http.put(path, (info) => once(info.request, async () => resolver(info)));
const del = (path: string, resolver: Resolver) =>
  http.delete(path, (info) => once(info.request, async () => resolver(info)));

// ---------------------------------------------------------------- обработчики

async function forwardCreateJob(
  request: Request,
  body: Record<string, unknown>,
): Promise<{ response: Response; job: S["Job"] | null }> {
  const response = await fetch(`${API}/jobs`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      "X-CSRF-Token": request.headers.get("X-CSRF-Token") ?? "",
      "Idempotency-Key": crypto.randomUUID ? crypto.randomUUID() : String(Math.random()),
    },
    body: JSON.stringify(body),
  });
  const payload = (await response.json().catch(() => null)) as S["Job"] | null;
  return { response, job: response.ok ? payload : null };
}

function rememberJob(
  job: S["Job"],
  candidate: S["ClipCandidate"] | null,
  sourceId: number,
  options: S["ClipJobOptions"],
  start: number,
  end: number,
) {
  const layer = options.author_layer;
  const state: JobAuthor = {
    candidateId: candidate?.id ?? null,
    sourceId,
    start,
    end,
    script: layer?.mode === "operator_text" && layer.script ? layer.script : null,
    mode: layer?.mode === "operator_text" && layer.script ? "operator" : null,
    label: layer?.mode === "operator_text" && layer.script ? "Текст оператора" : null,
    needsEdit: false,
    version: layer?.mode === "operator_text" && layer.script ? 1 : 0,
    draftUntil: null,
    draftTaskId: null,
    updated_at: nowIso(),
    updated_by: null,
    voiceSource: layer?.voice ?? "tts",
    voice: {},
  };
  JOB_AUTHOR.set(job.id, state);
  if (candidate) {
    candidate.job_id = job.id;
    candidate.status = "used";
    candidate.updated_at = nowIso();
  }
}

export const clippingHandlers = [
  // ---------- tus
  http.options(
    `${API}/uploads/tus`,
    () =>
      new HttpResponse(null, {
        status: 204,
        headers: {
          ...TUS_HEADERS,
          "Tus-Version": "1.0.0",
          "Tus-Extension": "creation,termination,expiration",
          "Tus-Max-Size": String(6 * 1024 ** 3),
        },
      }),
  ),
  post(`${API}/uploads/tus`, ({ request }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    if (request.headers.get("Tus-Resumable") !== "1.0.0")
      return error(412, "tus_version_unsupported", "Нужен Tus-Resumable: 1.0.0");
    const length = Number(request.headers.get("Upload-Length"));
    if (!Number.isFinite(length) || length <= 0) return error(400, "bad_request", "Нужен Upload-Length");
    if (length > 6 * 1024 ** 3) return error(413, "payload_too_large", "Файл больше 6 ГБ");
    const metadata = decodeMetadata(request.headers.get("Upload-Metadata"));
    const id = hexId();
    const at = nowIso();
    UPLOADS.set(id, {
      id,
      length,
      offset: 0,
      metadata,
      status: "uploading",
      created_at: at,
      updated_at: at,
      completed_at: null,
    });
    return new HttpResponse(null, {
      status: 201,
      headers: {
        ...TUS_HEADERS,
        Location: `${API}/uploads/tus/${id}`,
        "Upload-Expires": new Date(Date.now() + 24 * HOUR).toUTCString(),
      },
    });
  }),
  http.head(`${API}/uploads/tus/:uploadId`, ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const upload = UPLOADS.get(String(params.uploadId));
    if (!upload) return new HttpResponse(null, { status: 404, headers: TUS_HEADERS });
    return new HttpResponse(null, {
      status: 200,
      headers: {
        ...TUS_HEADERS,
        "Upload-Offset": String(upload.offset),
        "Upload-Length": String(upload.length),
        "Cache-Control": "no-store",
      },
    });
  }),
  patch(`${API}/uploads/tus/:uploadId`, async ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const upload = UPLOADS.get(String(params.uploadId));
    if (!upload) return error(404, "upload_not_found", "Загрузка не найдена");
    const offset = Number(request.headers.get("Upload-Offset"));
    if (offset !== upload.offset)
      return error(409, "upload_offset_mismatch", "Смещение не совпадает", { offset: upload.offset });
    const chunk = await request.arrayBuffer();
    if (chunk.byteLength > 16 * 1024 * 1024) return error(413, "payload_too_large", "Чанк больше 16 МБ");
    upload.offset = Math.min(upload.length, upload.offset + chunk.byteLength);
    upload.updated_at = nowIso();
    if (upload.offset >= upload.length) {
      upload.status = "complete";
      upload.completed_at = upload.updated_at;
    }
    return new HttpResponse(null, { status: 204, headers: { ...TUS_HEADERS, "Upload-Offset": String(upload.offset) } });
  }),
  del(`${API}/uploads/tus/:uploadId`, ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const upload = UPLOADS.get(String(params.uploadId));
    if (upload) upload.status = "aborted";
    return new HttpResponse(null, { status: 204, headers: TUS_HEADERS });
  }),
  http.get(`${API}/uploads/:uploadId`, ({ request, params }) => {
    const g = guard(request);
    if (g instanceof Response) return g;
    const upload = UPLOADS.get(String(params.uploadId));
    if (!upload) return error(404, "upload_not_found", "Загрузка не найдена");
    return HttpResponse.json(uploadInfo(upload));
  }),

  // ---------- приём источника
  post(`${API}/sources/from-upload`, async ({ request }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const body = (await request.json()) as S["SourceFromUpload"];
    const rights = rightsProblems(body.rights);
    if (rights) return rights;
    const upload = UPLOADS.get(body.upload_id);
    if (!upload) return error(404, "upload_not_found", "Загрузка не найдена");
    if (upload.status === "consumed") return error(409, "upload_consumed", "Загрузка уже стала источником");
    if (upload.status !== "complete") return error(409, "upload_not_complete", "Загрузка не закончена");
    if (!body.title?.trim()) return invalid(["title"], "Название обязательно", "missing");
    upload.status = "consumed";
    const src = newSource(g, { ...body, title: body.title.trim() }, "upload", null, upload.length);
    return HttpResponse.json(sourceView(src), { status: 202 });
  }),
  post(`${API}/sources/from-url`, async ({ request }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const body = (await request.json()) as S["SourceFromUrl"];
    const rights = rightsProblems(body.rights);
    if (rights) return rights;
    let host = "";
    try {
      host = new URL(body.url).hostname;
    } catch {
      return error(422, "unsupported_url", "Ссылка не распознана");
    }
    if (/^(localhost|127\.|10\.|192\.168\.)/.test(host))
      return error(422, "unsupported_url", "Локальные адреса не принимаются");
    const src = newSource(g, { ...body, title: body.title?.trim() || `Видео с ${host}` }, "url", body.url, null);
    return HttpResponse.json(sourceView(src), { status: 202 });
  }),

  // ---------- источники
  http.get(`${API}/sources`, ({ request }) => {
    const g = guard(request);
    if (g instanceof Response) return g;
    const url = new URL(request.url);
    const statuses = url.searchParams.getAll("status");
    const q = (url.searchParams.get("q") ?? "").trim().toLowerCase();
    const limit = Math.min(200, Number(url.searchParams.get("limit") ?? 50));
    const offset = Number(url.searchParams.get("offset") ?? 0);
    const rows = SOURCES.map(rowView)
      .filter(
        (row) => (!statuses.length || statuses.includes(row.status)) && (!q || row.title.toLowerCase().includes(q)),
      )
      .sort((a, b) => b.updated_at.localeCompare(a.updated_at));
    return HttpResponse.json({
      items: rows.slice(offset, offset + limit),
      total: rows.length,
      limit,
      offset,
    } satisfies S["SourcePage"]);
  }),
  http.get(`${API}/sources/:sourceId`, ({ request, params }) => {
    const g = guard(request);
    if (g instanceof Response) return g;
    const src = findSource(params.sourceId);
    if (!src) return error(404, "source_not_found", "Источник не найден");
    return HttpResponse.json(sourceView(src));
  }),
  patch(`${API}/sources/:sourceId`, async ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const src = findSource(params.sourceId);
    if (!src) return error(404, "source_not_found", "Источник не найден");
    const body = (await request.json()) as S["SourcePatch"];
    if (body.title) src.title = body.title;
    if (body.niche_id !== undefined) src.niche_id = body.niche_id;
    src.updated_at = nowIso();
    return HttpResponse.json(sourceView(src));
  }),
  del(`${API}/sources/:sourceId`, ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const index = SOURCES.findIndex((item) => item.id === Number(params.sourceId));
    if (index < 0) return error(404, "source_not_found", "Источник не найден");
    SOURCES.splice(index, 1);
    return new HttpResponse(null, { status: 204 });
  }),
  http.get(`${API}/sources/:sourceId/rights`, ({ request, params }) => {
    const g = guard(request);
    if (g instanceof Response) return g;
    const src = findSource(params.sourceId);
    if (!src) return error(404, "source_not_found", "Источник не найден");
    return HttpResponse.json(src.rights);
  }),
  put(`${API}/sources/:sourceId/rights`, async ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const src = findSource(params.sourceId);
    if (!src) return error(404, "source_not_found", "Источник не найден");
    const body = (await request.json()) as S["SourceRightsIn"];
    const problems = rightsProblems(body);
    if (problems) {
      const payload = (await problems.clone().json()) as { error: { details: { errors: Array<{ loc: string[] }> } } };
      for (const item of payload.error.details.errors) item.loc = item.loc.filter((part) => part !== "rights");
      return HttpResponse.json(payload, { status: 422 });
    }
    src.rights = { ...rightsRecord(body, src.rights.recorded_at, src.rights.recorded_by), updated_at: nowIso() };
    return HttpResponse.json(src.rights);
  }),
  post(`${API}/sources/:sourceId/reprocess`, async ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const src = findSource(params.sourceId);
    if (!src) return error(404, "source_not_found", "Источник не найден");
    const body = (await request.json()) as S["SourceReprocess"];
    if (!body.steps?.length) return invalid(["steps"], "Нужен хотя бы один шаг", "too_short");
    if (src.status === "failed") {
      src.status = "preparing";
      src.error = null;
      src.frozenPipeline = undefined;
      src.processingFrom = Date.now();
    } else if (body.steps.includes("select")) {
      src.selectingUntil = Date.now() + 3_000;
    }
    return HttpResponse.json({ task_id: nextTaskId++ } satisfies S["TaskAccepted"], { status: 202 });
  }),
  http.get(`${API}/sources/:sourceId/transcript`, ({ request, params }) => {
    const g = guard(request);
    if (g instanceof Response) return g;
    const src = findSource(params.sourceId);
    if (!src) return error(404, "source_not_found", "Источник не найден");
    const view = sourceView(src);
    if (!view.media.transcript_url) return error(404, "transcript_not_found", "Транскрипта ещё нет");
    const tr = transcriptOf(src);
    return HttpResponse.json({
      source_id: src.id,
      language: "ru",
      duration_sec: tr.duration,
      provider_key: "gigaam_v3_onnx",
      model: "gigaam-v3-e2e-ctc",
      words: tr.words,
      sentences: tr.sentences,
    } satisfies S["SourceTranscript"]);
  }),
  http.get(`${API}/sources/:sourceId/peaks`, ({ request, params }) => {
    const g = guard(request);
    if (g instanceof Response) return g;
    const src = findSource(params.sourceId);
    if (!src || !sourceView(src).media.peaks_url) return error(404, "media_not_found", "Файл ещё не готов");
    return HttpResponse.json(buildPeaks(transcriptOf(src), src.seed));
  }),
  http.get(`${API}/sources/:sourceId/storyboard`, ({ request, params }) => {
    const g = guard(request);
    if (g instanceof Response) return g;
    const src = findSource(params.sourceId);
    if (!src || !sourceView(src).media.storyboard_url) return error(404, "media_not_found", "Файл ещё не готов");
    const count = Math.ceil(transcriptOf(src).duration / STORYBOARD.interval_sec);
    const perSheet = STORYBOARD.cols * STORYBOARD.rows;
    const sheets = Array.from(
      { length: Math.ceil(count / perSheet) },
      (_, i) => `${API}/sources/${src.id}/storyboard/${i}`,
    );
    return HttpResponse.json({ ...STORYBOARD, count, sheets } satisfies S["SourceStoryboard"]);
  }),
  http.get(`${API}/sources/:sourceId/storyboard/:sheetIdx`, ({ params }) => {
    const src = findSource(params.sourceId);
    if (!src) return new HttpResponse(null, { status: 404 });
    return new HttpResponse(storyboardSheetSvg(src, Number(params.sheetIdx)), {
      headers: { "Content-Type": "image/svg+xml", "Cache-Control": "max-age=3600" },
    });
  }),
  http.get(`${API}/sources/:sourceId/scenes`, ({ request, params }) => {
    const g = guard(request);
    if (g instanceof Response) return g;
    const src = findSource(params.sourceId);
    if (!src) return error(404, "source_not_found", "Источник не найден");
    const duration = transcriptOf(src).duration;
    return HttpResponse.json({
      items: buildScenes(duration, src.seed),
      coverage: [[0, duration]],
    } satisfies S["SourceScenes"]);
  }),
  // Видео в моках нет: плеер студии работает «вхолостую».
  http.get(`${API}/sources/:sourceId/media/:kind`, () => error(404, "media_not_found", "В моках нет видео")),

  // ---------- кандидаты
  http.get(`${API}/sources/:sourceId/candidates`, ({ request, params }) => {
    const g = guard(request);
    if (g instanceof Response) return g;
    const src = findSource(params.sourceId);
    if (!src) return error(404, "source_not_found", "Источник не найден");
    const url = new URL(request.url);
    liveState(src);
    return HttpResponse.json(
      candidateList(src, url.searchParams.get("include_superseded") === "true", url.searchParams.getAll("status")),
    );
  }),
  post(`${API}/sources/:sourceId/candidates`, async ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const src = findSource(params.sourceId);
    if (!src) return error(404, "source_not_found", "Источник не найден");
    const body = (await request.json()) as S["ClipCandidateCreate"];
    const tr = transcriptOf(src);
    const w0 =
      body.start_word_idx ??
      (body.start_sec !== null && body.start_sec !== undefined ? wordIndexNear(tr, body.start_sec, "start") : null);
    const w1 =
      body.end_word_idx ??
      (body.end_sec !== null && body.end_sec !== undefined ? wordIndexNear(tr, body.end_sec, "end") : null);
    if (w0 === null || w1 === null) return invalid(["start_word_idx"], "Нужны обе границы", "edge_missing");
    if (w1 < w0) return invalid(["end_word_idx"], "end_word_idx раньше start_word_idx", "edge_order");
    const cand = candidateFrom(src, w0, Math.min(w1, tr.words.length - 1), {
      origin: "manual",
      title: body.title ?? null,
      hook_text: body.hook_text ?? null,
      notes: body.notes ?? null,
    });
    CANDIDATES.push(cand);
    return HttpResponse.json(cand, { status: 201 });
  }),
  post(`${API}/sources/:sourceId/select`, async ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const src = findSource(params.sourceId);
    if (!src) return error(404, "source_not_found", "Источник не найден");
    if (sourceView(src).status !== "ready") return error(409, "source_not_ready", "Источник ещё не готов");
    const body = (await request.json()) as S["SourceSelectRequest"];
    for (const cand of CANDIDATES) {
      if (cand.source_id === src.id && cand.created_by_kind === "auto" && cand.job_id === null) cand.superseded = true;
    }
    if (body.save_as_default) {
      src.options = {
        ...src.options,
        selector: body.selector,
        target_clip_sec: body.target_clip_sec ?? src.options.target_clip_sec,
        max_candidates: body.max_candidates ?? src.options.max_candidates,
        tighten_pauses: body.tighten_pauses ?? src.options.tighten_pauses,
      };
    }
    selectCandidates(src, body.max_candidates ?? src.options.max_candidates);
    src.selectTaskId = nextTaskId++;
    src.selectedAt = Date.now();
    src.selectingUntil = Date.now() + 2_000;
    return HttpResponse.json({ task_id: src.selectTaskId } satisfies S["TaskAccepted"], { status: 202 });
  }),
  post(`${API}/sources/:sourceId/clips`, async ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const src = findSource(params.sourceId);
    if (!src) return error(404, "source_not_found", "Источник не найден");
    const body = (await request.json()) as S["ClipBatchCreate"];
    const items: S["Job"][] = [];
    for (const item of body.items) {
      const cand = item.candidate_id ? (CANDIDATES.find((c) => c.id === item.candidate_id) ?? null) : null;
      const { response, job } = await forwardCreateJob(request, {
        kind: "clip",
        ...(cand
          ? { clip_candidate_id: cand.id }
          : { source: { source_id: 5, start_sec: item.start_sec, end_sec: item.end_sec } }),
        channel_id: item.channel_id,
        campaign_id: item.campaign_id ?? null,
        banner_variant_id: item.banner_variant_id ?? null,
        author_layer: item.author_layer ?? null,
        reframe: item.reframe ?? null,
        priority: item.priority,
        start: item.start,
      });
      if (!job) return response;
      rememberJob(job, cand, src.id, item, item.start_sec, item.end_sec);
      items.push(job);
    }
    return HttpResponse.json({ items } satisfies S["JobList"]);
  }),
  http.get(`${API}/clip-candidates/:candidateId`, ({ request, params }) => {
    const g = guard(request);
    if (g instanceof Response) return g;
    const cand = CANDIDATES.find((c) => c.id === Number(params.candidateId));
    if (!cand) return error(404, "clip_candidate_not_found", "Кандидат не найден");
    return HttpResponse.json(cand);
  }),
  patch(`${API}/clip-candidates/:candidateId`, async ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const index = CANDIDATES.findIndex((c) => c.id === Number(params.candidateId));
    if (index < 0) return error(404, "clip_candidate_not_found", "Кандидат не найден");
    let cand = CANDIDATES[index];
    const src = findSource(cand.source_id);
    if (!src) return error(404, "source_not_found", "Источник не найден");
    const body = (await request.json()) as S["ClipCandidatePatch"];
    if (body.status === "used") return invalid(["status"], "Статус used ставится созданием clip-job", "status_used");
    const tr = transcriptOf(src);
    const edges = [body.start_word_idx, body.end_word_idx, body.start_sec, body.end_sec].some(
      (v) => v !== null && v !== undefined,
    );
    if (edges) {
      const w0 =
        body.start_word_idx ??
        (body.start_sec != null ? wordIndexNear(tr, body.start_sec, "start") : (cand.start_word_idx ?? 0));
      const w1 =
        body.end_word_idx ?? (body.end_sec != null ? wordIndexNear(tr, body.end_sec, "end") : (cand.end_word_idx ?? 0));
      if (w1 < w0) return invalid(["end_word_idx"], "end_word_idx раньше start_word_idx", "edge_order");
      const next = candidateFrom(src, w0, Math.min(w1, tr.words.length - 1), {
        id: cand.id,
        origin: cand.origin,
        title: cand.title,
        hook_text: cand.hook_text,
        notes: cand.notes,
        status: cand.status,
        job_id: cand.job_id,
        unblocked_flags: cand.unblocked_flags,
        created_at: cand.created_at,
      });
      next.origin_label = cand.origin_label;
      next.llm_scores = cand.llm_scores;
      next.hook_type = cand.hook_type;
      next.reason = cand.reason;
      cand = next;
    }
    if (body.title !== undefined) cand.title = body.title;
    if (body.hook_text !== undefined) cand.hook_text = body.hook_text;
    if (body.notes !== undefined) cand.notes = body.notes;
    if (body.unblock_flags?.length) {
      cand.unblocked_flags = [...new Set([...cand.unblocked_flags, ...body.unblock_flags])];
    }
    if (body.block_flags?.length)
      cand.unblocked_flags = cand.unblocked_flags.filter((f) => !body.block_flags?.includes(f));
    cand.block_reasons = cand.content_flags.filter(
      (f) => ["medical_claim", "guarantee_claim", "forbidden_phrase"].includes(f) && !cand.unblocked_flags.includes(f),
    );
    cand.blocked = cand.block_reasons.length > 0;
    if (body.status) cand.status = body.status;
    cand.updated_at = nowIso();
    CANDIDATES[index] = cand;
    return HttpResponse.json(cand);
  }),
  post(`${API}/clip-candidates/:candidateId/jobs`, async ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const cand = CANDIDATES.find((c) => c.id === Number(params.candidateId));
    if (!cand) return error(404, "clip_candidate_not_found", "Кандидат не найден");
    if (cand.blocked) {
      return error(409, "clip_candidate_blocked", "Кандидат заблокирован: снимите флаги или отредактируйте момент", {
        clip_candidate_id: cand.id,
        block_reasons: cand.block_reasons,
      });
    }
    if (cand.job_id !== null) {
      const current = await jobStatusOf(cand.job_id);
      if (current && !["cancelled", "rejected", "failed"].includes(current.status)) {
        return error(409, "clip_candidate_in_use", "Кандидат уже в работе", {
          clip_candidate_id: cand.id,
          job_id: cand.job_id,
        });
      }
    }
    const body = (await request.json()) as S["ClipJobOptions"];
    const { response, job } = await forwardCreateJob(request, { kind: "clip", clip_candidate_id: cand.id, ...body });
    if (!job) return response;
    rememberJob(job, cand, cand.source_id, body, cand.start_sec, cand.end_sec);
    return HttpResponse.json(job, { status: 201 });
  }),
  post(`${API}/clip-candidates/:candidateId/originality-estimate`, async ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const cand = CANDIDATES.find((c) => c.id === Number(params.candidateId));
    if (!cand) return error(404, "clip_candidate_not_found", "Кандидат не найден");
    const src = findSource(cand.source_id);
    if (!src) return error(404, "source_not_found", "Источник не найден");
    const body = (await request.json()) as S["OriginalityEstimateRequest"];
    const start = body.start_sec ?? cand.start_sec;
    const end = body.end_sec ?? cand.end_sec;
    if (end <= start) return invalid(["end_sec"], "Конец раньше начала", "edge_order");
    const script = body.script ?? templateScript(src, start, end);
    return HttpResponse.json(estimateReport(src, script, end - start, [], "estimate", !body.script));
  }),

  // ---------- авторский текст
  http.get(`${API}/jobs/:jobId/author-script`, async ({ request, params }) => {
    const g = guard(request);
    if (g instanceof Response) return g;
    const jobId = Number(params.jobId);
    const job = await jobStatusOf(jobId);
    if (!job) return error(404, "job_not_found", "Ролик не найден");
    return HttpResponse.json(scriptView(jobId, jobAuthor(jobId), editableFor(job)));
  }),
  put(`${API}/jobs/:jobId/author-script`, async ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const jobId = Number(params.jobId);
    const job = await jobStatusOf(jobId);
    if (!job) return error(404, "job_not_found", "Ролик не найден");
    if (!editableFor(job)) {
      return error(409, "job_state_conflict", "Авторский текст правится до озвучки: отправьте ролик на доработку", {
        job_id: jobId,
        status: job.status,
        stage: job.stage,
        hint: "rework",
      });
    }
    const state = jobAuthor(jobId);
    settleDraft(state);
    const body = (await request.json()) as S["AuthorScriptPut"];
    if (
      body.expected_version !== null &&
      body.expected_version !== undefined &&
      body.expected_version !== state.version
    ) {
      return error(409, "version_conflict", "Данные уже изменились", { job_id: jobId, version: state.version });
    }
    state.script = body.script;
    state.mode = "operator";
    state.label = "Текст оператора";
    state.needsEdit = false;
    state.version += 1;
    state.updated_at = nowIso();
    state.updated_by = g.userId;
    return HttpResponse.json(scriptView(jobId, state, true));
  }),
  post(`${API}/jobs/:jobId/author-script/draft`, async ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const jobId = Number(params.jobId);
    const job = await jobStatusOf(jobId);
    if (!job) return error(404, "job_not_found", "Ролик не найден");
    if (!editableFor(job))
      return error(409, "job_state_conflict", "Черновик пишется до стадии авторского текста или на доработке");
    const state = jobAuthor(jobId);
    state.draftTaskId = nextTaskId++;
    state.draftUntil = Date.now() + 1_500;
    return HttpResponse.json({ task_id: state.draftTaskId } satisfies S["TaskAccepted"], { status: 202 });
  }),

  // ---------- голос автора
  http.get(`${API}/jobs/:jobId/author-voice`, ({ request, params }) => {
    const g = guard(request);
    if (g instanceof Response) return g;
    const jobId = Number(params.jobId);
    return HttpResponse.json(voiceView(jobId, jobAuthor(jobId)));
  }),
  post(`${API}/jobs/:jobId/author-voice`, async ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const jobId = Number(params.jobId);
    const form = await request.formData();
    const segment = String(form.get("segment") ?? "") as Segment;
    const audio = form.get("audio");
    if (!SEGMENTS.includes(segment)) return invalid(["segment"], `Неизвестная реплика ${segment}`, "segment");
    if (!(audio instanceof Blob)) return invalid(["audio"], "Нет файла записи", "missing");
    if (audio.size > 20 * 1024 * 1024)
      return error(413, "payload_too_large", "Запись больше 20 МБ", { asset_error: "file_too_large" });
    const mime = audio.type.split(";")[0] || "audio/webm";
    if (!["audio/webm", "audio/ogg", "audio/wav", "audio/x-wav", "audio/wave"].includes(mime)) {
      return error(415, "unsupported_media_type", "Нужен webm, ogg или wav");
    }
    const state = jobAuthor(jobId);
    const taskId = nextTaskId++;
    state.voice[segment] = {
      kind: "operator",
      readyAt: Date.now() + 1_500,
      blob: audio,
      mime,
      bytes: audio.size,
      uploaded_at: nowIso(),
      task_id: taskId,
    };
    return HttpResponse.json({ task_id: taskId, segment, bytes: audio.size, mime } satisfies S["AuthorVoiceAccepted"], {
      status: 202,
    });
  }),
  http.get(`${API}/jobs/:jobId/author-voice/:segment/audio`, ({ params }) => {
    const entry = jobAuthor(Number(params.jobId)).voice[params.segment as Segment];
    if (!entry?.blob) return new HttpResponse(null, { status: 404 });
    return new HttpResponse(entry.blob, { headers: { "Content-Type": entry.mime } });
  }),
  del(`${API}/jobs/:jobId/author-voice/:segment`, ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const state = jobAuthor(Number(params.jobId));
    const segment = params.segment as Segment;
    if (!state.voice[segment]) return error(404, "author_voice_not_found", "Записи нет");
    delete state.voice[segment];
    return new HttpResponse(null, { status: 204 });
  }),

  // ---------- оригинальность ролика
  post(`${API}/jobs/:jobId/originality/estimate`, async ({ request, params }) => {
    const g = guard(request, OPERATORS);
    if (g instanceof Response) return g;
    const jobId = Number(params.jobId);
    const state = jobAuthor(jobId);
    settleDraft(state);
    const src = findSource(state.sourceId) ?? SOURCES[0];
    const body = (await request.json()) as S["OriginalityEstimateRequest"];
    const script = body.script ?? state.script;
    const operatorSegments = SEGMENTS.filter(
      (seg) => state.voice[seg]?.kind === "operator" && Date.now() >= (state.voice[seg]?.readyAt ?? 0),
    );
    const start = body.start_sec ?? state.start;
    const end = body.end_sec ?? state.end;
    return HttpResponse.json(
      estimateReport(src, script, end - start, operatorSegments, "estimate", isTemplate(script)),
    );
  }),
  http.get(`${API}/jobs/:jobId/originality`, () =>
    error(404, "originality_not_found", "Отчёт появится после стадии QC"),
  ),
];
