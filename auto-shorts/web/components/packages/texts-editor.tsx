"use client";

import { CircleCheck, CloudOff, LoaderCircle, PenLine } from "lucide-react";
import type { ReactNode } from "react";

import { Input, Textarea } from "@/components/ui";
import { cn } from "@/lib/cn";
import { formatTime } from "@/lib/format";
import type { UploadPackage } from "@/lib/queries/packages";

import { CopyButton } from "./copy";
import type { PackageDraft, SaveState } from "./use-package-draft";

/** Поля, которые копируются клавишами 1–6 (§7.3): порядок = цифра. */
export type CopyKey = "title" | "description" | "hashtags" | "pinned" | "link" | "code";

export const COPY_LABELS: Record<CopyKey, string> = {
  title: "Заголовок",
  description: "Описание с хэштегами",
  hashtags: "Хэштеги",
  pinned: "Закреплённый комментарий",
  link: "Ссылка из ролика",
  code: "Код ролика",
};

export const COPY_ORDER: CopyKey[] = ["title", "description", "hashtags", "pinned", "link", "code"];

function Counter({ value, max }: { value: string; max: number }) {
  const length = [...value].length;
  return (
    <span className={cn("tabular font-mono text-2xs", length > max ? "text-negative" : "text-fg-muted")}>
      {length}/{max}
    </span>
  );
}

export function SaveIndicator({ state, blocked }: { state: SaveState; blocked: boolean }) {
  if (state.kind === "saving") {
    return (
      <span className="flex items-center gap-1 text-xs text-machine" role="status">
        <LoaderCircle className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />
        Сохраняю…
      </span>
    );
  }
  if (state.kind === "error" || (state.kind === "dirty" && blocked)) {
    return (
      <span className="flex items-center gap-1 text-xs text-negative" role="status">
        <CloudOff className="h-3.5 w-3.5" aria-hidden="true" />
        {state.kind === "error" ? "Не сохранено" : "Не сохранено — исправьте поле"}
      </span>
    );
  }
  if (state.kind === "dirty") {
    return (
      <span className="flex items-center gap-1 text-xs text-fg-muted" role="status">
        <PenLine className="h-3.5 w-3.5" aria-hidden="true" />
        Есть правки
      </span>
    );
  }
  if (state.kind === "saved") {
    return (
      <span className="flex items-center gap-1 text-xs text-positive" role="status">
        <CircleCheck className="h-3.5 w-3.5" aria-hidden="true" />
        Сохранено {formatTime(state.at)}
      </span>
    );
  }
  return <span className="text-xs text-fg-muted">Правки сохраняются сами</span>;
}

/**
 * Тексты пакета: правка на месте с автосохранением и копированием в один клик. Значение для
 * копирования — то, что оператор видит в поле (а не последнее сохранённое).
 */
export function TextsEditor({
  pkg,
  draft,
  errors,
  editable,
  onField,
  onBlur,
  copied,
  onCopy,
}: {
  pkg: UploadPackage;
  draft: PackageDraft;
  errors: Record<string, string>;
  editable: boolean;
  onField: (field: keyof PackageDraft, value: string) => void;
  onBlur: () => void;
  copied: string | null;
  onCopy: (key: CopyKey) => void;
}) {
  const id = (field: string) => `pkg-${pkg.id}-${field}`;
  const hotkey = (key: CopyKey) => String(COPY_ORDER.indexOf(key) + 1);

  function header(key: CopyKey, field: string, extra?: ReactNode) {
    return (
      <div className="flex items-center justify-between gap-2">
        <label htmlFor={id(field)} className="flex items-center gap-1.5 text-xs font-medium text-fg-muted">
          <kbd className="hidden rounded-sm border border-border px-1 font-mono text-2xs md:inline">{hotkey(key)}</kbd>
          {COPY_LABELS[key]}
        </label>
        <span className="flex items-center gap-2">
          {extra}
          <CopyButton
            copied={copied === key}
            onCopy={() => onCopy(key)}
            label={COPY_LABELS[key]}
            hotkey={hotkey(key)}
            testId={`copy-${key}`}
          />
        </span>
      </div>
    );
  }

  function error(field: string) {
    return errors[field] ? (
      <p id={`${id(field)}-error`} role="alert" className="text-xs text-negative">
        {errors[field]}
      </p>
    ) : null;
  }

  return (
    <div className="flex flex-col gap-3" data-testid="package-texts">
      <div className="flex flex-col gap-1">
        {header("title", "title", <Counter value={draft.title.trim()} max={pkg.texts.title_max} />)}
        <Input
          id={id("title")}
          value={draft.title}
          readOnly={!editable}
          onChange={(event) => onField("title", event.target.value)}
          onBlur={onBlur}
          aria-invalid={errors.title ? true : undefined}
          aria-describedby={errors.title ? `${id("title")}-error` : undefined}
          data-testid="package-title"
        />
        {error("title")}
      </div>

      <div className="flex flex-col gap-1">
        {header("description", "description", <Counter value={draft.description} max={pkg.texts.description_max} />)}
        <Textarea
          id={id("description")}
          value={draft.description}
          readOnly={!editable}
          rows={7}
          onChange={(event) => onField("description", event.target.value)}
          onBlur={onBlur}
          aria-invalid={errors.description ? true : undefined}
          aria-describedby={errors.description ? `${id("description")}-error` : undefined}
          data-testid="package-description"
        />
        {error("description")}
        <p className="text-2xs text-fg-muted">
          Кнопка копирует описание и хэштеги в конце — так, как их ждёт площадка.
        </p>
      </div>

      <div className="flex flex-col gap-1">
        {header("hashtags", "hashtags")}
        <Input
          id={id("hashtags")}
          value={draft.hashtags}
          readOnly={!editable}
          placeholder="#таро #расклад"
          onChange={(event) => onField("hashtags", event.target.value)}
          onBlur={onBlur}
          aria-invalid={errors.hashtags ? true : undefined}
          aria-describedby={errors.hashtags ? `${id("hashtags")}-error` : undefined}
          className="font-mono text-xs"
          data-testid="package-hashtags"
        />
        {error("hashtags")}
      </div>

      <div className="flex flex-col gap-1">
        {header("pinned", "pinned")}
        <Textarea
          id={id("pinned")}
          value={draft.pinned}
          readOnly={!editable}
          rows={3}
          placeholder="Пусто — закреп не нужен"
          onChange={(event) => onField("pinned", event.target.value)}
          onBlur={onBlur}
          aria-invalid={errors.pinned_comment ? true : undefined}
          data-testid="package-pinned"
        />
        {error("pinned_comment")}
      </div>
    </div>
  );
}
