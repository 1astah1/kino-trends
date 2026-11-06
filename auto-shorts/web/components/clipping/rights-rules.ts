/**
 * Правила записи прав на клиенте — зеркало `clipping/source_ingest.validate_rights` движка
 * (docs/ARCHITECTURE.md §0.1 п. 14, §5.2, OWNER-DECISIONS п. 5). Сервер проверяет то же самое
 * и остаётся главным: эти правила только показывают ошибку до отправки и по-русски.
 *
 * - без прав источника нет;
 * - всё, кроме `own`, требует `attribution_text`;
 * - CC BY-SA, любые NC и ND отклоняются (SA и NC несовместимы с промо-баннером, ND запрещает
 *   адаптированный клип) — в названии лицензии, её URL, атрибуции, а для cc_by и в заметке;
 * - `valid_until` в прошлом отклоняется.
 */
import type { Schemas } from "@/lib/api/client";
import type { ValidationError, ValidationIssue } from "@/lib/api/errors";

export type RightsType = Schemas["RightsType"];
export type AllowlistStatus = Schemas["AllowlistStatus"];
export type SourceRightsIn = Schemas["SourceRightsIn"];

export interface RightsFormValues {
  rights_type: RightsType | "";
  owner_name: string;
  proof_note: string;
  owner_channel_url: string;
  attribution_text: string;
  license: string;
  license_url: string;
  valid_until: string;
  allowlist_status: AllowlistStatus | "";
  proof_upload_id: string | null;
}

export const EMPTY_RIGHTS: RightsFormValues = {
  rights_type: "",
  owner_name: "",
  proof_note: "",
  owner_channel_url: "",
  attribution_text: "",
  license: "",
  license_url: "",
  valid_until: "",
  allowlist_status: "",
  proof_upload_id: null,
};

export type RightsField = Exclude<keyof RightsFormValues, "proof_upload_id">;

// Маркеры SA / NC / ND — те же регулярки, что в source_ingest._BAD_LICENSE (флаги i + u для кириллицы).
const BAD_LICENSE: ReadonlyArray<{ code: "share_alike" | "non_commercial" | "no_derivatives"; re: RegExp }> = [
  {
    code: "share_alike",
    re: /(by[-_ ]?(nc[-_ ]?)?sa\b|share[-_ ]?alike|licenses\/by(-nc)?-sa|сохранени[а-яё]* услови)/iu,
  },
  { code: "non_commercial", re: /(by[-_ ]?nc|cc[-_ ]?nc|non[-_ ]?commercial|licenses\/by-nc|некоммерческ)/iu },
  {
    code: "no_derivatives",
    re: /(by[-_ ]?(nc[-_ ]?)?nd\b|cc[-_ ]?nd|no[-_ ]?deriv|licenses\/by(-nc)?-nd|без производн)/iu,
  },
];

const BAD_LICENSE_LABELS: Record<(typeof BAD_LICENSE)[number]["code"], string> = {
  share_alike: "SA (с сохранением условий)",
  non_commercial: "NC (некоммерческая)",
  no_derivatives: "ND (без производных)",
};

/** Несовместимые маркеры лицензии в текстах (коды движка). */
export function licenseProblems(...texts: Array<string | null | undefined>): string[] {
  const found: string[] = [];
  for (const text of texts) {
    if (!text) continue;
    for (const { code, re } of BAD_LICENSE) {
      if (re.test(text) && !found.includes(code)) found.push(code);
    }
  }
  return found;
}

/** Ссылку без схемы (`youtube.com/@канал`) дополняем `https://`. */
export function withScheme(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) return "";
  return /^https?:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`;
}

const LINK_RE = /^https?:\/\/[^\s/]+\.[^\s]{2,}$/i;

function todayIso(): string {
  const now = new Date();
  const mm = String(now.getMonth() + 1).padStart(2, "0");
  const dd = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${mm}-${dd}`;
}

/** Ошибки формы прав по полям; пустой объект — можно отправлять. */
export function validateRights(
  values: RightsFormValues,
  today: string = todayIso(),
): Partial<Record<RightsField, string>> {
  const errors: Partial<Record<RightsField, string>> = {};
  if (!values.rights_type) {
    errors.rights_type = "Выберите основание: без прав источник не создаётся";
    return errors;
  }
  if (!values.owner_name.trim()) errors.owner_name = "Укажите правообладателя";
  else if (values.owner_name.trim().length > 300) errors.owner_name = "Не длиннее 300 символов";
  if (values.proof_note.trim().length < 3) errors.proof_note = "Чем подтверждено: договор, страница лицензии, дата";
  if (values.rights_type !== "own" && !values.attribution_text.trim()) {
    errors.attribution_text = "Для чужих прав атрибуция обязательна: автор, название, источник, лицензия";
  } else if (values.attribution_text.trim().length > 1000) {
    errors.attribution_text = "Не длиннее 1000 символов";
  }
  const cc = values.rights_type === "cc_by";
  const noteLicenses = cc ? (values.proof_note.match(/cc[-_ ]?by[-_ a-z]*/gi) ?? []) : [];
  const bad = licenseProblems(values.license, values.license_url, values.attribution_text, ...noteLicenses);
  if (bad.length) {
    const labels = bad.map((code) => BAD_LICENSE_LABELS[code as keyof typeof BAD_LICENSE_LABELS] ?? code).join(", ");
    errors.license = `Лицензия не принимается: ${labels}. CC BY-SA и NC несовместимы с промо-баннером, ND запрещает нарезку`;
  }
  if (values.valid_until && values.valid_until < today) errors.valid_until = "Срок прав уже истёк";
  const url = withScheme(values.owner_channel_url);
  if (url && !LINK_RE.test(url)) errors.owner_channel_url = "Не похоже на ссылку";
  const licenseUrl = withScheme(values.license_url);
  if (licenseUrl && !LINK_RE.test(licenseUrl)) errors.license_url = "Не похоже на ссылку";
  return errors;
}

