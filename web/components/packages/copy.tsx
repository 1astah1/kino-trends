"use client";

import { Check, Copy } from "lucide-react";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui";
import { cn } from "@/lib/cn";

/**
 * Копирование в один клик (§7.3 «Пакеты»). `navigator.clipboard` есть только в безопасном контексте
 * (https, localhost); на всякий случай — запасной путь через скрытый `<textarea>` и `execCommand`.
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // ниже — запасной путь
  }
  try {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    return ok;
  } catch {
    return false;
  }
}

/** Кнопка/клавиша копирования: помнит, что скопировано последним (галочка на 1,5 с). */
export function useCopy() {
  const [copied, setCopied] = useState<string | null>(null);
  const timer = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (timer.current) window.clearTimeout(timer.current);
    },
    [],
  );
  const copy = useCallback(async (key: string, text: string, label: string) => {
    if (!text) {
      toast.warning(`«${label}» пусто — копировать нечего`);
      return;
    }
    const ok = await copyText(text);
    if (!ok) {
      toast.error("Браузер не дал доступ к буферу обмена", { description: "Выделите текст и скопируйте вручную." });
      return;
    }
    setCopied(key);
    toast.success(`Скопировано: ${label}`, { duration: 1500 });
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setCopied(null), 1500);
  }, []);
  return { copied, copy };
}

export function CopyButton({
  copied,
  onCopy,
  label,
  hotkey,
  className,
  testId,
}: {
  copied: boolean;
  onCopy: () => void;
  label: string;
  hotkey?: string;
  className?: string;
  testId?: string;
}) {
  return (
    <Button
      variant="outline"
      size="xs"
      onClick={onCopy}
      className={cn(copied && "border-positive/50 text-positive", className)}
      aria-label={`Скопировать: ${label}${hotkey ? ` (клавиша ${hotkey})` : ""}`}
      title={hotkey ? `Скопировать (${hotkey})` : "Скопировать"}
      data-testid={testId}
    >
      {copied ? (
        <Check className="h-3.5 w-3.5" aria-hidden="true" />
      ) : (
        <Copy className="h-3.5 w-3.5" aria-hidden="true" />
      )}
      {copied ? "Готово" : "Копировать"}
      {hotkey ? (
        <kbd className="ml-0.5 hidden rounded-sm border border-border px-1 font-mono text-2xs text-fg-muted md:inline">
          {hotkey}
        </kbd>
      ) : null}
    </Button>
  );
}

/** Строка «подпись · значение · Копировать» для коротких значений (ссылки, коды). */
export function CopyRow({
  label,
  value,
  copied,
  onCopy,
  hotkey,
  mono = true,
  extra,
  testId,
}: {
  label: string;
  value: string | null | undefined;
  copied: boolean;
  onCopy: () => void;
  hotkey?: string;
  mono?: boolean;
  extra?: ReactNode;
  testId?: string;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-1 sm:flex-row sm:items-center sm:gap-2">
      <span className="w-28 shrink-0 text-xs text-fg-muted">{label}</span>
      <span
        className={cn("min-w-0 flex-1 truncate text-sm", mono && "font-mono text-xs", !value && "text-fg-muted")}
        title={value ?? undefined}
      >
        {value || "—"}
      </span>
      <span className="flex shrink-0 items-center gap-1.5">
        {extra}
        {value ? <CopyButton copied={copied} onCopy={onCopy} label={label} hotkey={hotkey} testId={testId} /> : null}
      </span>
    </div>
  );
}
