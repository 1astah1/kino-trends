/**
 * Разбор ссылки на опубликованный ролик YouTube или VK — зеркало `src/autoshorts/domain/url_parse.py`
 * (docs/ARCHITECTURE.md §5.2 `parse-url`, §7.3 «Пакеты»).
 *
 * Нужен для живого предпросмотра в поле «Ссылка на опубликованный ролик»: оператор видит площадку,
 * вид ролика, ID и алиас атрибуции ещё до запроса. **Истина — бэкенд**: регистрацию решает
 * `POST /publications`, и при расхождении прав `url_parse.py`. Поэтому экран не блокирует отправку
 * по результату этого модуля, а только подсказывает.
 *
 * Модуль без импортов и без DOM (его проверяют отдельно). В нём нет литералов вида `схема://хост`:
 * scripts/check-no-external.mjs ищет такие строки в собранном бандле (§7.7).
 */

export type VideoPlatform = "youtube" | "vk";
export type VideoKind = "short" | "clip" | "video";

/** Машинные причины отказа — те же, что `UnsupportedUrl.reason` (details.reason в ответе 422). */
export type UnsupportedUrlReason =
  | "empty"
  | "too_long"
  | "non_ascii"
  | "whitespace"
  | "bad_scheme"
  | "no_host"
  | "unsupported_host"
  | "not_a_video"
  | "bad_video_id";

export interface ParsedVideoUrl {
  platform: VideoPlatform;
  /** 11 символов YouTube | `<owner>_<id>` VK. */
  platform_video_id: string;
  platform_kind: VideoKind;
  canonical_url: string;
  /** `yt_<id>` | `vk_<owner>_<id>` (алфавит start-параметра Telegram). */
  alias_code: string;
  /** VK: владелец клипа (`-<group_id>` или `<user_id>`). */
  owner_ref: string | null;
}

export type ParseVideoUrlResult =
  { ok: true; value: ParsedVideoUrl } | { ok: false; reason: UnsupportedUrlReason; message: string; host?: string };

export const URL_MAX = 2000;

/** Русские подписи причин отказа (совпадают по смыслу с сообщениями `url_parse.py`). */
export const UNSUPPORTED_URL_MESSAGES: Record<UnsupportedUrlReason, string> = {
  empty: "Пустая ссылка",
  too_long: "Ссылка длиннее 2000 символов",
  non_ascii: "В ссылке кириллица или другие не-ASCII символы — скопируйте адрес из адресной строки или «Поделиться»",
  whitespace: "В ссылке пробелы или управляющие символы",
  bad_scheme: "Нужна ссылка http(s)",
  no_host: "В ссылке нет домена",
  unsupported_host: "Ссылка не на YouTube или VK",
  not_a_video: "Это ссылка не на ролик (канал, плейлист, поиск)",
  bad_video_id: "Неверный ID ролика в ссылке",
};

const YOUTUBE_ID_RE = /^[A-Za-z0-9_-]{11}$/;
// `\p{Nd}` вместо `\d`: в Python `\d` — любая десятичная цифра Юникода (после unquote в `z`,
// `oid`, `id` могут оказаться не-ASCII цифры — тогда, как у бэкенда, отказ bad_video_id).
const VK_REF_RE = /(clip|video)(-?[1-9]\p{Nd}{0,19})_([1-9]\p{Nd}{0,19})/u;
const VK_PATH_RE = /^\/(clip|video)(-?[1-9]\p{Nd}{0,19})_([1-9]\p{Nd}{0,19})\/?$/u;
const VK_OWNER_RE = /^-?[1-9]\p{Nd}{0,19}$/u;
const VK_ID_RE = /^[1-9]\p{Nd}{0,19}$/u;
const TRACKING_CODE_RE = /^[A-Za-z0-9_-]{2,64}$/;
const SCHEME_RE = /^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//;
/** `javascript:`, `data:` — но не `host:443`. */
const BARE_SCHEME_RE = /^[a-zA-Z][a-zA-Z0-9+.-]*:(?!\d)/;
const SCHEME_CHARS_RE = /^[a-zA-Z][a-zA-Z0-9+.-]*$/;

