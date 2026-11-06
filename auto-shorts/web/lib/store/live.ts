import { create } from "zustand";

import type { LiveStatus } from "@/lib/api/sse";

interface LiveState {
  status: LiveStatus;
  lastEventAt: number;
  setStatus: (status: LiveStatus) => void;
  touch: (at: number) => void;
}

/** Состояние SSE-канала для tally-точки и для переключения экранов на опрос. */
export const useLiveStore = create<LiveState>((set) => ({
  status: "connecting",
  lastEventAt: 0,
  setStatus: (status) => set({ status }),
  touch: (at) => set({ lastEventAt: at }),
}));
