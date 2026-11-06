"use client";

import { RotateCcw } from "lucide-react";
import { type ReactNode, useState } from "react";
import { toast } from "sonner";

import { Badge, Button, Input, Select } from "@/components/ui";
import { errorMessage, ValidationError } from "@/lib/api/errors";
import { cn } from "@/lib/cn";
import { formatDateTime } from "@/lib/format";
import { type SettingItem, useUpdateSettings } from "@/lib/queries/settings";

/**
 * Форма одной группы рантайм-настроек (`GET /settings` → `PATCH /settings {key: value}`):
 * показывает текущее значение, дефолт и пометку «переопределено», сохраняет только изменённые
 * ключи, «Сбросить» отправляет `null` — сервер возвращает дефолт. Используют вкладки
 * «Воркер», «Атрибуция», «Уведомления».
 */
export type SettingFieldKind = "number" | "integer" | "text" | "select" | "boolean" | "json";

export interface SettingFieldSpec {
  key: string;
  label: string;
  kind: SettingFieldKind;
  options?: ReadonlyArray<{ value: string; label: string }>;
  /** Единица после поля: «ГБ», «МБ», «дн». */
  unit?: string;
  /** Пустое поле допустимо и означает «не задано» (сброс к дефолту). */
  nullable?: boolean;
  /** Минимум включительно. */
  min?: number;
  /** Строго больше нуля. */
  positive?: boolean;
  placeholder?: string;
  hint?: ReactNode;
  pattern?: RegExp;
  patternHint?: string;
  /** Переопределить описание с сервера. */
  description?: string;
}

const BOOLEAN_OPTIONS = [
  { value: "true", label: "Да" },
  { value: "false", label: "Нет" },
] as const;

