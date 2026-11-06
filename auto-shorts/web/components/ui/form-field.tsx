// Источник: retentionos/packages/ui/components/form-field.tsx (скопировано 26.09.2026).
import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export interface FormFieldProps {
  label: string;
  htmlFor?: string;
  hint?: ReactNode;
  error?: string;
  children: ReactNode;
  className?: string;
}

export function FormField({ label, htmlFor, hint, error, children, className }: FormFieldProps) {
  const messageId = htmlFor ? `${htmlFor}-message` : undefined;
  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <label htmlFor={htmlFor} className="text-xs font-medium text-fg-muted">
        {label}
      </label>
      {children}
      {error ? (
        <p id={messageId} role="alert" className="text-xs text-negative">
          {error}
        </p>
      ) : hint ? (
        <p id={messageId} className="text-xs text-fg-muted">
          {hint}
        </p>
      ) : null}
    </div>
  );
}
