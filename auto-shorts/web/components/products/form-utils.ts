import type { FieldValues, Path, UseFormSetError } from "react-hook-form";

import { errorMessage, ValidationError } from "@/lib/api/errors";

/**
 * 422 → ошибки по полям формы (`details.errors[].loc` без `body`, через точку: `brand.display_domain`);
 * что не легло на поле — в общий текст формы (`root.server`).
 */
export function applyServerErrors<T extends FieldValues>(
  error: unknown,
  setError: UseFormSetError<T>,
  fields: readonly string[],
): void {
  if (error instanceof ValidationError) {
    let matched = false;
    for (const [field, message] of Object.entries(error.fieldErrors)) {
      if (fields.includes(field)) {
        setError(field as Path<T>, { type: "server", message });
        matched = true;
      }
    }
    if (matched) return;
  }
  setError("root.server" as Path<T>, { type: "server", message: errorMessage(error) });
}

/** Пустая строка из поля формы → null для API. */
export function orNull(value: string | null | undefined): string | null {
  const trimmed = (value ?? "").trim();
  return trimmed ? trimmed : null;
}

export const SLUG_RE = /^[a-z0-9][a-z0-9_-]{1,39}$/;
export const HOST_RE = /^[a-z0-9.-]{3,253}$/;
export const TG_BOT_RE = /^[A-Za-z0-9_]{5,32}$/;
export const TMA_RE = /^[A-Za-z0-9_]{3,64}$/;
