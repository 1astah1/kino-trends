"use client";

import { useQuery } from "@tanstack/react-query";
import { Hourglass } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/cn";
import { activeSidebarPath, NAV_GROUPS, type NavBadgeKey, type NavRoute, routeHref, SIDEBAR_ROUTES } from "@/lib/nav";
import { queryKeys } from "@/lib/queries/keys";

type PackagesCounts = { ready: number; downloaded: number };

/** Значение бейджа из кеша запросов: его наполняют SSE-события и экраны M2–M6. */
function useBadgeValue(key: NavBadgeKey | undefined): number | null {
  const queryKey =
    key === "review"
      ? queryKeys.reviewQueueCount
      : key === "packages"
        ? queryKeys.packagesCounts
        : queryKeys.trendsAttention;
  const { data } = useQuery<number | PackagesCounts | null>({
    queryKey,
    queryFn: () => Promise.resolve(null),
    enabled: false,
    staleTime: Infinity,
  });
  if (!key || data === null || data === undefined) return null;
  if (typeof data === "number") return data;
  return data.ready;
}

function NavBadge({ badge, collapsed }: { badge?: NavBadgeKey; collapsed: boolean }) {
  const value = useBadgeValue(badge);
  if (!badge || !value) return null;
  const text = value > 99 ? "99+" : String(value);
  const tone = badge === "trends" ? "bg-machine text-bg" : "bg-human text-human-fg";
  return collapsed ? (
    <span className={cn("absolute top-1 right-1 h-2 w-2 rounded-full", tone.split(" ")[0])} aria-hidden="true" />
  ) : (
    <span className={cn("tabular rounded-sm px-1.5 font-mono text-2xs leading-4 font-semibold", tone)}>{text}</span>
  );
}

function NavLink({
  route,
  active,
  collapsed,
  onNavigate,
}: {
  route: NavRoute;
  active: boolean;
  collapsed: boolean;
  onNavigate?: () => void;
}) {
  const Icon = route.icon;
  const soon = route.status === "soon";
  return (
    <Link
      href={routeHref(route.path)}
      onClick={onNavigate}
      aria-current={active ? "page" : undefined}
      title={collapsed ? `${route.label}${soon ? " · скоро" : ""}` : undefined}
      aria-label={collapsed ? route.label : undefined}
      className={cn(
        "relative flex h-8 items-center gap-2.5 rounded-md px-2.5 text-sm transition-colors duration-150",
        active ? "bg-surface-2 font-medium text-fg" : "text-fg-muted hover:bg-surface-2/60 hover:text-fg",
        collapsed && "justify-center px-0",
      )}
    >
      {active ? (
        <span className="absolute top-1.5 bottom-1.5 left-0 w-0.5 rounded-full bg-accent" aria-hidden="true" />
      ) : null}
      <Icon className={cn("h-4 w-4 shrink-0", active && "text-accent")} aria-hidden="true" />
      {collapsed ? null : <span className="min-w-0 flex-1 truncate">{route.label}</span>}
      <NavBadge badge={route.badge} collapsed={collapsed} />
      {!collapsed && soon ? (
        <span className="shrink-0 text-fg-muted/60" title={`Скоро: ${route.milestone}`}>
          <Hourglass className="h-3 w-3" aria-hidden="true" />
          <span className="sr-only">скоро</span>
        </span>
      ) : null}
    </Link>
  );
}

export function NavList({ collapsed = false, onNavigate }: { collapsed?: boolean; onNavigate?: () => void }) {
  const pathname = usePathname();
  const activePath = activeSidebarPath(pathname);

  return (
    <nav aria-label="Основное меню" className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-2 py-3">
      {NAV_GROUPS.map((group) => {
        const routes = SIDEBAR_ROUTES.filter((route) => route.group === group.id);
        if (routes.length === 0) return null;
        return (
          <div key={group.id} className="flex flex-col gap-0.5" role="group" aria-label={group.label}>
            {collapsed ? (
              <div className="mx-2 mb-1 border-t border-border first:hidden" aria-hidden="true" />
            ) : (
              <p className="px-2.5 pb-0.5 font-mono text-2xs tracking-wider text-fg-muted/80 uppercase">
                {group.label}
              </p>
            )}
            {routes.map((route) => (
              <NavLink
                key={route.path}
                route={route}
                active={activePath === route.path}
                collapsed={collapsed}
                onNavigate={onNavigate}
              />
            ))}
          </div>
        );
      })}
    </nav>
  );
}
