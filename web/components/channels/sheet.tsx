"use client";

import { X } from "lucide-react";
import { Dialog as RadixDialog } from "radix-ui";
import type { ReactNode } from "react";

import { Button } from "@/components/ui";
import { cn } from "@/lib/cn";

/** Боковая панель (drawer) поверх списка: radix Dialog — фокус, Escape, возврат фокуса. */
export function SideSheet({
  open,
  onClose,
  title,
  description,
  children,
  className,
  testId,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  className?: string;
  testId?: string;
}) {
  return (
    <RadixDialog.Root open={open} onOpenChange={(next) => (next ? undefined : onClose())}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="fixed inset-0 z-40 bg-overlay" />
        <RadixDialog.Content
          className={cn(
            "fixed inset-y-0 right-0 z-50 flex w-full max-w-2xl flex-col border-l border-border bg-surface text-fg",
            className,
          )}
          data-testid={testId}
        >
          <div className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
            <div className="flex min-w-0 flex-col gap-0.5">
              <RadixDialog.Title className="truncate text-base font-semibold">{title}</RadixDialog.Title>
              {description ? (
                <RadixDialog.Description className="text-xs text-fg-muted">{description}</RadixDialog.Description>
              ) : (
                <RadixDialog.Description className="sr-only">Карточка</RadixDialog.Description>
              )}
            </div>
            <RadixDialog.Close asChild>
              <Button variant="ghost" size="icon-sm" aria-label="Закрыть">
                <X className="h-4 w-4" aria-hidden="true" />
              </Button>
            </RadixDialog.Close>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}
