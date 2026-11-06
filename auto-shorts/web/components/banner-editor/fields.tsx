"use client";

import { type ReactNode, useId, useState } from "react";

import { cn } from "@/lib/cn";

const inputClass =
  "h-7 w-full min-w-0 rounded-md border border-border bg-bg px-2 text-xs text-fg tabular font-mono transition-colors duration-150 hover:border-fg-muted/50 focus-visible:border-accent disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-negative";

/**
 * Числовое поле инспектора: правка применяется сразу при корректном числе, пустое или кривое
 * значение (вне min–max, дробное при `integer`) не уходит в черновик — рамка ошибки до потери фокуса.
 */
export function NumberField({
  label,
  value,
  onChange,
  min,
  max,
  step = 1,
  integer = false,
  suffix,
  disabled,
  className,
  testId,
  hint,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  step?: number;
  /** Поле схемы — целое (пиксели, мс, кегль): дробное значение не принимается. */
  integer?: boolean;
  suffix?: string;
  disabled?: boolean;
  className?: string;
  testId?: string;
  hint?: string;
}) {
  const id = useId();
  // Пока поле в фокусе, показываем набранный текст (в том числе незаконченный «1,»), иначе — значение.
  const [typed, setTyped] = useState<string | null>(null);
  const text = typed ?? String(value);
  const setText = setTyped;
  const parsed = Number(text.replace(",", "."));
  const outOfRange = (n: number) =>
    (min !== undefined && n < min) || (max !== undefined && n > max) || (integer && !Number.isInteger(n));
  const invalid = text.trim() === "" || !Number.isFinite(parsed) || outOfRange(parsed);
  return (
    <label htmlFor={id} className={cn("flex min-w-0 flex-col gap-0.5", className)} title={hint}>
      <span className="text-2xs text-fg-muted">{label}</span>
      <span className="relative flex items-center">
        <input
          id={id}
          type="text"
          inputMode="decimal"
          className={cn(inputClass, suffix && "pr-6")}
          value={text}
          disabled={disabled}
          aria-invalid={invalid || undefined}
          data-testid={testId}
          onBlur={() => setTyped(null)}
          onChange={(event) => {
            const raw = event.target.value;
            setText(raw);
            const next = Number(raw.replace(",", "."));
            if (raw.trim() === "" || !Number.isFinite(next) || outOfRange(next)) return;
            onChange(next);
          }}
          onKeyDown={(event) => {
            if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
            event.preventDefault();
            const delta = (event.key === "ArrowUp" ? 1 : -1) * step * (event.shiftKey ? 10 : 1);
            let next = Math.round((value + delta) * 1000) / 1000;
            if (integer) next = Math.round(next);
            if (min !== undefined) next = Math.max(min, next);
            if (max !== undefined) next = Math.min(max, next);
            setTyped(null);
            onChange(next);
          }}
        />
        {suffix ? <span className="pointer-events-none absolute right-2 text-2xs text-fg-muted">{suffix}</span> : null}
      </span>
    </label>
  );
}

export function TextField({
  label,
  value,
  onChange,
  placeholder,
  disabled,
  mono,
  invalid,
  hint,
  testId,
  maxLength,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  disabled?: boolean;
  mono?: boolean;
  invalid?: boolean;
  hint?: ReactNode;
  testId?: string;
  maxLength?: number;
}) {
  const id = useId();
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <label htmlFor={id} className="text-2xs text-fg-muted">
        {label}
      </label>
      <input
        id={id}
        type="text"
        className={cn(inputClass, !mono && "font-sans")}
        value={value}
        placeholder={placeholder}
        disabled={disabled}
        maxLength={maxLength}
        aria-invalid={invalid || undefined}
        data-testid={testId}
        spellCheck={false}
        onChange={(event) => onChange(event.target.value)}
      />
      {hint ? <span className={cn("text-2xs", invalid ? "text-negative" : "text-fg-muted")}>{hint}</span> : null}
    </div>
  );
}

export function SelectField<T extends string>({
  label,
  value,
  options,
  onChange,
  disabled,
  testId,
}: {
  label: string;
  value: T;
  options: ReadonlyArray<{ value: T; label: string }>;
  onChange: (value: T) => void;
  disabled?: boolean;
  testId?: string;
}) {
  const id = useId();
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <label htmlFor={id} className="text-2xs text-fg-muted">
        {label}
      </label>
      <select
        id={id}
        className={cn(inputClass, "pr-6 font-sans")}
        value={value}
        disabled={disabled}
        data-testid={testId}
        onChange={(event) => onChange(event.target.value as T)}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </div>
  );
}

export function CheckField({
  label,
  checked,
  onChange,
  disabled,
  hint,
  testId,
}: {
  label: ReactNode;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  hint?: ReactNode;
  testId?: string;
}) {
  return (
    <label className={cn("flex items-start gap-2 text-xs", disabled && "opacity-60")}>
      <input
        type="checkbox"
        className="mt-0.5 h-3.5 w-3.5 shrink-0 accent-[var(--accent)]"
        checked={checked}
        disabled={disabled}
        data-testid={testId}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span className="flex flex-col">
        <span className="text-fg">{label}</span>
        {hint ? <span className="text-2xs text-fg-muted">{hint}</span> : null}
      </span>
    </label>
  );
}

export function RangeField({
  label,
  value,
  onChange,
  min,
  max,
  step,
  format,
  disabled,
  testId,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  min: number;
  max: number;
  step: number;
  format?: (value: number) => string;
  disabled?: boolean;
  testId?: string;
}) {
  const id = useId();
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <div className="flex items-center justify-between gap-2">
        <label htmlFor={id} className="text-2xs text-fg-muted">
          {label}
        </label>
        <span className="tabular font-mono text-2xs text-fg">{format ? format(value) : value}</span>
      </div>
      <input
        id={id}
        type="range"
        className="h-4 w-full accent-[var(--accent)]"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        data-testid={testId}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </div>
  );
}

export function ColorField({
  label,
  value,
  onChange,
  disabled,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  const id = useId();
  const safe = /^#[0-9a-fA-F]{6}$/.test(value) ? value : "#000000";
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <label htmlFor={id} className="text-2xs text-fg-muted">
        {label}
      </label>
      <span className="flex items-center gap-1.5">
        <input
          id={id}
          type="color"
          className="h-7 w-8 shrink-0 cursor-pointer rounded-md border border-border bg-bg p-0.5"
          value={safe}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value.toUpperCase())}
        />
        <span className="tabular font-mono text-2xs text-fg-muted">{value}</span>
      </span>
    </div>
  );
}

/** Заголовок секции панели: моно-капс, как подписи на пульте. */
export function PanelSection({
  title,
  actions,
  children,
  className,
  testId,
}: {
  title: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  testId?: string;
}) {
  return (
    <section className={cn("flex flex-col gap-2 border-b border-border px-3 py-3", className)} data-testid={testId}>
      <div className="flex items-center justify-between gap-2">
        <h2 className="font-mono text-2xs font-semibold tracking-wider text-fg-muted uppercase">{title}</h2>
        {actions}
      </div>
      {children}
    </section>
  );
}
