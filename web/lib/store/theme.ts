// Источник: retentionos/apps/web/lib/store/theme.ts (скопировано 26.09.2026).
// Отличие: тёмная тема по умолчанию (§7.6 «оператор смотрит видео»), ключ `as-theme`.
import { create } from "zustand";

export type Theme = "dark" | "light" | "system";

export const THEME_STORAGE_KEY = "as-theme";
export const DEFAULT_THEME: Theme = "dark";

/**
 * Инлайн-скрипт для <head>: применяет сохранённую тему до первой отрисовки, чтобы не мигало.
 * В разметке по умолчанию `<html data-theme="dark">`.
 */
export const THEME_BOOT_SCRIPT = `(function(){try{var t=localStorage.getItem("${THEME_STORAGE_KEY}");var d=document.documentElement;if(t==="light"){d.setAttribute("data-theme","light")}else if(t==="system"){d.removeAttribute("data-theme")}else{d.setAttribute("data-theme","dark")}}catch(e){}})();`;

export function applyTheme(theme: Theme) {
  if (typeof document === "undefined") return;
  if (theme === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", theme);
}

function readStoredTheme(): Theme {
  try {
    const raw = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (raw === "light" || raw === "dark" || raw === "system") return raw;
  } catch {
    // приватный режим или запрет хранилища — остаётся тема по умолчанию
  }
  return DEFAULT_THEME;
}

interface ThemeState {
  theme: Theme;
  setTheme: (theme: Theme) => void;
  cycle: () => void;
  hydrate: () => void;
}

const ORDER: Theme[] = ["dark", "light", "system"];

export const useThemeStore = create<ThemeState>((set, get) => ({
  theme: DEFAULT_THEME,
  setTheme: (theme) => {
    set({ theme });
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, theme);
    } catch {
      // выбор просто не сохранится
    }
    applyTheme(theme);
  },
  cycle: () => {
    const current = get().theme;
    get().setTheme(ORDER[(ORDER.indexOf(current) + 1) % ORDER.length]);
  },
  hydrate: () => {
    const stored = readStoredTheme();
    set({ theme: stored });
    applyTheme(stored);
  },
}));

export const THEME_LABELS: Record<Theme, string> = {
  dark: "Тёмная",
  light: "Светлая",
  system: "Как в системе",
};
