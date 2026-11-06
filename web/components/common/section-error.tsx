"use client";

import { RotateCcw } from "lucide-react";

import { Button, Callout } from "@/components/ui";
import { errorMessage, isApiError } from "@/lib/api/errors";

/** Ошибка секции: сообщение сервера по-русски + «Повторить». Не роняет весь экран. */
export function SectionError({
  error,
  onRetry,
  title = "Не удалось загрузить данные",
  className,
}: {
  error: unknown;
  onRetry?: () => void;
  title?: string;
  className?: string;
}) {
  const code = isApiError(error) ? error.code : null;
  return (
    <Callout
      variant="negative"
      title={title}
      className={className}
      action={
        onRetry ? (
          <Button variant="outline" size="sm" onClick={onRetry}>
            <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
            Повторить
          </Button>
        ) : undefined
      }
    >
      {errorMessage(error)}
      {code ? <span className="ml-1 font-mono text-xs">({code})</span> : null}
    </Callout>
  );
}
