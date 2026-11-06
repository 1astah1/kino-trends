"use client";

import { Plus } from "lucide-react";
import { useHotkeys } from "react-hotkeys-hook";

import { openNewJobSheet } from "@/components/create-job/new-job-store";
import { canOperate } from "@/lib/auth";
import { cn } from "@/lib/cn";

import { useCurrentUser } from "../auth/auth-context";

function openSheet() {
  openNewJobSheet();
}

/** «Новый ролик» (`N`): открывает NewJobSheet (M2-D1); сам шит рисует `NewJobSheetHost` в AppShell. */
export function NewJobButton() {
  const user = useCurrentUser();
  useHotkeys("n", openSheet, { enabled: canOperate(user.role), preventDefault: true });
  if (!canOperate(user.role)) return null;
  return (
    <button
      type="button"
      onClick={openSheet}
      aria-keyshortcuts="N"
      title="Новый ролик (N)"
      data-testid="header-new-job"
      className={cn(
        "hidden h-7 items-center gap-1.5 rounded-md border border-border px-2 text-xs text-fg-muted transition-colors hover:bg-surface-2 hover:text-fg sm:inline-flex",
      )}
    >
      <Plus className="h-3.5 w-3.5" aria-hidden="true" />
      <span>Новый ролик</span>
      <kbd className="rounded-sm border border-border px-1 font-mono text-2xs text-fg-muted">N</kbd>
    </button>
  );
}
