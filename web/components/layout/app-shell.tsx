"use client";

import type { ReactNode } from "react";

import { LazyCommandPalette } from "@/components/command-palette-lazy";
import { NewJobSheetHost } from "@/components/create-job/new-job-sheet-host";
import { useLiveEvents } from "@/lib/hooks/use-live-events";

import { Header } from "./header";
import { MobileNav } from "./mobile-nav";
import { Sidebar } from "./sidebar";

/**
 * AppShell (§7.5): Sidebar · Header · CommandPalette · NewJobSheet · main. Один EventSource на
 * вкладку открывается здесь, то есть только после входа. NewJobSheetHost ленивый и единственный:
 * хост на экране Конвейера второй формы не рисует.
 */
export function AppShell({ children }: { children: ReactNode }) {
  useLiveEvents();
  return (
    <div className="flex h-dvh overflow-hidden">
      <a href="#main-content" className="skip-link">
        К содержимому
      </a>
      <Sidebar />
      <MobileNav />
      <div className="flex min-w-0 flex-1 flex-col">
        <Header />
        <main id="main-content" tabIndex={-1} className="min-h-0 flex-1 overflow-y-auto focus:outline-none">
          {children}
        </main>
      </div>
      <LazyCommandPalette />
      <NewJobSheetHost />
    </div>
  );
}
