"use client";

import { QueryClientProvider } from "@tanstack/react-query";
import { NuqsAdapter } from "nuqs/adapters/next/app";
import { type ReactNode, useEffect, useState } from "react";
import { HotkeysProvider } from "react-hotkeys-hook";

import { AppToaster } from "@/components/common/app-toaster";
import { MockGate } from "@/components/common/mock-gate";
import { makeQueryClient } from "@/lib/query-client";
import { useShellStore } from "@/lib/store/shell";
import { useThemeStore } from "@/lib/store/theme";

/** Providers(QueryClient, Toaster, Hotkeys) (§7.5). LiveEvents монтируется в AppShell — после входа. */
export function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(makeQueryClient);

  useEffect(() => {
    useThemeStore.getState().hydrate();
    useShellStore.getState().hydrate();
  }, []);

  return (
    <NuqsAdapter>
      <QueryClientProvider client={queryClient}>
        <HotkeysProvider initiallyActiveScopes={["global"]}>
          <MockGate>{children}</MockGate>
          <AppToaster />
        </HotkeysProvider>
      </QueryClientProvider>
    </NuqsAdapter>
  );
}
