"use client";

import { ChevronDown, KeyRound, LogOut, Monitor, Moon, Sun } from "lucide-react";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { DropdownMenu } from "radix-ui";
import { useState } from "react";

import { cn } from "@/lib/cn";
import { useLogout } from "@/lib/queries/auth";
import { ROLE_META } from "@/lib/status-meta";
import { THEME_LABELS, type Theme, useThemeStore } from "@/lib/store/theme";

import { useCurrentUser } from "../auth/auth-context";
import { headerButtonClass } from "./popover";

// Форма смены пароля (react-hook-form + zod) грузится только при открытии: не раздувает оболочку.
const ChangePasswordDialog = dynamic(
  () => import("../auth/change-password-dialog").then((module) => module.ChangePasswordDialog),
  { ssr: false },
);

const itemClass =
  "flex h-8 cursor-default items-center gap-2 rounded-md px-2 text-sm text-fg outline-none select-none data-[highlighted]:bg-surface-2 data-[disabled]:opacity-50";

const THEME_ICONS: Record<Theme, typeof Sun> = { dark: Moon, light: Sun, system: Monitor };

/** Пользователь: имя, роль («Владелец» для admin), тема, выход. */
export function UserMenu() {
  const user = useCurrentUser();
  const logout = useLogout();
  const router = useRouter();
  const theme = useThemeStore((state) => state.theme);
  const setTheme = useThemeStore((state) => state.setTheme);
  const role = ROLE_META[user.role];
  const [changingPassword, setChangingPassword] = useState(false);
  const initials = user.display_name.trim().slice(0, 1).toUpperCase() || user.login.slice(0, 1).toUpperCase();

  const onLogout = () => {
    logout.mutate(undefined, { onSettled: () => router.replace("/login/") });
  };

  return (
    <>
      <DropdownMenu.Root>
        <DropdownMenu.Trigger asChild>
          <button
            type="button"
            className={cn(headerButtonClass, "pl-1")}
            aria-label={`Пользователь ${user.display_name}`}
            data-testid="user-menu"
          >
            <span className="flex h-5 w-5 items-center justify-center rounded-sm bg-surface-2 font-mono text-2xs font-semibold text-fg">
              {initials}
            </span>
            <span className="hidden max-w-[120px] truncate text-fg xl:inline">{user.display_name}</span>
            <ChevronDown className="h-3 w-3" aria-hidden="true" />
          </button>
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content
            align="end"
            sideOffset={6}
            className="z-50 w-60 rounded-lg border border-border bg-surface p-1 text-fg shadow-[0_8px_24px_-12px_rgb(0_0_0/0.5)]"
          >
            <div className="px-2 py-1.5">
              <p className="truncate text-sm font-medium">{user.display_name}</p>
              <p className="truncate font-mono text-2xs text-fg-muted">
                {user.login} · {role.label}
              </p>
            </div>
            <DropdownMenu.Separator className="my-1 h-px bg-border" />
            <DropdownMenu.Label className="px-2 py-1 font-mono text-2xs tracking-wider text-fg-muted uppercase">
              Тема
            </DropdownMenu.Label>
            <DropdownMenu.RadioGroup value={theme} onValueChange={(value) => setTheme(value as Theme)}>
              {(Object.keys(THEME_LABELS) as Theme[]).map((value) => {
                const Icon = THEME_ICONS[value];
                return (
                  <DropdownMenu.RadioItem key={value} value={value} className={itemClass}>
                    <Icon className="h-4 w-4 text-fg-muted" aria-hidden="true" />
                    <span className="flex-1">{THEME_LABELS[value]}</span>
                    <DropdownMenu.ItemIndicator>
                      <span className="h-1.5 w-1.5 rounded-full bg-accent" aria-hidden="true" />
                    </DropdownMenu.ItemIndicator>
                  </DropdownMenu.RadioItem>
                );
              })}
            </DropdownMenu.RadioGroup>
            <DropdownMenu.Separator className="my-1 h-px bg-border" />
            <DropdownMenu.Item className={itemClass} onSelect={() => setChangingPassword(true)}>
              <KeyRound className="h-4 w-4 text-fg-muted" aria-hidden="true" />
              Сменить пароль
            </DropdownMenu.Item>
            <DropdownMenu.Item className={itemClass} onSelect={onLogout} data-testid="logout">
              <LogOut className="h-4 w-4 text-fg-muted" aria-hidden="true" />
              Выйти
            </DropdownMenu.Item>
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>
      {changingPassword ? <ChangePasswordDialog open onClose={() => setChangingPassword(false)} /> : null}
    </>
  );
}
