"use client";

// Источник: retentionos/packages/ui/components/dialog.tsx (скопировано 26.09.2026).
// API тот же (open, onClose, title, description), но внутри примитив radix-ui Dialog:
// портал, ловушка фокуса, Escape, возврат фокуса на кнопку-триггер.
import { X } from "lucide-react";
import { Dialog as RadixDialog } from "radix-ui";
import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

import { Button } from "./button";

export interface DialogProps {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  className?: string;
  closeLabel?: string;
}

export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  className,
  closeLabel = "Закрыть",
}: DialogProps) {
  return (
    <RadixDialog.Root open={open} onOpenChange={(next) => (next ? undefined : onClose())}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="fixed inset-0 z-50 bg-overlay" />
        <RadixDialog.Content
          className={cn(
            "fixed left-1/2 top-1/2 z-50 flex max-h-[85vh] w-[calc(100vw-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 flex-col gap-3 rounded-lg border border-border bg-surface p-4 text-fg",
            className,
          )}
        >
          <div className="flex items-start justify-between gap-3">
            <div className="flex flex-col gap-0.5">
              <RadixDialog.Title className="text-base font-semibold">{title}</RadixDialog.Title>
              {description ? (
                <RadixDialog.Description className="text-xs text-fg-muted">{description}</RadixDialog.Description>
              ) : (
                <RadixDialog.Description className="sr-only">{title}</RadixDialog.Description>
              )}
            </div>
            <RadixDialog.Close asChild>
              <Button variant="ghost" size="icon-sm" aria-label={closeLabel}>
                <X className="h-4 w-4" aria-hidden="true" />
              </Button>
            </RadixDialog.Close>
          </div>
          <div className="min-h-0 overflow-y-auto">{children}</div>
          {footer ? <div className="flex items-center justify-end gap-2 pt-1">{footer}</div> : null}
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}
