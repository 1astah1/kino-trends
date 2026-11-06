/**
 * Чистые функции студии нарезки (docs/ARCHITECTURE.md §6.3.5, §7.3 «Студия нарезки»): поиск слов
 * по времени, границы клипа по словам, зоны длительности, плитки раскадровки, сценарий авторского
 * слоя. Без React и без DOM — их удобно проверять отдельно.
 *
 * Транскрипт — компактный контракт движка (`api/schemas/transcripts.py`): слово `{w, s, e, sid}`,
 * предложение `{sid, s, e, text, w0, w1}` (w0..w1 включительно). Границы клипа в студии хранятся
 * индексами слов (`start_word_idx`/`end_word_idx` в PATCH), снаппинг в тишину делает сервер.
 */
import type { Schemas } from "@/lib/api/client";

export type TranscriptWord = Schemas["TranscriptWord"];
export type TranscriptSentence = Schemas["TranscriptSentence"];
export type AuthorScriptBody = Schemas["AuthorScriptBody"];
export type AuthorVoiceSegment = Schemas["AuthorVoiceSegment"];
export type Platform = Schemas["Platform"];

/** Границы клипа индексами слов (оба конца включительно). */
export interface WordRange {
  w0: number;
  w1: number;
}

// ---------------------------------------------------------------- время

function pad(value: number, size = 2): string {
  return String(value).padStart(size, "0");
}

/** Таймкод `00:12.40` (§7.6), от часа — `1:02:03.40`; `withCs=false` — без сотых. */
export function formatTc(sec: number | null | undefined, { withCs = true }: { withCs?: boolean } = {}): string {
  if (sec === null || sec === undefined || !Number.isFinite(sec)) return "—";
  const negative = sec < 0;
  const total = Math.abs(sec);
  let whole = Math.floor(total);
  let cs = Math.round((total - whole) * 100);
  if (cs === 100) {
    whole += 1;
    cs = 0;
  }
  const h = Math.floor(whole / 3600);
  const m = Math.floor((whole % 3600) / 60);
  const s = whole % 60;
  const head = h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
  return `${negative ? "−" : ""}${head}${withCs ? `.${pad(cs)}` : ""}`;
}

/** Длительность источника: `12:34` или `1 ч 02 мин`. */
export function formatLength(sec: number | null | undefined): string {
  if (sec === null || sec === undefined || !Number.isFinite(sec)) return "—";
  const whole = Math.round(sec);
  const h = Math.floor(whole / 3600);
  const m = Math.floor((whole % 3600) / 60);
  if (h > 0) return `${h} ч ${pad(m)} мин`;
  return `${pad(m)}:${pad(whole % 60)}`;
}

/** Секунды клипа с одним знаком: `31,4 с`. */
export function formatSeconds(sec: number | null | undefined, digits = 1): string {
  if (sec === null || sec === undefined || !Number.isFinite(sec)) return "—";
  return `${sec.toLocaleString("ru-RU", { minimumFractionDigits: digits, maximumFractionDigits: digits })} с`;
}

/** Байты: `734 МБ`, `2,4 ГБ`. */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes)) return "—";
  const mb = bytes / (1024 * 1024);
  if (mb < 1) return `${Math.max(1, Math.round(bytes / 1024))} КБ`;
  if (mb < 1024) return `${Math.round(mb).toLocaleString("ru-RU")} МБ`;
  return `${(mb / 1024).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} ГБ`;
}

// ---------------------------------------------------------------- слова

