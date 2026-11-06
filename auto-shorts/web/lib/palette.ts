/**
 * Разбор строки палитры Ctrl/Cmd+K (§7.3): переход по `video_uid`, `yt_…`/`vk_…`, коду пакета,
 * каналу. Экраны-цели появляются в M2–M4; пока они заглушки, переход открывает их с `q`/`channel`.
 */
import { routeHref } from "@/lib/nav";

export interface PaletteTarget {
  id: string;
  label: string;
  hint: string;
  href: string;
}

/** `video_uid`: 12 шестнадцатеричных символов (a1b2c3d4e5f6). */
export const VIDEO_UID_RE = /^[0-9a-f]{12}$/;
/** Алиас публикации: `yt_<id>` или `vk_<owner>_<id>` (§0 «Атрибуция»). */
export const PUBLICATION_ALIAS_RE = /^(?:yt_[A-Za-z0-9_-]{11}|vk_-?\d+_\d+)$/;
/** Код пакета: 5 символов Crockford base32 в нижнем регистре (без i, l, o, u). */
export const PACKAGE_CODE_RE = /^[0-9a-hjkmnp-tv-z]{5}$/;
/** Слаг канала (§5.1). */
export const SLUG_RE = /^[a-z0-9][a-z0-9_-]{1,39}$/;

export function resolvePaletteQuery(raw: string): PaletteTarget[] {
  const query = raw.trim();
  if (!query) return [];
  const targets: PaletteTarget[] = [];
  const lower = query.toLowerCase();

  if (VIDEO_UID_RE.test(lower)) {
    targets.push({
      id: `job:${lower}`,
      label: `Ролик ${lower}`,
      hint: "Конвейер",
      href: routeHref("/production", { q: lower }),
    });
  }
  if (PUBLICATION_ALIAS_RE.test(query)) {
    targets.push({
      id: `publication:${query}`,
      label: `Публикация ${query}`,
      hint: "Аналитика",
      href: routeHref("/analytics", { tab: "videos", q: query }),
    });
  }
  if (PACKAGE_CODE_RE.test(lower)) {
    targets.push({
      id: `package:${lower}`,
      label: `Пакет с кодом ${lower}`,
      hint: "Пакеты",
      href: routeHref("/packages", { q: lower }),
    });
  }
  if (SLUG_RE.test(lower) && !VIDEO_UID_RE.test(lower)) {
    targets.push({
      id: `channel:${lower}`,
      label: `Канал ${lower}`,
      hint: "Каналы",
      href: routeHref("/channels", { channel: lower }),
    });
  }
  return targets;
}
