"use client";

// Источник: retentionos/packages/ui/components/tabs.tsx (скопировано 26.09.2026).
// Добавлены стрелки влево/вправо по вкладкам (WAI-ARIA tabs) и id для aria-controls.
import { type KeyboardEvent, type ReactNode, useRef } from "react";

import { cn } from "@/lib/cn";

export interface TabItem {
  value: string;
  label: string;
  badge?: ReactNode;
}

export interface TabsProps {
  items: TabItem[];
  value: string;
  onChange: (value: string) => void;
  className?: string;
  label?: string;
  idPrefix?: string;
}

export function tabId(prefix: string, value: string) {
  return `${prefix}-tab-${value}`;
}

export function tabPanelId(prefix: string, value: string) {
  return `${prefix}-panel-${value}`;
}

export function Tabs({ items, value, onChange, className, label, idPrefix = "tabs" }: TabsProps) {
  const listRef = useRef<HTMLDivElement>(null);

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    const index = items.findIndex((item) => item.value === value);
    const delta = event.key === "ArrowRight" ? 1 : -1;
    const next = items[(index + delta + items.length) % items.length];
    if (!next) return;
    event.preventDefault();
    onChange(next.value);
    listRef.current?.querySelector<HTMLButtonElement>(`#${CSS.escape(tabId(idPrefix, next.value))}`)?.focus();
  }

  return (
    <div
      ref={listRef}
      className={cn("flex gap-0.5 overflow-x-auto border-b border-border", className)}
      role="tablist"
      aria-label={label}
      onKeyDown={onKeyDown}
    >
      {items.map((item) => {
        const active = item.value === value;
        return (
          <button
            key={item.value}
            id={tabId(idPrefix, item.value)}
            type="button"
            role="tab"
            aria-selected={active}
            aria-controls={tabPanelId(idPrefix, item.value)}
            tabIndex={active ? 0 : -1}
            onClick={() => onChange(item.value)}
            className={cn(
              "-mb-px flex h-8 shrink-0 items-center gap-1.5 border-b-2 px-3 text-sm font-medium transition-colors duration-150",
              active ? "border-accent text-fg" : "border-transparent text-fg-muted hover:text-fg",
            )}
          >
            {item.label}
            {item.badge}
          </button>
        );
      })}
    </div>
  );
}

export function PillToggle<T extends string>({
  options,
  value,
  onChange,
  className,
  label,
}: {
  options: Array<{ value: T; label: string }>;
  value: T;
  onChange: (value: T) => void;
  className?: string;
  label?: string;
}) {
  return (
    <div
      role="group"
      aria-label={label}
      className={cn("inline-flex items-center gap-0.5 rounded-md border border-border p-0.5", className)}
    >
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          aria-pressed={opt.value === value}
          onClick={() => onChange(opt.value)}
          className={cn(
            "h-6 rounded-sm px-2 text-xs font-medium transition-colors duration-150",
            opt.value === value ? "bg-accent text-accent-fg" : "text-fg-muted hover:text-fg",
          )}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
