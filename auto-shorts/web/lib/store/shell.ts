import { create } from "zustand";

const SIDEBAR_KEY = "as-sidebar-collapsed";

interface ShellState {
  sidebarCollapsed: boolean;
  mobileNavOpen: boolean;
  paletteOpen: boolean;
  /** Палитра грузится лениво (cmdk не входит в стартовый бандл) и остаётся смонтированной после первого открытия. */
  paletteLoaded: boolean;
  togglePalette: () => void;
  toggleSidebar: () => void;
  setMobileNavOpen: (open: boolean) => void;
  setPaletteOpen: (open: boolean) => void;
  hydrate: () => void;
}

/** Состояние оболочки: свёртка сайдбара (216 → 56 px), мобильное меню, палитра Ctrl+K. */
export const useShellStore = create<ShellState>((set, get) => ({
  sidebarCollapsed: false,
  mobileNavOpen: false,
  paletteOpen: false,
  paletteLoaded: false,
  togglePalette: () => set((state) => ({ paletteOpen: !state.paletteOpen, paletteLoaded: true })),
  toggleSidebar: () => {
    const next = !get().sidebarCollapsed;
    set({ sidebarCollapsed: next });
    try {
      window.localStorage.setItem(SIDEBAR_KEY, next ? "1" : "0");
    } catch {
      // не сохранится между сессиями — не страшно
    }
  },
  setMobileNavOpen: (open) => set({ mobileNavOpen: open }),
  setPaletteOpen: (open) => set((state) => ({ paletteOpen: open, paletteLoaded: state.paletteLoaded || open })),
  hydrate: () => {
    try {
      set({ sidebarCollapsed: window.localStorage.getItem(SIDEBAR_KEY) === "1" });
    } catch {
      // по умолчанию развёрнут
    }
  },
}));