/** Последнее слово, начавшееся не позже `t` (−1 — до первого слова). Бинарный поиск по `s`. */
export function wordIndexAt(words: readonly TranscriptWord[], t: number): number {
  let lo = 0;
  let hi = words.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (words[mid].s <= t) {
      found = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return found;
}

/** Слово, которое звучит в момент `t` (с допуском на паузу после него), иначе −1. */
export function activeWordAt(words: readonly TranscriptWord[], t: number, tolerance = 0.25): number {
  const idx = wordIndexAt(words, t);
  if (idx < 0) return -1;
  return t <= words[idx].e + tolerance ? idx : -1;
}

/** Слово, чьё начало ближе всего к `t` (для входа). */
export function nearestWordStart(words: readonly TranscriptWord[], t: number): number {
  if (words.length === 0) return -1;
  const idx = wordIndexAt(words, t);
  if (idx < 0) return 0;
  const next = idx + 1 < words.length ? idx + 1 : idx;
  return Math.abs(words[next].s - t) < Math.abs(words[idx].s - t) ? next : idx;
}

/** Слово, чей конец ближе всего к `t` (для выхода). */
export function nearestWordEnd(words: readonly TranscriptWord[], t: number): number {
  if (words.length === 0) return -1;
  let idx = wordIndexAt(words, t);
  if (idx < 0) return 0;
  // Кандидаты: текущее слово и предыдущее (если t попало в паузу сразу после него).
  let best = idx;
  let bestDist = Math.abs(words[idx].e - t);
  for (const other of [idx - 1, idx + 1]) {
    if (other < 0 || other >= words.length) continue;
    const dist = Math.abs(words[other].e - t);
    if (dist < bestDist) {
      best = other;
      bestDist = dist;
    }
  }
  idx = best;
  return idx;
}

/** Первое и последнее слово целиком внутри [start, end] (как `word_span` сервера); null — слов нет. */
export function wordSpan(words: readonly TranscriptWord[], start: number, end: number): WordRange | null {
  let first = wordIndexAt(words, start);
  if (first < 0 || words[first].s < start - 0.05) first += 1;
  while (first < words.length && words[first].e <= start) first += 1;
  let last = wordIndexAt(words, end);
  while (last >= 0 && words[last].e > end + 0.35) last -= 1;
  if (first < 0 || last < first || first >= words.length) return null;
  return { w0: first, w1: last };
}

/** Упорядочить и зажать границы в пределах транскрипта. */
export function clampRange(range: WordRange, count: number): WordRange {
  if (count <= 0) return { w0: 0, w1: 0 };
  const a = Math.max(0, Math.min(count - 1, Math.round(range.w0)));
  const b = Math.max(0, Math.min(count - 1, Math.round(range.w1)));
  return a <= b ? { w0: a, w1: b } : { w0: b, w1: a };
}

/** Время клипа по словам (без снаппинга: от начала первого до конца последнего слова). */
export function rangeTimes(words: readonly TranscriptWord[], range: WordRange): { start: number; end: number } {
  const first = words[range.w0];
  const last = words[range.w1];
  return { start: first ? first.s : 0, end: last ? last.e : 0 };
}

/** Предложение, в котором стоит слово (бинарный поиск по `w0`), −1 — нет. */
export function sentenceIndexOfWord(sentences: readonly TranscriptSentence[], wordIdx: number): number {
  let lo = 0;
  let hi = sentences.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (sentences[mid].w0 <= wordIdx) {
      found = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return found;
}

/** Предложения, попадающие в клип (по словам). */
export function sentencesInRange(sentences: readonly TranscriptSentence[], range: WordRange): TranscriptSentence[] {
  const first = sentenceIndexOfWord(sentences, range.w0);
  const last = sentenceIndexOfWord(sentences, range.w1);
  if (first < 0 || last < 0) return [];
  return sentences.slice(first, last + 1);
}

/** Текст слов диапазона (для подписи выделения). */
export function rangeText(words: readonly TranscriptWord[], range: WordRange, maxWords = 40): string {
  const out: string[] = [];
  for (let i = range.w0; i <= range.w1 && i < words.length; i += 1) {
    if (out.length >= maxWords) {
      out.push("…");
      break;
    }
    out.push(words[i].w);
  }
  return out.join(" ");
}

// ---------------------------------------------------------------- длительность

/** Длина клипа (services/jobs.py): 3–180 с; VK — не больше 60 с жёстко, цель отбора ≤ 58 с (§6.3.4). */
export const CLIP_MIN_SEC = 3;
export const CLIP_MAX_SEC = 180;
export const VK_HARD_MAX_SEC = 60;
export const VK_TARGET_MAX_SEC = 58;

/**
 * QC клипа (§6.3.9, `clipping/qc_clip.py` DURATION_RANGE) меряет ФИНАЛ: исходник + хук + стоп-кадры
 * вставок + вывод. Движок подрезает конец исходника под 58 с, а финал короче 20 с QC проваливает.
 */
export const FINAL_MIN_SEC = 20;
export const FINAL_MAX_SEC = 58;
/** Запас на неточность оценки до рендера (длина реплик TTS считается по числу знаков). */
const FINAL_TOLERANCE_SEC = 0.5;

/** Блокер «В рендер» по длительности финала из оценки оригинальности; null — в пределах или оценки нет. */
export function finalDurationBlocker(finalSec: number | null | undefined): string | null {
  if (finalSec === null || finalSec === undefined || !Number.isFinite(finalSec) || finalSec <= 0) return null;
  const rounded = Math.round(finalSec);
  if (finalSec < FINAL_MIN_SEC - FINAL_TOLERANCE_SEC)
    return `Итоговый ролик ≈ ${rounded} с — короче ${FINAL_MIN_SEC} с, QC не пропустит: возьмите фрагмент длиннее или добавьте хук и вывод`;
  if (finalSec > FINAL_MAX_SEC + FINAL_TOLERANCE_SEC)
    return `Итоговый ролик ≈ ${rounded} с — длиннее ${FINAL_MAX_SEC} с, QC не пропустит: сократите авторские реплики`;
  return null;
}

export type DurationZone = "too_short" | "short" | "ideal" | "long" | "too_long";

export interface DurationVerdict {
  zone: DurationZone;
  label: string;
  /** Сервер не примет клип такой длины. */
  blocking: boolean;
}

/** Зоны длительности инспектора: цель отбора 30 ± 15 с (§6.3.4), жёсткие пределы площадки. */
export function durationVerdict(sec: number, platform: Platform | null | undefined): DurationVerdict {
  const hardMax = platform === "vk" ? VK_HARD_MAX_SEC : CLIP_MAX_SEC;
  if (sec < CLIP_MIN_SEC)
    return { zone: "too_short", label: `Короче ${CLIP_MIN_SEC} с — не примет сервер`, blocking: true };
  if (sec > hardMax) {
    return {
      zone: "too_long",
      label: platform === "vk" ? `Для VK не длиннее ${VK_HARD_MAX_SEC} с` : `Не длиннее ${CLIP_MAX_SEC} с`,
      blocking: true,
    };
  }
  if (sec < 15) return { zone: "short", label: "Короткий: мало места для авторского слоя", blocking: false };
  if (platform === "vk" && sec > VK_TARGET_MAX_SEC)
    return {
      zone: "long",
      label: `VK: целимся в ≤ ${VK_TARGET_MAX_SEC} с — конец фрагмента подрежется под ролик ≤ ${FINAL_MAX_SEC} с`,
      blocking: false,
    };
  if (sec > FINAL_MAX_SEC)
    return {
      zone: "long",
      label: `Длиннее ${FINAL_MAX_SEC} с: конец фрагмента подрежется, чтобы ролик с авторским слоем уложился в ${FINAL_MAX_SEC} с`,
      blocking: false,
    };
  if (sec > 45) return { zone: "long", label: "Длинновато: цель отбора 30 ± 15 с", blocking: false };
  return { zone: "ideal", label: "В зоне цели 15–45 с", blocking: false };
}

// ---------------------------------------------------------------- раскадровка

export interface StoryboardGrid {
  interval_sec: number;
  tile_w: number;
  tile_h: number;
  cols: number;
  rows: number;
  count: number;
  sheets: readonly string[];
}

export interface StoryboardTile {
  index: number;
  sheet: string;
  /** Смещение плитки в листе, px (до масштабирования). */
  x: number;
  y: number;
  /** Размер листа, px. */
  sheetW: number;
  sheetH: number;
}

/** Плитка раскадровки для момента `t` (кадр каждые `interval_sec`, листы cols×rows). */
export function storyboardTile(grid: StoryboardGrid, t: number): StoryboardTile | null {
  if (!grid.count || grid.interval_sec <= 0 || grid.cols <= 0 || grid.rows <= 0) return null;
  const index = Math.max(0, Math.min(grid.count - 1, Math.floor(t / grid.interval_sec)));
  const perSheet = grid.cols * grid.rows;
  const sheetIdx = Math.floor(index / perSheet);
  const sheet = grid.sheets[sheetIdx];
  if (!sheet) return null;
  const within = index % perSheet;
  return {
    index,
    sheet,
    x: (within % grid.cols) * grid.tile_w,
    y: Math.floor(within / grid.cols) * grid.tile_h,
    sheetW: grid.cols * grid.tile_w,
    sheetH: grid.rows * grid.tile_h,
  };
}

// ---------------------------------------------------------------- авторский слой (§6.3.8)

/** Лимиты сценария — константы движка `clipping/author/script.py` (сервер отвечает 422 сверх них). */
export const AUTHOR_LIMITS = {
  coldOpen: 160,
  insert: 220,
  outro: 160,
  onScreen: 60,
  title: 48,
  inserts: 2,
} as const;

export const VOICE_SEGMENTS: readonly AuthorVoiceSegment[] = ["cold_open", "insert_0", "insert_1", "outro"];

export interface AuthorDraft {
  cold_open: { vo_text: string; on_screen: string; duck_source: boolean };
  inserts: Array<{
    after_sid: number;
    mode: Schemas["AuthorInsertMode"];
    vo_text: string;
    on_screen: string;
  }>;
  outro: { vo_text: string; cta_style: Schemas["AuthorCtaStyle"] };
  on_screen_title: string;
}

export function emptyAuthorDraft(): AuthorDraft {
  return {
    cold_open: { vo_text: "", on_screen: "", duck_source: true },
    inserts: [],
    outro: { vo_text: "", cta_style: "question" },
    on_screen_title: "",
  };
}

/** Ответ сервера (поля по умолчанию могут отсутствовать) → полный черновик редактора. */
export function draftFromBody(body: AuthorScriptBody | null | undefined): AuthorDraft {
  const base = emptyAuthorDraft();
  if (!body) return base;
  return {
    cold_open: {
      vo_text: body.cold_open?.vo_text ?? "",
      on_screen: body.cold_open?.on_screen ?? "",
      duck_source: body.cold_open?.duck_source ?? true,
    },
    inserts: (body.inserts ?? []).slice(0, AUTHOR_LIMITS.inserts).map((ins) => ({
      after_sid: ins.after_sid,
      mode: ins.mode ?? "freeze_zoom",
      vo_text: ins.vo_text ?? "",
      on_screen: ins.on_screen ?? "",
    })),
    outro: { vo_text: body.outro?.vo_text ?? "", cta_style: body.outro?.cta_style ?? "question" },
    on_screen_title: body.on_screen_title ?? "",
  };
}

/** Черновик редактора → тело `AuthorScriptBody` (пробелы по краям срезаны, пустые вставки убраны). */
export function bodyFromDraft(draft: AuthorDraft): AuthorScriptBody {
  return {
    cold_open: {
      vo_text: draft.cold_open.vo_text.trim(),
      on_screen: draft.cold_open.on_screen.trim(),
      duck_source: draft.cold_open.duck_source,
    },
    inserts: draft.inserts
      .filter((ins) => ins.vo_text.trim() || ins.on_screen.trim())
      .map((ins) => ({
        after_sid: ins.after_sid,
        mode: ins.mode,
        vo_text: ins.vo_text.trim(),
        on_screen: ins.on_screen.trim(),
      })),
    outro: { vo_text: draft.outro.vo_text.trim(), cta_style: draft.outro.cta_style },
    on_screen_title: draft.on_screen_title.trim(),
  };
}

/** В черновике нет ни одной реплики и ни одной надписи. */
export function isDraftEmpty(draft: AuthorDraft): boolean {
  return (
    !draft.cold_open.vo_text.trim() &&
    !draft.cold_open.on_screen.trim() &&
    !draft.outro.vo_text.trim() &&
    !draft.on_screen_title.trim() &&
    draft.inserts.every((ins) => !ins.vo_text.trim() && !ins.on_screen.trim())
  );
}

export function draftsEqual(a: AuthorDraft, b: AuthorDraft): boolean {
  return JSON.stringify(bodyFromDraft(a)) === JSON.stringify(bodyFromDraft(b));
}

/** Превышения лимитов по полям (ключ — путь поля в редакторе). */
export function draftLimitErrors(draft: AuthorDraft): Record<string, string> {
  const out: Record<string, string> = {};
  const over = (value: string, limit: number) => value.trim().length > limit;
  if (over(draft.on_screen_title, AUTHOR_LIMITS.title))
    out.on_screen_title = `Не длиннее ${AUTHOR_LIMITS.title} символов`;
  if (over(draft.cold_open.vo_text, AUTHOR_LIMITS.coldOpen))
    out["cold_open.vo_text"] = `Не длиннее ${AUTHOR_LIMITS.coldOpen} символов`;
  if (over(draft.cold_open.on_screen, AUTHOR_LIMITS.onScreen))
    out["cold_open.on_screen"] = `Не длиннее ${AUTHOR_LIMITS.onScreen} символов`;
  draft.inserts.forEach((ins, i) => {
    if (over(ins.vo_text, AUTHOR_LIMITS.insert))
      out[`inserts.${i}.vo_text`] = `Не длиннее ${AUTHOR_LIMITS.insert} символов`;
    if (over(ins.on_screen, AUTHOR_LIMITS.onScreen))
      out[`inserts.${i}.on_screen`] = `Не длиннее ${AUTHOR_LIMITS.onScreen} символов`;
  });
  const sids = draft.inserts.map((ins) => ins.after_sid);
  if (new Set(sids).size !== sids.length) out.inserts = "Две вставки после одного предложения";
  if (over(draft.outro.vo_text, AUTHOR_LIMITS.outro))
    out["outro.vo_text"] = `Не длиннее ${AUTHOR_LIMITS.outro} символов`;
  return out;
}

/** Текст реплики для записи голоса. */
export function segmentText(draft: AuthorDraft, segment: AuthorVoiceSegment): string {
  switch (segment) {
    case "cold_open":
      return draft.cold_open.vo_text.trim();
    case "insert_0":
      return draft.inserts[0]?.vo_text.trim() ?? "";
    case "insert_1":
      return draft.inserts[1]?.vo_text.trim() ?? "";
    case "outro":
      return draft.outro.vo_text.trim();
  }
}

/** Грубая оценка длительности реплики голосом (≈ 14 символов в секунду) — только для подсказки. */
export function estimateSpeechSec(text: string): number {
  const chars = text.trim().length;
  return chars ? Math.max(0.8, chars / 14) : 0;
}
