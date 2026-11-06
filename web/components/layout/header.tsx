"use client";

import { Menu, Search } from "lucide-react";

import { useShellStore } from "@/lib/store/shell";

import { AlertsBell } from "./alerts-bell";
import { Breadcrumbs } from "./breadcrumbs";
import { BudgetMeter } from "./budget-meter";
import { LiveDot } from "./live-dot";
import { NewJobButton } from "./new-job-button";
import { headerButtonClass } from "./popover";
import { ThemeToggle } from "./theme-toggle";
import { UserMenu } from "./user-menu";
import { WorkerIndicator } from "./worker-indicator";

/**
 * Шапка 44 px (§7.3), слева направо: крошки; индикатор воркера; бюджет дня; «Новый ролик» (N);
 * алерты; tally-точка SSE; тема и пользователь. Поиск/палитра — Ctrl/Cmd+K.
 */
export function Header() {
  const openMobileNav = useShellStore((state) => state.setMobileNavOpen);
  const openPalette = useShellStore((state) => state.setPaletteOpen);
  return (
    <header className="flex h-11 shrink-0 items-center gap-2 border-b border-border bg-surface px-2 md:px-4">
      <button
        type="button"
        className={`${headerButtonClass} md:hidden`}
        aria-label="Открыть меню"
        onClick={() => openMobileNav(true)}
      >
        <Menu className="h-4 w-4" aria-hidden="true" />
      </button>
      <div className="min-w-0 flex-1">
        <Breadcrumbs />
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <WorkerIndicator />
        <BudgetMeter />
        <NewJobButton />
        <button
          type="button"
          className={`${headerButtonClass} hidden sm:inline-flex`}
          onClick={() => openPalette(true)}
          aria-label="Палитра команд"
          aria-keyshortcuts="Control+K Meta+K"
        >
          <Search className="h-4 w-4" aria-hidden="true" />
          <kbd className="hidden rounded-sm border border-border px-1 font-mono text-2xs lg:inline">Ctrl K</kbd>
        </button>
        <AlertsBell />
        <LiveDot />
        <div className="hidden sm:block">
          <ThemeToggle />
        </div>
        <UserMenu />
      </div>
    </header>
  );
}
