/**
 * Открытие NewJobSheet из любого места (§7.3 шапка «Новый ролик» `N`, Конвейер, позже — студия
 * нарезки и брифы трендоскаута). Хостов может быть смонтировано несколько (AppShell и экран),
 * рисует шит только первый зарегистрированный — второй экземпляр формы не появится.
 */
import { create } from "zustand";

import type { Schemas } from "@/lib/api/client";

export interface NewJobPreset {
  kind?: "generate" | "clip";
  channelId?: string;
  topics?: string[];
  campaignId?: number | null;
  bannerVariantId?: number | null;
  source?: Schemas["ClipSourceRange"];
  trendBriefId?: number | null;
}

interface NewJobSheetState {
  open: boolean;
  preset: NewJobPreset | null;
  /** Порядковый номер открытия: форма пересоздаётся с чистым состоянием и новым ключом идемпотентности. */
  openCount: number;
  hosts: string[];
  openSheet: (preset?: NewJobPreset | null) => void;
  close: () => void;
  register: (id: string) => void;
  unregister: (id: string) => void;
}

export const useNewJobSheet = create<NewJobSheetState>((set) => ({
  open: false,
  preset: null,
  openCount: 0,
  hosts: [],
  openSheet: (preset) => set((state) => ({ open: true, preset: preset ?? null, openCount: state.openCount + 1 })),
  close: () => set({ open: false }),
  register: (id) => set((state) => (state.hosts.includes(id) ? state : { hosts: [...state.hosts, id] })),
  unregister: (id) => set((state) => ({ hosts: state.hosts.filter((host) => host !== id) })),
}));

/** Открыть «Новый ролик» (для шапки: `NewJobButton` и клавиша `N`). */
export function openNewJobSheet(preset?: NewJobPreset | null) {
  useNewJobSheet.getState().openSheet(preset);
}
