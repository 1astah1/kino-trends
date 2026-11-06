"use client";

import dynamic from "next/dynamic";
import { useHotkeys } from "react-hotkeys-hook";

import { useShellStore } from "@/lib/store/shell";

// cmdk и разметка палитры грузятся отдельным чанком при первом открытии (бюджет оболочки, §7.1).
const CommandPalette = dynamic(() => import("./command-palette").then((module) => module.CommandPalette), {
  ssr: false,
});

/** Ctrl/Cmd+K открывает палитру команд (§7.3). */
export function LazyCommandPalette() {
  const loaded = useShellStore((state) => state.paletteLoaded);
  const toggle = useShellStore((state) => state.togglePalette);
  useHotkeys("mod+k", toggle, { enableOnFormTags: true, preventDefault: true });
  return loaded ? <CommandPalette /> : null;
}
