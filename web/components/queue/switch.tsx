"use client";

import { cn } from "@/lib/cn";

/**
 * Тумблер (role="switch"): в UI-ките его нет, а для автоматики «включено/выключено» селект
 * «Да/Нет» читается хуже. Клавиатура: пробел и Enter как у кнопки.
 */
export function Switch({
  checked,
  onChange,
  disabled,
  label,
  id,
  className,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  label: string;
  id?: string;
  className?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      id={id}
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border transition-colors duration-150",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        "disabled:cursor-not-allowed disabled:opacity-60",
        checked ? "border-accent bg-accent" : "border-border bg-surface-2",
        className,
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          "inline-block h-3.5 w-3.5 rounded-full shadow-sm transition-transform duration-150",
          checked ? "translate-x-[18px] bg-white" : "translate-x-[3px] bg-fg-muted",
        )}
      />
    </button>
  );
}
