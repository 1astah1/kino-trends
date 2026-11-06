"use client";

import { Toaster } from "sonner";

import { useThemeStore } from "@/lib/store/theme";

/** Тосты (sonner): aria-live встроен; тема следует выбору в шапке. */
export function AppToaster() {
  const theme = useThemeStore((state) => state.theme);
  return (
    <Toaster
      theme={theme}
      position="bottom-right"
      closeButton
      duration={5000}
      toastOptions={{
        classNames: {
          toast: "!bg-surface !text-fg !border-border !rounded-lg !font-sans",
          description: "!text-fg-muted",
          actionButton: "!bg-accent !text-accent-fg",
        },
      }}
    />
  );
}
