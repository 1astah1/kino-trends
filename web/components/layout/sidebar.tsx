"use client";

import { PanelLeftClose, PanelLeftOpen } from "lucide-react";

import { cn } from "@/lib/cn";
import { useShellStore } from "@/lib/store/shell";

import { Brand } from "./brand";
import { NavList } from "./nav-list";

/** Сайдбар 216 px со свёрткой до 56 px (§7.3). На телефоне — MobileNav. */
export function Sidebar() {
  const collapsed = useShellStore((state) => state.sidebarCollapsed);
  const toggle = useShellStore((state) => state.toggleSidebar);
  return (
    <aside
      className={cn(
        "hidden shrink-0 flex-col border-r border-border bg-surface transition-[width] duration-150 md:flex",
        collapsed ? "w-14" : "w-[216px]",
      )}
      aria-label="Навигация"
    >
      <Brand collapsed={collapsed} />
      <NavList collapsed={collapsed} />
      <div className="border-t border-border p-2">
        <button
          type="button"
          onClick={toggle}
          aria-label={collapsed ? "Развернуть меню" : "Свернуть меню"}
          aria-expanded={!collapsed}
          className={cn(
            "flex h-8 w-full items-center gap-2.5 rounded-md px-2.5 text-xs text-fg-muted transition-colors hover:bg-surface-2 hover:text-fg",
            collapsed && "justify-center px-0",
          )}
        >
          {collapsed ? (
            <PanelLeftOpen className="h-4 w-4" aria-hidden="true" />
          ) : (
            <PanelLeftClose className="h-4 w-4" aria-hidden="true" />
          )}
          {collapsed ? null : <span>Свернуть</span>}
        </button>
      </div>
    </aside>
  );
}
