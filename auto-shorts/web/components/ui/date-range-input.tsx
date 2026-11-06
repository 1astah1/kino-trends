"use client";

// Источник: retentionos/packages/ui/components/date-range-input.tsx (скопировано 26.09.2026).
// Даты `date_from`/`date_to` — YYYY-MM-DD включительно, в business_timezone (§5.1).
import { Input } from "./input";

export function DateRangeInput({
  from,
  to,
  onFromChange,
  onToChange,
  fromLabel = "С даты",
  toLabel = "По дату",
  fromMin,
  fromMax,
  toMin,
  toMax,
}: {
  from: string;
  to: string;
  onFromChange: (value: string) => void;
  onToChange: (value: string) => void;
  fromLabel?: string;
  toLabel?: string;
  fromMin?: string;
  fromMax?: string;
  toMin?: string;
  toMax?: string;
}) {
  return (
    <div className="flex items-center gap-2">
      <Input
        type="date"
        aria-label={fromLabel}
        value={from}
        min={fromMin}
        max={fromMax ?? to}
        onChange={(e) => onFromChange(e.target.value)}
        className="w-auto"
      />
      <span className="text-xs text-fg-muted">—</span>
      <Input
        type="date"
        aria-label={toLabel}
        value={to}
        min={toMin ?? from}
        max={toMax}
        onChange={(e) => onToChange(e.target.value)}
        className="w-auto"
      />
    </div>
  );
}