/** Мягкие предупреждения до отправки (как `warnings` движка). */
export function rightsHints(values: RightsFormValues): string[] {
  const hints: string[] = [];
  if (!values.rights_type) return hints;
  if (values.rights_type !== "own")
    hints.push("Чужие права: ролик пройдёт гейт, только если авторский вклад не меньше 30%.");
  if (
    (values.rights_type === "licensed_written" || values.rights_type === "licensed_platform") &&
    values.allowlist_status !== "allowlisted"
  ) {
    hints.push("Канал не подтверждён в allowlist Content ID правообладателя — возможны заявки.");
  }
  if (values.rights_type === "cc_by") {
    hints.push("CC BY: атрибуция попадёт в описание каждого ролика.");
    if (!values.license.trim() && !values.license_url.trim())
      hints.push("Не указана версия лицензии CC BY (например, «CC BY 4.0»).");
  }
  if (values.rights_type === "public_domain")
    hints.push("Общественное достояние: проверьте, нет ли внутри чужой музыки.");
  return hints;
}

/** Значения формы → тело `SourceRightsIn` (пустые строки → null). */
export function rightsBody(values: RightsFormValues): SourceRightsIn {
  const opt = (value: string) => (value.trim() ? value.trim() : null);
  return {
    rights_type: values.rights_type as RightsType,
    owner_name: values.owner_name.trim(),
    proof_note: values.proof_note.trim(),
    owner_channel_url: opt(withScheme(values.owner_channel_url)),
    proof_upload_id: values.proof_upload_id,
    allowlist_status: values.allowlist_status || null,
    attribution_text: opt(values.attribution_text),
    license: opt(values.license),
    license_url: opt(withScheme(values.license_url)),
    valid_until: values.valid_until || null,
  };
}

/** Запись прав с сервера → значения формы (редактирование прав источника). */
export function rightsFormFrom(rights: Schemas["SourceRights"]): RightsFormValues {
  return {
    rights_type: rights.rights_type,
    owner_name: rights.owner_name,
    proof_note: rights.proof_note,
    owner_channel_url: rights.owner_channel_url ?? "",
    attribution_text: rights.attribution_text ?? "",
    license: rights.license ?? "",
    license_url: rights.license_url ?? "",
    valid_until: rights.valid_until ?? "",
    allowlist_status: rights.allowlist_status,
    proof_upload_id: null,
  };
}

/**
 * 422 сервера → ошибки полей формы прав. Проверка прав идёт внутри модели, поэтому ошибка приходит
 * с `loc = [body, rights]` (создание источника) или `[body]` (PUT прав) и `type = rights_rejected`;
 * коды из `msg` раскладываем по полям. `base` — имя вложенного поля с правами или null.
 */
export function rightsServerErrors(
  error: ValidationError,
  base: "rights" | null,
): { fields: Partial<Record<RightsField, string>>; general: string | null; other: Record<string, string> } {
  const fields: Partial<Record<RightsField, string>> = {};
  const other: Record<string, string> = {};
  let general: string | null = null;
  const raw = Array.isArray(error.details.errors) ? (error.details.errors as ValidationIssue[]) : [];
  for (const item of raw) {
    if (!item || !Array.isArray(item.loc)) continue;
    const path = item.loc.filter((part) => part !== "body").map(String);
    if (base && path[0] !== base) {
      if (path.length) other[path[0]] = item.msg;
      continue;
    }
    const rest = base ? path.slice(1) : path;
    const field = rest[0] as RightsField | undefined;
    if (field && field in EMPTY_RIGHTS) {
      if (!fields[field]) fields[field] = item.msg;
      continue;
    }
    if (/attribution_required/.test(item.msg)) fields.attribution_text = "Для чужих прав атрибуция обязательна";
    if (/license_incompatible/.test(item.msg)) fields.license = "Лицензия не принимается (SA, NC или ND)";
    if (/rights_expired/.test(item.msg)) fields.valid_until = "Срок прав уже истёк";
    general = item.msg;
  }
  return { fields, general, other };
}