export function settingText(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** Спецификация для ключа, которого нет в описании вкладки: по типу значения. */
export function inferSpec(item: SettingItem): SettingFieldSpec {
  const sample = item.value ?? item.default;
  const base = { key: item.key, label: item.key, nullable: item.default === null };
  if (typeof sample === "boolean") return { ...base, kind: "boolean" };
  if (typeof sample === "number") {
    const integer = Number.isInteger(sample) && (item.default === null || Number.isInteger(item.default));
    return { ...base, kind: integer ? "integer" : "number" };
  }
  if (sample !== null && typeof sample === "object") return { ...base, kind: "json" };
  return { ...base, kind: "text" };
}

function parseField(spec: SettingFieldSpec, text: string): { value: unknown; error: string | null } {
  const trimmed = text.trim();
  if (trimmed === "") {
    return spec.nullable ? { value: null, error: null } : { value: null, error: "Обязательное поле" };
  }
  switch (spec.kind) {
    case "number":
    case "integer": {
      const parsed = Number(trimmed.replace(",", "."));
      if (!Number.isFinite(parsed)) return { value: null, error: "Нужно число" };
      if (spec.kind === "integer" && !Number.isInteger(parsed)) return { value: null, error: "Нужно целое число" };
      if (spec.positive && parsed <= 0) return { value: null, error: "Число больше нуля" };
      if (spec.min !== undefined && parsed < spec.min) return { value: null, error: `Не меньше ${spec.min}` };
      return { value: parsed, error: null };
    }
    case "boolean":
      return { value: trimmed === "true", error: null };
    case "select":
      if (!spec.options?.some((option) => option.value === trimmed))
        return { value: null, error: "Выберите значение из списка" };
      return { value: trimmed, error: null };
    case "json":
      try {
        return { value: JSON.parse(trimmed) as unknown, error: null };
      } catch {
        return { value: null, error: "Некорректный JSON" };
      }
    default:
      if (spec.pattern && !spec.pattern.test(trimmed))
        return { value: null, error: spec.patternHint ?? "Недопустимое значение" };
      return { value: trimmed, error: null };
  }
}

function defaultLabel(spec: SettingFieldSpec, item: SettingItem): string {
  if (item.default === null || item.default === undefined) return "пусто";
  const text = settingText(item.default);
  if (spec.kind === "select") return spec.options?.find((option) => option.value === text)?.label ?? text;
  if (spec.kind === "boolean") return item.default ? "да" : "нет";
  return spec.unit ? `${text} ${spec.unit}` : text;
}

export function SettingsGroupForm({
  idPrefix,
  items,
  fields,
  admin,
  includeUnknown = true,
  successMessage = "Настройки сохранены",
  footer,
}: {
  idPrefix: string;
  /** Записи одной группы (`item.group`). */
  items: SettingItem[];
  fields: ReadonlyArray<SettingFieldSpec>;
  admin: boolean;
  /** Показывать ключи группы, не описанные в `fields` (по типу значения). */
  includeUnknown?: boolean;
  successMessage?: string;
  footer?: ReactNode;
}) {
  const update = useUpdateSettings();
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [serverErrors, setServerErrors] = useState<Record<string, string>>({});
  const [resetting, setResetting] = useState<string | null>(null);

  const byKey = new Map(items.map((item) => [item.key, item]));
  const known = new Set(fields.map((field) => field.key));
  const rows: Array<{ spec: SettingFieldSpec; item: SettingItem }> = [];
  for (const spec of fields) {
    const item = byKey.get(spec.key);
    if (item) rows.push({ spec, item });
  }
  if (includeUnknown) for (const item of items) if (!known.has(item.key)) rows.push({ spec: inferSpec(item), item });

  const parsed = rows.map(({ spec, item }) => {
    const current = settingText(item.value);
    const text = draft[item.key] ?? current;
    const dirty = draft[item.key] !== undefined && draft[item.key] !== current;
    const result = dirty ? parseField(spec, text) : { value: item.value, error: null };
    return { spec, item, text, dirty, ...result, serverError: serverErrors[item.key] };
  });
  const dirtyRows = parsed.filter((row) => row.dirty);
  const hasErrors = dirtyRows.some((row) => row.error);

  const save = async () => {
    const body: Record<string, unknown> = {};
    for (const row of dirtyRows) body[row.item.key] = row.value;
    try {
      await update.mutateAsync(body);
      setDraft({});
      setServerErrors({});
      toast.success(successMessage);
    } catch (error) {
      if (error instanceof ValidationError && Object.keys(error.fieldErrors).length) {
        setServerErrors(error.fieldErrors);
        toast.error("Не сохранено", { description: "Проверьте подсвеченные поля." });
        return;
      }
      toast.error("Не сохранено", { description: errorMessage(error) });
    }
  };

  const reset = async (spec: SettingFieldSpec) => {
    setResetting(spec.key);
    try {
      await update.mutateAsync({ [spec.key]: null });
      setDraft((prev) => {
        const next = { ...prev };
        delete next[spec.key];
        return next;
      });
      setServerErrors((prev) => {
        const next = { ...prev };
        delete next[spec.key];
        return next;
      });
      toast.success(`${spec.label}: значение по умолчанию`);
    } catch (error) {
      toast.error("Не сброшено", { description: errorMessage(error) });
    } finally {
      setResetting(null);
    }
  };

  if (rows.length === 0) return <p className="text-sm text-fg-muted">В этой группе пока нет настроек.</p>;

  return (
    <div className="flex flex-col">
      {parsed.map(({ spec, item, text, dirty, error, serverError }) => {
        const id = `${idPrefix}-${spec.key}`;
        const message = error ?? serverError;
        const disabled = !admin || update.isPending;
        const fieldClass = cn("max-w-xs", spec.kind !== "select" && spec.kind !== "boolean" && "font-mono");
        return (
          <div
            key={spec.key}
            className="grid gap-x-4 gap-y-1 border-b border-border py-2.5 last:border-b-0 sm:grid-cols-[minmax(0,260px)_minmax(0,1fr)]"
            data-setting={spec.key}
          >
            <div className="flex min-w-0 flex-col">
              <label htmlFor={id} className="text-sm font-medium">
                {spec.label}
              </label>
              <span className="text-2xs text-fg-muted">{spec.description ?? item.description}</span>
              {spec.label !== spec.key ? <span className="font-mono text-2xs text-fg-muted/70">{spec.key}</span> : null}
            </div>
            <div className="flex min-w-0 flex-col gap-1">
              <div className="flex flex-wrap items-center gap-2">
                {spec.kind === "select" || spec.kind === "boolean" ? (
                  <Select
                    id={id}
                    className={fieldClass}
                    value={text}
                    disabled={disabled}
                    aria-invalid={!!message}
                    onChange={(event) => setDraft((prev) => ({ ...prev, [spec.key]: event.target.value }))}
                  >
                    {spec.nullable ? <option value="">— не задано —</option> : null}
                    {(spec.kind === "boolean" ? BOOLEAN_OPTIONS : (spec.options ?? [])).map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </Select>
                ) : (
                  <Input
                    id={id}
                    className={fieldClass}
                    inputMode={spec.kind === "integer" ? "numeric" : spec.kind === "number" ? "decimal" : undefined}
                    value={text}
                    placeholder={spec.placeholder}
                    disabled={disabled}
                    aria-invalid={!!message}
                    aria-describedby={`${id}-default`}
                    spellCheck={false}
                    onChange={(event) => setDraft((prev) => ({ ...prev, [spec.key]: event.target.value }))}
                  />
                )}
                {spec.unit ? <span className="text-xs text-fg-muted">{spec.unit}</span> : null}
                {dirty ? (
                  <Badge variant="warning" outline>
                    не сохранено
                  </Badge>
                ) : item.is_overridden ? (
                  <Badge variant="accent" outline>
                    переопределено
                  </Badge>
                ) : null}
                {admin && item.is_overridden ? (
                  <Button
                    variant="ghost"
                    size="xs"
                    disabled={update.isPending}
                    onClick={() => void reset(spec)}
                    aria-label={`Сбросить ${spec.label} к значению по умолчанию`}
                  >
                    <RotateCcw className={cn("h-3 w-3", resetting === spec.key && "animate-spin")} aria-hidden="true" />
                    Сбросить
                  </Button>
                ) : null}
              </div>
              <p id={`${id}-default`} className="text-2xs text-fg-muted">
                По умолчанию: <span className="font-mono">{defaultLabel(spec, item)}</span>
                {item.is_overridden && item.updated_at
                  ? ` · изменено ${formatDateTime(item.updated_at, { short: true })}`
                  : ""}
                {spec.hint ? <> · {spec.hint}</> : null}
              </p>
              {message ? (
                <p className="text-2xs text-negative" role="alert">
                  {message}
                </p>
              ) : null}
            </div>
          </div>
        );
      })}
      <div className="flex flex-wrap items-center gap-2 pt-3">
        {admin ? (
          <>
            <Button
              size="sm"
              disabled={dirtyRows.length === 0 || hasErrors || update.isPending}
              onClick={() => void save()}
            >
              Сохранить{dirtyRows.length > 1 ? ` (${dirtyRows.length})` : ""}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={dirtyRows.length === 0 || update.isPending}
              onClick={() => {
                setDraft({});
                setServerErrors({});
              }}
            >
              Отменить
            </Button>
          </>
        ) : (
          <p className="text-2xs text-fg-muted">Меняет владелец.</p>
        )}
        {footer}
      </div>
    </div>
  );
}
