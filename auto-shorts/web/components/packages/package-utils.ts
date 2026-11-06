/**
 * Чистые помощники экрана «Пакеты» (без DOM и React): группировка по каналам, время МСК для поля
 * «когда опубликован», erid из описания. Проверяются отдельно от компонентов.
 */
import type { UploadPackage } from "@/lib/queries/packages";

export interface ChannelGroupData {
  channelId: string;
  displayName: string;
  platform: UploadPackage["channel"]["platform"];
  accountHint: string;
  items: UploadPackage[];
}

/** Пакеты по каналам (§7.5 ChannelGroup): внутри — сначала те, что ждут ссылку, затем по давности. */
export function groupByChannel(items: UploadPackage[]): ChannelGroupData[] {
  const groups = new Map<string, ChannelGroupData>();
  for (const pkg of items) {
    const group = groups.get(pkg.channel.id) ?? {
      channelId: pkg.channel.id,
      displayName: pkg.channel.display_name,
      platform: pkg.channel.platform,
      accountHint: pkg.channel.account_hint,
      items: [],
    };
    group.items.push(pkg);
    groups.set(pkg.channel.id, group);
  }
  const rank = (pkg: UploadPackage) => (pkg.status === "downloaded" ? 0 : pkg.status === "ready" ? 1 : 2);
  const out = [...groups.values()];
  for (const group of out) {
    group.items.sort((a, b) => rank(a) - rank(b) || a.created_at.localeCompare(b.created_at));
  }
  // YouTube — основная площадка, затем по названию.
  return out.sort(
    (a, b) =>
      (a.platform === b.platform ? 0 : a.platform === "youtube" ? -1 : 1) ||
      a.displayName.localeCompare(b.displayName, "ru"),
  );
}

/** МСК без перехода на летнее время: смещение постоянное. */
const MSK_OFFSET = "+03:00";

/** ISO-время → значение `<input type="datetime-local">` в МСК (`2026-09-27T14:05`). */
export function toMskInput(iso: string | null | undefined): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Europe/Moscow",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  const get = (type: string) => parts.find((part) => part.type === type)?.value ?? "00";
  return `${get("year")}-${get("month")}-${get("day")}T${get("hour")}:${get("minute")}`;
}

/** Значение поля (МСК) → ISO с явным смещением; пусто — «сейчас» на сервере. */
export function fromMskInput(value: string): string | null {
  return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value) ? `${value}:00${MSK_OFFSET}` : null;
}

/** Маркировка рекламы в описании: «Реклама. <рекламодатель>. erid: <erid>» (38-ФЗ). */
export function eridOf(description: string): string | null {
  const match = /erid[:\s]+([A-Za-z0-9]{4,64})/iu.exec(description);
  return match ? match[1] : null;
}
