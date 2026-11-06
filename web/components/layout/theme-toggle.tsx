"use client";

import { Monitor, Moon, Sun } from "lucide-react";

import { THEME_LABELS, useThemeStore } from "@/lib/store/theme";

import { headerButtonClass } from "./popover";

const ICONS = { dark: Moon, light: Sun, system: Monitor } as const;

/** Переключатель темы: тёмная → светлая → как в системе. */
export function ThemeToggle() {
  const theme = useThemeStore((state) => state.theme);
  const cycle = useThemeStore((state) => state.cycle);
  const Icon = ICONS[theme];
  return (
    <button
      type="button"
      onClick={cycle}
      className={headerButtonClass}
      aria-label={`Тема: ${THEME_LABELS[theme]}. Переключить`}
      title={`Тема: ${THEME_LABELS[theme]}`}
    >
      <Icon className="h-4 w-4" aria-hidden="true" />
    </button>
  );
}
