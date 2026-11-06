"use client";

import { LoaderCircle, Plus, Sparkles, Trash } from "lucide-react";
import { type ReactNode, useId } from "react";

import { Badge, Button, Callout, Input, Select, StatusBadge, Textarea } from "@/components/ui";
import type { Schemas } from "@/lib/api/client";
import { cn } from "@/lib/cn";

import { AUTHOR_LIMITS, type AuthorDraft, draftLimitErrors, type TranscriptSentence } from "./clip-math";
import { AUTHOR_MODE_META, CTA_STYLE_LABELS, INSERT_MODE_LABELS } from "./labels";

type AuthorScriptIssue = Schemas["AuthorScriptIssue"];
type AuthorScriptMode = Schemas["AuthorScriptMode"];

const WHERE_LABELS: Record<string, string> = {
  cold_open: "хук",
  insert_0: "вставка 1",
  insert_1: "вставка 2",
  outro: "вывод",
  title: "заголовок",
  script: "сценарий",
};

function Counter({ value, max }: { value: string; max: number }) {
  const len = value.trim().length;
  return (
    <span className={cn("font-mono text-[10px] tabular", len > max ? "text-negative" : "text-fg-muted")}>
      {len}/{max}
    </span>
  );
}

function Field({
  label,
  htmlFor,
  value,
  max,
  error,
  children,
}: {
  label: string;
  htmlFor: string;
  value: string;
  max: number;
  error?: string;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between gap-2">
        <label htmlFor={htmlFor} className="text-xs font-medium text-fg-muted">
          {label}
        </label>
        <Counter value={value} max={max} />
      </div>
      {children}
      {error ? (
        <p role="alert" className="text-2xs text-negative">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export interface AuthorSourceInfo {
  mode: AuthorScriptMode | null;
  label: string | null;
  needsOperatorEdit: boolean;
  llmConfigured: boolean | null;
}

/**
 * Редактор авторского слоя (§6.3.8, OWNER-DECISIONS п. 5): хук голосом (cold open без заставки),
 * до двух вставок-комментариев, вывод, свой заголовок. Лимиты — константы движка. «Черновик модели»
 * пишет LLM (Umka через OpenAI-совместимый API); без ключа — шаблон с пометкой, его нужно переписать.
 */
export function AuthorLayerEditor({
  value,
  onChange,
  sentences,
  disabled,
  info,
  issues,
  onDraft,
  drafting,
  draftDisabledReason,
  saveState,
}: {
  value: AuthorDraft;
  onChange: (next: AuthorDraft) => void;
  /** Предложения клипа: вставка ставится после одного из них (кроме последнего). */
  sentences: readonly TranscriptSentence[];
  disabled?: boolean;
  info: AuthorSourceInfo | null;
  issues: readonly AuthorScriptIssue[];
  onDraft: (() => void) | null;
  drafting: boolean;
  draftDisabledReason?: string | null;
  saveState?: "saved" | "dirty" | "saving" | "error" | null;
}) {
  const id = useId();
  const limitErrors = draftLimitErrors(value);
  const insertOptions = sentences.slice(0, -1);
  const set = (patch: Partial<AuthorDraft>) => onChange({ ...value, ...patch });
  const templated = !!info && (info.needsOperatorEdit || info.mode === "heuristic" || info.mode === "mock");

  const addInsert = () => {
    if (value.inserts.length >= AUTHOR_LIMITS.inserts || !insertOptions.length) return;
    const used = new Set(value.inserts.map((ins) => ins.after_sid));
    const middle = insertOptions[Math.floor(insertOptions.length / 2)];
    const pick = used.has(middle.sid) ? insertOptions.find((s) => !used.has(s.sid)) : middle;
    if (!pick) return;
    set({
      inserts: [
        ...value.inserts,
        { after_sid: pick.sid, mode: "freeze_zoom" as const, vo_text: "", on_screen: "" },
      ].sort((a, b) => a.after_sid - b.after_sid),
    });
  };

  return (
    <div className="flex flex-col gap-3" data-testid="author-layer-editor">
      <div className="flex flex-wrap items-center gap-2">
        {info?.mode ? (
          <StatusBadge status={info.mode} map={AUTHOR_MODE_META} />
        ) : (
          <Badge variant="neutral">свой текст</Badge>
        )}
        {info?.label ? (
          <span className="text-xs text-fg-muted" data-testid="author-label">
            {info.label}
          </span>
        ) : null}
        {saveState ? (
          <span
            className={cn(
              "ml-auto font-mono text-2xs",
              saveState === "error" ? "text-negative" : saveState === "saved" ? "text-positive" : "text-fg-muted",
            )}
            role="status"
          >
            {saveState === "saving"
              ? "сохраняем…"
              : saveState === "dirty"
                ? "не сохранено"
                : saveState === "error"
                  ? "не сохранилось"
                  : "сохранено"}
          </span>
        ) : null}
      </div>
      {onDraft ? (
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={onDraft}
            disabled={disabled || drafting || !!draftDisabledReason}
            data-testid="author-draft"
            title={draftDisabledReason ?? "Черновик авторского текста от модели"}
          >
            {drafting ? (
              <LoaderCircle className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            ) : (
              <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
            )}
            {drafting ? "Модель пишет…" : "Черновик модели"}
          </Button>
          {info?.llmConfigured === false ? (
            <span className="text-2xs text-warning">
              Ключ LLM не задан — будет шаблон, его нужно переписать своими словами
            </span>
          ) : draftDisabledReason ? (
            <span className="text-2xs text-fg-muted">{draftDisabledReason}</span>
          ) : null}
        </div>
      ) : null}
      {templated ? (
        <Callout variant="warning" title="Шаблон без LLM — перепишите своими словами">
          Это заготовка, а не авторский текст. Пока хук и вставки почти дословно из шаблона, текст остаётся шаблоном и
          после сохранения. Для чужих прав отчёт об оригинальности его не пропустит; свой голос и своя мысль — то, за
          что YouTube рекомендует нарезки.
        </Callout>
      ) : null}

      <fieldset className="flex flex-col gap-3" disabled={disabled}>
        <legend className="sr-only">Авторский слой</legend>
        <Field
          label="Свой заголовок на экране"
          htmlFor={`${id}-title`}
          value={value.on_screen_title}
          max={AUTHOR_LIMITS.title}
          error={limitErrors.on_screen_title}
        >
          <Input
            id={`${id}-title`}
            value={value.on_screen_title}
            onChange={(event) => set({ on_screen_title: event.target.value })}
            placeholder="Почему Таро не предсказывает, а подсказывает"
            data-testid="author-title"
          />
        </Field>

        <div className="flex flex-col gap-2 rounded-md border border-border p-2.5">
          <span className="text-xs font-semibold text-fg">Хук голосом (cold open, без заставки)</span>
          <Field
            label="Реплика"
            htmlFor={`${id}-co`}
            value={value.cold_open.vo_text}
            max={AUTHOR_LIMITS.coldOpen}
            error={limitErrors["cold_open.vo_text"]}
          >
            <Textarea
              id={`${id}-co`}
              rows={2}
              value={value.cold_open.vo_text}
              onChange={(event) => set({ cold_open: { ...value.cold_open, vo_text: event.target.value } })}
              placeholder="Первые 2 секунды решают всё: зачем смотреть дальше — своими словами"
              data-testid="author-cold-open"
            />
          </Field>
          <Field
            label="Тезис на экране"
            htmlFor={`${id}-co-os`}
            value={value.cold_open.on_screen}
            max={AUTHOR_LIMITS.onScreen}
            error={limitErrors["cold_open.on_screen"]}
          >
            <Input
              id={`${id}-co-os`}
              value={value.cold_open.on_screen}
              onChange={(event) => set({ cold_open: { ...value.cold_open, on_screen: event.target.value } })}
            />
          </Field>
          <label className="flex items-center gap-2 text-xs text-fg">
            <input
              type="checkbox"
              checked={value.cold_open.duck_source}
              onChange={(event) => set({ cold_open: { ...value.cold_open, duck_source: event.target.checked } })}
            />
            Приглушить исходник под голосом
          </label>
        </div>

        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-semibold text-fg">Вставки-комментарии</span>
            <Button
              variant="ghost"
              size="xs"
              onClick={addInsert}
              disabled={disabled || value.inserts.length >= AUTHOR_LIMITS.inserts || insertOptions.length === 0}
              data-testid="author-add-insert"
            >
              <Plus className="h-3 w-3" aria-hidden="true" />
              Вставка ({value.inserts.length}/{AUTHOR_LIMITS.inserts})
            </Button>
          </div>
          {limitErrors.inserts ? (
            <p role="alert" className="text-2xs text-negative">
              {limitErrors.inserts}
            </p>
          ) : null}
          {value.inserts.length === 0 ? (
            <p className="text-2xs text-fg-muted">
              Вставка — ваш комментарий посреди клипа: стоп-кадр с наездом или голос поверх приглушённого звука.
            </p>
          ) : null}
          {value.inserts.map((insert, index) => {
            const update = (patch: Partial<AuthorDraft["inserts"][number]>) =>
              set({ inserts: value.inserts.map((item, i) => (i === index ? { ...item, ...patch } : item)) });
            const known = insertOptions.some((s) => s.sid === insert.after_sid);
            return (
              <div
                key={index}
                className="flex flex-col gap-2 rounded-md border border-border p-2.5"
                data-testid={`author-insert-${index}`}
              >
                <div className="flex items-center gap-2">
                  <span className="text-xs font-medium text-fg">Вставка {index + 1}</span>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    className="ml-auto"
                    aria-label={`Убрать вставку ${index + 1}`}
                    onClick={() => set({ inserts: value.inserts.filter((_, i) => i !== index) })}
                  >
                    <Trash className="h-3.5 w-3.5" aria-hidden="true" />
                  </Button>
                </div>
                <div className="flex flex-col gap-2">
                  <Select
                    aria-label="После предложения"
                    value={String(insert.after_sid)}
                    onChange={(event) => update({ after_sid: Number(event.target.value) })}
                  >
                    {!known ? (
                      <option value={insert.after_sid}>предложение {insert.after_sid} (вне клипа)</option>
                    ) : null}
                    {insertOptions.map((sentence) => (
                      <option key={sentence.sid} value={sentence.sid}>
                        после: {sentence.text.length > 60 ? `${sentence.text.slice(0, 60)}…` : sentence.text}
                      </option>
                    ))}
                  </Select>
                  <Select
                    aria-label="Как вставить"
                    value={insert.mode}
                    onChange={(event) => update({ mode: event.target.value as AuthorDraft["inserts"][number]["mode"] })}
                  >
                    {(Object.keys(INSERT_MODE_LABELS) as Array<keyof typeof INSERT_MODE_LABELS>).map((mode) => (
                      <option key={mode} value={mode}>
                        {INSERT_MODE_LABELS[mode]}
                      </option>
                    ))}
                  </Select>
                </div>
                <Field
                  label="Комментарий"
                  htmlFor={`${id}-ins-${index}`}
                  value={insert.vo_text}
                  max={AUTHOR_LIMITS.insert}
                  error={limitErrors[`inserts.${index}.vo_text`]}
                >
                  <Textarea
                    id={`${id}-ins-${index}`}
                    rows={2}
                    value={insert.vo_text}
                    onChange={(event) => update({ vo_text: event.target.value })}
                  />
                </Field>
                <Field
                  label="На экране"
                  htmlFor={`${id}-ins-os-${index}`}
                  value={insert.on_screen}
                  max={AUTHOR_LIMITS.onScreen}
                  error={limitErrors[`inserts.${index}.on_screen`]}
                >
                  <Input
                    id={`${id}-ins-os-${index}`}
                    value={insert.on_screen}
                    onChange={(event) => update({ on_screen: event.target.value })}
                  />
                </Field>
              </div>
            );
          })}
        </div>

        <div className="flex flex-col gap-2 rounded-md border border-border p-2.5">
          <span className="text-xs font-semibold text-fg">Вывод (outro)</span>
          <Field
            label="Реплика"
            htmlFor={`${id}-outro`}
            value={value.outro.vo_text}
            max={AUTHOR_LIMITS.outro}
            error={limitErrors["outro.vo_text"]}
          >
            <Textarea
              id={`${id}-outro`}
              rows={2}
              value={value.outro.vo_text}
              onChange={(event) => set({ outro: { ...value.outro, vo_text: event.target.value } })}
              placeholder="Вывод или вопрос зрителю — своими словами"
              data-testid="author-outro"
            />
          </Field>
          <Select
            aria-label="Стиль концовки"
            value={value.outro.cta_style}
            onChange={(event) =>
              set({ outro: { ...value.outro, cta_style: event.target.value as AuthorDraft["outro"]["cta_style"] } })
            }
          >
            {(Object.keys(CTA_STYLE_LABELS) as Array<keyof typeof CTA_STYLE_LABELS>).map((style) => (
              <option key={style} value={style}>
                {CTA_STYLE_LABELS[style]}
              </option>
            ))}
          </Select>
        </div>
      </fieldset>

      {issues.length ? (
        <ul className="flex flex-col gap-1" aria-label="Замечания к тексту" data-testid="author-issues">
          {issues.map((issue, index) => (
            <li
              key={`${issue.code}-${index}`}
              className={cn(
                "rounded-sm px-2 py-1 text-2xs",
                issue.severity === "error"
                  ? "bg-negative/10 text-negative"
                  : issue.severity === "warn"
                    ? "bg-warning/10 text-warning"
                    : "bg-surface-2 text-fg-muted",
              )}
            >
              <span className="font-medium">{WHERE_LABELS[issue.where] ?? issue.where}:</span> {issue.detail}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
