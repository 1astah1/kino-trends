"use client";

import { FileCheck, Paperclip, X } from "lucide-react";
import { useEffect, useId } from "react";

import { Button, Callout, FormField, Input, Select, Textarea } from "@/components/ui";
import { cn } from "@/lib/cn";

import { formatBytes } from "./clip-math";
import { ALLOWLIST_LABELS, RIGHTS_TYPE_HINTS, RIGHTS_TYPE_LABELS, RIGHTS_TYPES } from "./labels";
import { type RightsField, type RightsFormValues, rightsHints } from "./rights-rules";
import { uploadPct, useTusUpload } from "./use-tus-upload";

type FieldErrors = Partial<Record<RightsField, string>>;

/**
 * Обязательная форма прав (§7.3 «диалог Добавить … + обязательная форма прав»). Правила — зеркало
 * движка (`rights-rules.ts`), ошибки по-русски под полями; сервер проверяет то же и остаётся главным.
 */
export function RightsForm({
  values,
  onChange,
  errors,
  generalError,
  disabled,
  idPrefix = "rights",
  allowProof = true,
}: {
  values: RightsFormValues;
  onChange: (next: RightsFormValues) => void;
  errors: FieldErrors;
  generalError?: string | null;
  disabled?: boolean;
  idPrefix?: string;
  allowProof?: boolean;
}) {
  const groupId = useId();
  const set = <K extends keyof RightsFormValues>(key: K, value: RightsFormValues[K]) =>
    onChange({ ...values, [key]: value });
  const type = values.rights_type;
  const foreign = !!type && type !== "own";
  const licensed = type === "licensed_written" || type === "licensed_platform";
  const hints = rightsHints(values);
  const fid = (name: string) => `${idPrefix}-${name}`;

  return (
    <fieldset className="flex flex-col gap-3" disabled={disabled} data-testid="rights-form">
      <legend className="sr-only">Права на источник</legend>
      <div className="flex flex-col gap-1.5">
        <span id={groupId} className="text-xs font-medium text-fg-muted">
          Основание прав <span className="text-negative">*</span>
        </span>
        <div role="radiogroup" aria-labelledby={groupId} className="grid gap-1.5 sm:grid-cols-2">
          {RIGHTS_TYPES.map((option) => {
            const checked = type === option;
            return (
              <label
                key={option}
                className={cn(
                  "flex cursor-pointer flex-col gap-0.5 rounded-md border px-2.5 py-2 text-sm transition-colors duration-150",
                  checked ? "border-accent bg-accent/10" : "border-border hover:border-fg-muted/50",
                )}
              >
                <span className="flex items-center gap-2">
                  <input
                    type="radio"
                    name={`${idPrefix}-type`}
                    value={option}
                    checked={checked}
                    onChange={() => set("rights_type", option)}
                    className="accent-[var(--accent)]"
                  />
                  <span className="font-medium text-fg">{RIGHTS_TYPE_LABELS[option]}</span>
                </span>
                <span className="text-xs text-fg-muted">{RIGHTS_TYPE_HINTS[option]}</span>
              </label>
            );
          })}
        </div>
        {errors.rights_type ? (
          <p role="alert" className="text-xs text-negative">
            {errors.rights_type}
          </p>
        ) : null}
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <FormField label="Правообладатель *" htmlFor={fid("owner")} error={errors.owner_name}>
          <Input
            id={fid("owner")}
            value={values.owner_name}
            maxLength={300}
            onChange={(event) => set("owner_name", event.target.value)}
            placeholder={type === "own" ? "Вы или ваша компания" : "Автор или студия"}
            aria-invalid={!!errors.owner_name}
          />
        </FormField>
        <FormField label="Канал правообладателя" htmlFor={fid("channel")} error={errors.owner_channel_url}>
          <Input
            id={fid("channel")}
            value={values.owner_channel_url}
            maxLength={2000}
            inputMode="url"
            onChange={(event) => set("owner_channel_url", event.target.value)}
            placeholder="youtube.com/@канал"
            aria-invalid={!!errors.owner_channel_url}
          />
        </FormField>
      </div>

      <FormField
        label={foreign ? "Атрибуция *" : "Атрибуция"}
        htmlFor={fid("attribution")}
        error={errors.attribution_text}
        hint={
          foreign
            ? "Попадёт в описание ролика: автор, название, источник, лицензия, что изменено."
            : "Для своего видео не обязательна."
        }
      >
        <Textarea
          id={fid("attribution")}
          value={values.attribution_text}
          maxLength={1000}
          rows={2}
          onChange={(event) => set("attribution_text", event.target.value)}
          placeholder="Иван Петров — «Доклад на CG Event 2024», youtube.com/…, CC BY 4.0. Изменения: нарезка и авторский комментарий"
          aria-invalid={!!errors.attribution_text}
        />
      </FormField>

      {type && type !== "own" ? (
        <div className="grid gap-3 sm:grid-cols-2">
          <FormField label="Лицензия" htmlFor={fid("license")} error={errors.license}>
            <Input
              id={fid("license")}
              value={values.license}
              maxLength={200}
              onChange={(event) => set("license", event.target.value)}
              placeholder={type === "cc_by" ? "CC BY 4.0" : "Договор № 12 от 01.09.2026"}
              aria-invalid={!!errors.license}
            />
          </FormField>
          <FormField label="Ссылка на лицензию" htmlFor={fid("license-url")} error={errors.license_url}>
            <Input
              id={fid("license-url")}
              value={values.license_url}
              maxLength={2000}
              inputMode="url"
              onChange={(event) => set("license_url", event.target.value)}
              placeholder="creativecommons.org/licenses/by/4.0"
              aria-invalid={!!errors.license_url}
            />
          </FormField>
        </div>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2">
        <FormField
          label="Права действуют до"
          htmlFor={fid("valid")}
          error={errors.valid_until}
          hint="Пусто — бессрочно"
        >
          <Input
            id={fid("valid")}
            type="date"
            value={values.valid_until}
            onChange={(event) => set("valid_until", event.target.value)}
            aria-invalid={!!errors.valid_until}
          />
        </FormField>
        {licensed ? (
          <FormField label="Content ID allowlist" htmlFor={fid("allowlist")}>
            <Select
              id={fid("allowlist")}
              value={values.allowlist_status}
              onChange={(event) => set("allowlist_status", event.target.value as RightsFormValues["allowlist_status"])}
            >
              <option value="">Неизвестно</option>
              <option value="requested">{ALLOWLIST_LABELS.requested}</option>
              <option value="allowlisted">{ALLOWLIST_LABELS.allowlisted}</option>
            </Select>
          </FormField>
        ) : null}
      </div>

      <FormField
        label="Чем подтверждено *"
        htmlFor={fid("proof")}
        error={errors.proof_note}
        hint="Договор, письмо, страница с лицензией и дата проверки"
      >
        <Textarea
          id={fid("proof")}
          value={values.proof_note}
          maxLength={4000}
          rows={2}
          onChange={(event) => set("proof_note", event.target.value)}
          placeholder={
            type === "own"
              ? "Запись моего эфира 12.09.2026"
              : "Лицензия CC BY 4.0 указана под видео, проверено 27.09.2026"
          }
          aria-invalid={!!errors.proof_note}
        />
      </FormField>

      {allowProof && foreign ? (
        <ProofUpload
          value={values.proof_upload_id}
          onChange={(uploadId) => set("proof_upload_id", uploadId)}
          disabled={disabled}
          inputId={fid("proof-file")}
        />
      ) : null}

      {generalError ? (
        <Callout variant="negative" title="Права не приняты">
          {generalError}
        </Callout>
      ) : null}
      {hints.length ? (
        <ul className="flex flex-col gap-1 text-xs text-fg-muted" data-testid="rights-hints">
          {hints.map((hint) => (
            <li key={hint} className="flex gap-1.5">
              <span aria-hidden="true" className="text-warning">
                •
              </span>
              {hint}
            </li>
          ))}
        </ul>
      ) : null}
    </fieldset>
  );
}

/** Скан договора или скриншот лицензии: tus-загрузка с `purpose=rights_doc` → `proof_upload_id`. */
function ProofUpload({
  value,
  onChange,
  disabled,
  inputId,
}: {
  value: string | null;
  onChange: (uploadId: string | null) => void;
  disabled?: boolean;
  inputId: string;
}) {
  const upload = useTusUpload("rights_doc");
  const { state } = upload;
  const done = state.phase === "complete" && !!state.uploadId;
  useEffect(() => {
    if (state.phase === "complete" && state.uploadId && value !== state.uploadId) onChange(state.uploadId);
  }, [state.phase, state.uploadId, value, onChange]);

  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs font-medium text-fg-muted">Документ (необязательно)</span>
      {value && done ? (
        <div className="flex items-center gap-2 text-sm">
          <FileCheck className="h-4 w-4 text-positive" aria-hidden="true" />
          <span className="text-fg">Документ загружен</span>
          <span className="font-mono text-2xs text-fg-muted">{formatBytes(state.total)}</span>
          <Button
            variant="ghost"
            size="xs"
            onClick={() => {
              onChange(null);
              void upload.cancel();
            }}
            disabled={disabled}
          >
            <X className="h-3 w-3" aria-hidden="true" />
            Убрать
          </Button>
        </div>
      ) : (
        <label
          htmlFor={inputId}
          className="flex w-fit cursor-pointer items-center gap-2 rounded-md border border-dashed border-border px-2.5 py-1.5 text-xs text-fg-muted hover:border-fg-muted/60"
        >
          <Paperclip className="h-3.5 w-3.5" aria-hidden="true" />
          {state.phase === "uploading" || state.phase === "starting"
            ? `Загрузка… ${uploadPct(state)}%`
            : "PDF, JPG или PNG: договор, письмо, скриншот лицензии"}
          <input
            id={inputId}
            type="file"
            accept=".pdf,.jpg,.jpeg,.png,.webp,application/pdf,image/*"
            className="sr-only"
            disabled={disabled}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void upload.start(file);
              event.target.value = "";
            }}
          />
        </label>
      )}
      {state.error ? (
        <p role="alert" className="text-xs text-negative">
          {state.error}
        </p>
      ) : null}
    </div>
  );
}