const YOUTUBE_HOSTS = new Set(["youtube.com", "youtu.be", "youtube-nocookie.com", "studio.youtube.com"]);
const VK_HOSTS = new Set(["vk.com", "vk.ru", "vkvideo.ru"]);
const HOST_PREFIXES = ["www.", "m.", "music.", "mobile."];
const YT_PATH_ID = new Set(["shorts", "embed", "live", "v", "e"]);

/** Хост канонической ссылки по площадке (таблица, а не литерал URL — см. шапку модуля). */
const CANONICAL_HOST: Record<VideoPlatform, string> = { youtube: "www.youtube.com", vk: "vk.com" };
const HTTPS = "https:";

// ---------------------------------------------------------------- stdlib Python: urlsplit, unquote, parse_qs

interface SplitUrl {
  scheme: string;
  netloc: string;
  path: string;
  query: string;
}

/** `urllib.parse.urlsplit` в объёме, нужном разбору (схема в нижнем регистре, netloc до `/?#`). */
function urlsplit(url: string): SplitUrl {
  let rest = url;
  let scheme = "";
  const colon = rest.indexOf(":");
  if (colon > 0 && SCHEME_CHARS_RE.test(rest.slice(0, colon))) {
    scheme = rest.slice(0, colon).toLowerCase();
    rest = rest.slice(colon + 1);
  }
  let netloc = "";
  if (rest.startsWith("//")) {
    rest = rest.slice(2);
    const end = rest.search(/[/?#]/);
    netloc = end === -1 ? rest : rest.slice(0, end);
    rest = end === -1 ? "" : rest.slice(end);
  }
  const hash = rest.indexOf("#");
  if (hash !== -1) rest = rest.slice(0, hash);
  let query = "";
  const question = rest.indexOf("?");
  if (question !== -1) {
    query = rest.slice(question + 1);
    rest = rest.slice(0, question);
  }
  return { scheme, netloc, path: rest, query };
}

const HEX_RE = /^[0-9a-fA-F]{2}$/;

/** `urllib.parse.unquote`: %XX → байты → UTF-8 с заменой битых последовательностей. */
function unquote(text: string): string {
  if (!text.includes("%")) return text;
  const bytes: number[] = [];
  for (let i = 0; i < text.length; i += 1) {
    const hex = text.slice(i + 1, i + 3);
    if (text[i] === "%" && HEX_RE.test(hex)) {
      bytes.push(Number.parseInt(hex, 16));
      i += 2;
    } else {
      const code = text.charCodeAt(i);
      if (code < 0x80) bytes.push(code);
      else bytes.push(...new TextEncoder().encode(text[i]));
    }
  }
  return new TextDecoder("utf-8").decode(new Uint8Array(bytes));
}

/** `urllib.parse.parse_qs(query, keep_blank_values=False)`: разделитель `&`, `+` → пробел. */
function parseQs(query: string): Map<string, string[]> {
  const out = new Map<string, string[]>();
  for (const pair of query.split("&")) {
    if (!pair) continue;
    const eq = pair.indexOf("=");
    if (eq === -1) continue;
    const rawValue = pair.slice(eq + 1);
    if (!rawValue.length) continue;
    const name = unquote(pair.slice(0, eq).replace(/\+/g, " "));
    const value = unquote(rawValue.replace(/\+/g, " "));
    const list = out.get(name);
    if (list) list.push(value);
    else out.set(name, [value]);
  }
  return out;
}

function first(qs: Map<string, string[]>, key: string): string {
  return qs.get(key)?.[0] ?? "";
}

/** `str.isalnum()` / `str.isdigit()` Python — с учётом Юникода. */
function isAlnum(char: string): boolean {
  return /^[\p{L}\p{N}]$/u.test(char);
}

function isDigit(char: string): boolean {
  return /^\p{Nd}$/u.test(char);
}

/** Символ (кодовая точка, как индекс строки в Python) перед позицией `index` UTF-16. */
function codePointBefore(text: string, index: number): string {
  const low = text.charCodeAt(index - 1);
  const start = low >= 0xdc00 && low <= 0xdfff && index >= 2 ? index - 2 : index - 1;
  return String.fromCodePoint(text.codePointAt(start) ?? 0x2f);
}

// ---------------------------------------------------------------- разбор

class Unsupported {
  readonly reason: UnsupportedUrlReason;
  readonly message: string;
  readonly host?: string;

  constructor(reason: UnsupportedUrlReason, message: string, host?: string) {
    this.reason = reason;
    this.message = message;
    this.host = host;
  }
}

function reject(reason: UnsupportedUrlReason, message?: string, host?: string): never {
  throw new Unsupported(reason, message ?? UNSUPPORTED_URL_MESSAGES[reason], host);
}

function hostOf(netloc: string): string {
  let host = netloc
    .slice(netloc.lastIndexOf("@") + 1)
    .split(":", 1)[0]
    .toLowerCase()
    .replace(/\.+$/, "");
  for (const prefix of HOST_PREFIXES) {
    if (host.startsWith(prefix)) {
      host = host.slice(prefix.length);
      break;
    }
  }
  return host;
}

/** Каноническая ссылка, как у бэкенда: `…/shorts/<id>`, `…/watch?v=<id>`, `…/clip<owner>_<id>`. */
export function canonicalUrl(platform: VideoPlatform, videoId: string, kind: VideoKind): string {
  const origin = `${HTTPS}//${CANONICAL_HOST[platform]}`;
  if (platform === "youtube") {
    return kind === "short" ? `${origin}/shorts/${videoId}` : `${origin}/watch?v=${videoId}`;
  }
  return `${origin}/${kind === "clip" ? "clip" : "video"}${videoId}`;
}

/** `yt_<id>`; `null`, если ID не из 11 символов. */
export function youtubeAlias(videoId: string): string | null {
  return YOUTUBE_ID_RE.test(videoId) ? `yt_${videoId}` : null;
}

/** `vk_-123_456`; `null`, если владелец или ID вне формата VK. */
export function vkAlias(owner: string, videoId: string): string | null {
  if (!VK_OWNER_RE.test(owner) || !VK_ID_RE.test(videoId)) return null;
  const code = `vk_${owner}_${videoId}`;
  return TRACKING_CODE_RE.test(code) ? code : null;
}

function parseYoutube(host: string, path: string, query: string): ParsedVideoUrl {
  const segs = path.split("/").filter(Boolean);
  const qs = parseQs(query);
  let videoId: string | null = null;
  let kind: VideoKind = "video";
  if (host === "youtu.be") {
    videoId = segs[0] ?? null;
  } else if (host === "studio.youtube.com") {
    // studio.youtube.com/video/ID/edit — ссылка из Творческой студии
    if (segs.length >= 2 && segs[0] === "video") videoId = segs[1];
  } else if (segs[0] === "watch") {
    videoId = first(qs, "v") || segs[1] || null;
  } else if (segs[0] === "attribution_link") {
    const inner = unquote(first(qs, "u"));
    videoId = first(parseQs(urlsplit(inner).query), "v") || null;
  } else if (segs.length >= 2 && YT_PATH_ID.has(segs[0])) {
    videoId = segs[1];
    if (segs[0] === "shorts") kind = "short";
  }
  if (!videoId) reject("not_a_video", "Это ссылка YouTube, но не на ролик (канал, плейлист, поиск)");
  if (!YOUTUBE_ID_RE.test(videoId)) {
    reject("bad_video_id", "ID ролика YouTube — ровно 11 символов A–Z, a–z, 0–9, _ и -");
  }
  return {
    platform: "youtube",
    platform_video_id: videoId,
    platform_kind: kind,
    canonical_url: canonicalUrl("youtube", videoId, kind),
    alias_code: `yt_${videoId}`,
    owner_ref: null,
  };
}

function vkMatch(text: string): RegExpExecArray | null {
  const match = VK_REF_RE.exec(text);
  if (!match) return null;
  // границы: перед clip/video не буква, после id не цифра (pl_…, %2F — ок)
  const start = match.index;
  const end = start + match[0].length;
  const before = start > 0 ? codePointBefore(text, start) : "/";
  const after = end < text.length ? String.fromCodePoint(text.codePointAt(end) ?? 0x2f) : "/";
  if (isAlnum(before) || isDigit(after)) return null;
  return match;
}

function parseVk(path: string, query: string): ParsedVideoUrl {
  const qs = parseQs(query);
  let match: RegExpExecArray | null = null;
  // 1) z=clip-1_2 / z=video-1_2%2Fpl_… (ленты клипов, стены, каталоги) — главнее пути
  const z = unquote(first(qs, "z"));
  if (z) match = vkMatch(z.split("/")[0]);
  // 2) /clip-1_2, /video-1_2
  if (!match) match = VK_PATH_RE.exec(path);
  let owner: string;
  let vid: string;
  let kind: VideoKind;
  if (match) {
    owner = match[2];
    vid = match[3];
    kind = match[1] === "clip" ? "clip" : "video";
  } else if (path.replace(/\/+$/, "") === "/video_ext.php") {
    // 3) плеер для вставки: video_ext.php?oid=-1&id=2&hash=…
    owner = first(qs, "oid");
    vid = first(qs, "id");
    if (!VK_OWNER_RE.test(owner) || !VK_ID_RE.test(vid)) {
      reject("bad_video_id", "В ссылке плеера VK нет oid и id ролика");
    }
    kind = "video";
  } else {
    reject("not_a_video", "Это ссылка VK, но не на клип или видео");
  }
  const videoId = `${owner}_${vid}`;
  const alias = vkAlias(owner, vid);
  if (!alias) reject("bad_video_id", "Неверный ID ролика VK");
  return {
    platform: "vk",
    platform_video_id: videoId,
    platform_kind: kind,
    canonical_url: canonicalUrl("vk", videoId, kind),
    alias_code: alias,
    owner_ref: owner,
  };
}

function parseOrThrow(url: string): ParsedVideoUrl {
  let text = (url ?? "").trim();
  if (!text) reject("empty");
  if ([...text].length > URL_MAX) reject("too_long");
  if (/[^\x00-\x7f]/.test(text)) reject("non_ascii");
  if (/[\x00-\x20]/.test(text)) reject("whitespace");
  if (!SCHEME_RE.test(text)) {
    if (BARE_SCHEME_RE.test(text)) reject("bad_scheme");
    text = text.startsWith("//") ? HTTPS + text : `${HTTPS}//${text}`;
  }
  const parts = urlsplit(text);
  if (parts.scheme !== "http" && parts.scheme !== "https") reject("bad_scheme");
  const host = hostOf(parts.netloc);
  if (!host) reject("no_host");
  if (YOUTUBE_HOSTS.has(host)) return parseYoutube(host, parts.path, parts.query);
  if (VK_HOSTS.has(host)) return parseVk(parts.path, parts.query);
  return reject("unsupported_host", `Ссылка не на YouTube или VK: ${host}`, host);
}

/** Ссылка на ролик → площадка, ID, вид, каноническая ссылка, алиас. Иначе причина отказа. */
export function parseVideoUrl(url: string): ParseVideoUrlResult {
  try {
    return { ok: true, value: parseOrThrow(url) };
  } catch (error) {
    if (error instanceof Unsupported) {
      return { ok: false, reason: error.reason, message: error.message, ...(error.host ? { host: error.host } : {}) };
    }
    throw error;
  }
}

/**
 * Ссылка/ID сообщества VK из профиля канала → владелец клипа (`normalize_vk_owner`):
 * `club123`, `public123`, `-123`, `vk.com/club123` → `-123`; `id45` / `45` → `45`.
 * Короткое имя (`vk.com/umka`) — `null`: без API VK его не сопоставить.
 */
export function normalizeVkOwner(ref: string | null | undefined): string | null {
  if (!ref) return null;
  let text = ref.trim();
  if (text.includes("/")) {
    const path = urlsplit(text.includes("://") ? text : `${HTTPS}//${text}`).path;
    const segs = path.split("/").filter(Boolean);
    text = segs.length ? segs[segs.length - 1] : "";
  }
  let match = /^(club|public|event)(\d{1,20})$/.exec(text);
  if (match) return `-${match[2]}`;
  match = /^id(\d{1,20})$/.exec(text);
  if (match) return match[1];
  if (/^-?\d{1,20}$/.test(text)) return text;
  return null;
}

export const PLATFORM_LABELS: Record<VideoPlatform, string> = { youtube: "YouTube", vk: "VK" };

export const VIDEO_KIND_LABELS: Record<VideoKind, string> = {
  short: "Shorts",
  clip: "Клип",
  video: "Видео",
};
