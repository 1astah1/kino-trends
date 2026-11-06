"use client";

import { Popover as RadixPopover } from "radix-ui";
import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

/** Поповер шапки: кнопка-триггер + панель. Радикс даёт фокус, Escape и закрытие по клику снаружи. */
export function HeaderPopover({
  trigger,
  children,
  align = "end",
  className,
  label,
}: {
  trigger: ReactNode;
  children: ReactNode;
  align?: "start" | "center" | "end";
  className?: string;
  label: string;
}) {
  return (
    <RadixPopover.Root>
      <RadixPopover.Trigger asChild>{trigger}</RadixPopover.Trigger>
      <RadixPopover.Portal>
        <RadixPopover.Content
          align={align}
          sideOffset={6}
          aria-label={label}
          className={cn(
            "z-50 w-80 max-w-[calc(100vw-1rem)] rounded-lg border border-border bg-surface p-3 text-sm text-fg shadow-[0_8px_24px_-12px_rgb(0_0_0/0.5)]",
            className,
          )}
        >
          {children}
        </RadixPopover.Content>
      </RadixPopover.Portal>
    </RadixPopover.Root>
  );
}

export const headerButtonClass =
  "inline-flex h-7 items-center gap-1.5 rounded-md border border-transparent px-2 text-xs text-fg-muted transition-colors duration-150 hover:border-border hover:bg-surface-2 hover:text-fg data-[state=open]:border-border data-[state=open]:bg-surface-2 data-[state=open]:text-fg";
