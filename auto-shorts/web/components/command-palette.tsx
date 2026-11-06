"use client";

import { Command } from "cmdk";
import { ArrowRight, CornerDownLeft, LogOut, Monitor, Moon, Search, Sun } from "lucide-react";
import { useRouter } from "next/navigation";
import { Dialog as RadixDialog } from "radix-ui";
import { useState } from "react";

import { MILESTONE_HINT, NAV_GROUPS, NAV_ROUTES, routeHref, SETTINGS_TABS } from "@/lib/nav";
import { resolvePaletteQuery } from "@/lib/palette";
import { useLogout } from "@/lib/queries/auth";
import { useShellStore } from "@/lib/store/shell";
import { type Theme, THEME_LABELS, useThemeStore } from "@/lib/store/theme";

const itemClass =
  "flex h-8 cursor-default items-center gap-2.5 rounded-md px-2 text-sm text-fg select-none data-[selected=true]:bg-surface-2 data-[disabled=true]:opacity-50";
const groupClass =
  "[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:pt-2 [&_[cmdk-group-heading]]:pb-1 [&_[cmdk-group-heading]]:font-mono [&_[cmdk-group-heading]]:text-2xs [&_[cmdk-group-heading]]:tracking-wider [&_[cmdk-group-heading]]:text-fg-muted [&_[cmdk-group-heading]]:uppercase";

const THEME_ICONS: Record<Theme, typeof Sun> = { dark: Moon, light: Sun, system: Monitor };

/**
 * Палитра Ctrl/Cmd+K (§7.3): переход по `video_uid`, `yt_…`/`vk_…`, коду пакета, каналу; экраны
 * и вкладки настроек; действия (тема, выход).
 */
export function CommandPalette() {
  const open = useShellStore((state) => state.paletteOpen);
  const setOpen = useShellStore((state) => state.setPaletteOpen);
  const setTheme = useThemeStore((state) => state.setTheme);
  const logout = useLogout();
  const router = useRouter();
  const [query, setQuery] = useState("");

  const close = () => {
    setOpen(false);
    setQuery("");
  };
  const go = (href: string) => {
    close();
    router.push(href);
  };

  const targets = resolvePaletteQuery(query);

  return (
    <RadixDialog.Root open={open} onOpenChange={(next) => (next ? setOpen(true) : close())}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="fixed inset-0 z-50 bg-overlay" />
        <RadixDialog.Content
          className="fixed top-[12vh] left-1/2 z-50 w-[calc(100vw-2rem)] max-w-xl -translate-x-1/2 overflow-hidden rounded-lg border border-border bg-surface text-fg shadow-[0_16px_48px_-16px_rgb(0_0_0/0.6)]"
          data-testid="command-palette"
        >
          <RadixDialog.Title className="sr-only">Палитра команд</RadixDialog.Title>
          <RadixDialog.Description className="sr-only">
            Переход к экрану, ролику, публикации, пакету или каналу и быстрые действия
          </RadixDialog.Description>
          <Command label="Палитра команд" loop>
            <div className="flex items-center gap-2 border-b border-border px-3">
              <Search className="h-4 w-4 text-fg-muted" aria-hidden="true" />
              <Command.Input
                value={query}
                onValueChange={setQuery}
                placeholder="Экран, video_uid, yt_…, код пакета, канал…"
                className="h-11 flex-1 bg-transparent text-sm text-fg outline-none placeholder:text-fg-muted/70 focus-visible:outline-none"
              />
              <kbd className="rounded-sm border border-border px-1 font-mono text-2xs text-fg-muted">Esc</kbd>
            </div>
            <Command.List className="max-h-[60vh] overflow-y-auto p-1.5">
              <Command.Empty className="px-2 py-6 text-center text-sm text-fg-muted">Ничего не найдено</Command.Empty>

              {targets.length ? (
                <Command.Group heading="Перейти к" className={groupClass}>
                  {targets.map((target) => (
                    <Command.Item
                      key={target.id}
                      value={`${target.id} ${query}`}
                      onSelect={() => go(target.href)}
                      className={itemClass}
                    >
                      <ArrowRight className="h-4 w-4 text-accent" aria-hidden="true" />
                      <span className="flex-1 truncate font-mono text-xs">{target.label}</span>
                      <span className="text-xs text-fg-muted">{target.hint}</span>
                    </Command.Item>
                  ))}
                </Command.Group>
              ) : null}

              {NAV_GROUPS.map((group) => {
                const routes = NAV_ROUTES.filter((route) => route.group === group.id);
                return (
                  <Command.Group key={group.id} heading={group.label} className={groupClass}>
                    {routes.map((route) => {
                      const Icon = route.icon;
                      return (
                        <Command.Item
                          key={route.path}
                          value={`${route.title} ${route.label} ${route.path}`}
                          keywords={route.keywords}
                          onSelect={() => go(routeHref(route.path))}
                          className={itemClass}
                        >
                          <Icon className="h-4 w-4 text-fg-muted" aria-hidden="true" />
                          <span className="flex-1 truncate">{route.title}</span>
                          {route.status === "soon" ? (
                            <span className="font-mono text-2xs text-fg-muted" title={MILESTONE_HINT[route.milestone]}>
                              скоро · {route.milestone}
                            </span>
                          ) : null}
                        </Command.Item>
                      );
                    })}
                  </Command.Group>
                );
              })}

              <Command.Group heading="Настройки" className={groupClass}>
                {SETTINGS_TABS.map((tab) => (
                  <Command.Item
                    key={tab.id}
                    value={`Настройки ${tab.label} settings ${tab.id}`}
                    onSelect={() => go(routeHref("/settings", { tab: tab.id }))}
                    className={itemClass}
                  >
                    <CornerDownLeft className="h-4 w-4 text-fg-muted" aria-hidden="true" />
                    <span className="flex-1">Настройки → {tab.label}</span>
                    {tab.status === "soon" ? (
                      <span className="font-mono text-2xs text-fg-muted">скоро · {tab.milestone}</span>
                    ) : null}
                  </Command.Item>
                ))}
              </Command.Group>

              <Command.Group heading="Действия" className={groupClass}>
                {(Object.keys(THEME_LABELS) as Theme[]).map((theme) => {
                  const Icon = THEME_ICONS[theme];
                  return (
                    <Command.Item
                      key={theme}
                      value={`Тема ${THEME_LABELS[theme]} theme ${theme}`}
                      onSelect={() => {
                        setTheme(theme);
                        close();
                      }}
                      className={itemClass}
                    >
                      <Icon className="h-4 w-4 text-fg-muted" aria-hidden="true" />
                      Тема: {THEME_LABELS[theme].toLowerCase()}
                    </Command.Item>
                  );
                })}
                <Command.Item
                  value="Выйти logout"
                  onSelect={() => {
                    close();
                    logout.mutate(undefined, { onSettled: () => router.replace("/login/") });
                  }}
                  className={itemClass}
                >
                  <LogOut className="h-4 w-4 text-fg-muted" aria-hidden="true" />
                  Выйти
                </Command.Item>
              </Command.Group>
            </Command.List>
          </Command>
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}
