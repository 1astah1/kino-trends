// Источник: retentionos/packages/ui/components/input.tsx (скопировано 26.09.2026).
import { forwardRef, type InputHTMLAttributes, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";

import { cn } from "@/lib/cn";

const fieldClasses =
  "h-8 w-full rounded-md border border-border bg-bg px-2.5 text-sm text-fg placeholder:text-fg-muted/70 transition-colors duration-150 hover:border-fg-muted/50 focus-visible:border-accent disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-negative";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input(
  { className, ...props },
  ref,
) {
  return <input ref={ref} className={cn(fieldClasses, className)} {...props} />;
});

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select(
  { className, ...props },
  ref,
) {
  return <select ref={ref} className={cn(fieldClasses, "pr-7", className)} {...props} />;
});

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea(
  { className, ...props },
  ref,
) {
  return <textarea ref={ref} className={cn(fieldClasses, "h-auto min-h-16 resize-y py-1.5", className)} {...props} />;
});
