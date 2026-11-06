/**
 * Настройки экрана приёмки (docs/ARCHITECTURE.md §7.3 «Приёмка»): слои оверлея, звук, петля,
 * автопереход. Хранятся в localStorage этого браузера; сбой хранилища — значения по умолчанию.
 * Счётчик сессии живёт здесь же, но не сохраняется: «сессия» — до перезагрузки вкладки.
 */
import { create } from "zustand";

const STORAGE_KEY = "as-review-prefs";

export interface ReviewOverlayPrefs {
  /** Мёртвые зоны и безопасная рамка площадки (`S`). */
  deadZones: boolean;
  /** Боксы слоёв баннера и зона субтитров (`B`). */
  banner: boolean;
}

export interface ReviewPrefsState {
  overlay: ReviewOverlayPrefs;
  muted: boolean;
  /** Громкость плеера 0…1 (ползунок и ↑/↓); 0 = без звука. */
  volume: number;
  loop: boolean;
  autoplay: boolean;
  /** После решения сразу открывать следующий ролик очереди. */
  autoAdvance: boolean;
  session: { approved: number; rejected: number; rework: number; undone: number; startedAt: number };
  toggleOverlay: (layer: keyof ReviewOverlayPrefs) => void;
  toggleMuted: () => void;
  setVolume: (value: number) => void;
  changeVolume: (delta: number) => void;
  toggleLoop: () => void;
  setAutoplay: (value: boolean) => void;
  setAutoAdvance: (value: boolean) => void;
  countDecision: (decision: "approve" | "override_qc" | "reject" | "rework") => void;
  countUndo: (decision: "approve" | "override_qc" | "reject" | "rework" | null) => void;
  hydrate: () => void;
}

type Persisted = Pick<ReviewPrefsState, "overlay" | "muted" | "volume" | "loop" | "autoplay" | "autoAdvance">;

const DEFAULTS: Persisted = {
  overlay: { deadZones: true, banner: true },
  muted: false,
  volume: 1,
  loop: true,
  autoplay: true,
  autoAdvance: true,
};

function persist(state: ReviewPrefsState) {
  const data: Persisted = {
    overlay: state.overlay,
    muted: state.muted,
    volume: state.volume,
    loop: state.loop,
    autoplay: state.autoplay,
    autoAdvance: state.autoAdvance,
  };
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
  } catch {
    // хранилище недоступно — настройки проживут до перезагрузки
  }
}

function readPersisted(): Partial<Persisted> {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Partial<Persisted> | null;
    if (!parsed || typeof parsed !== "object") return {};
    const out: Partial<Persisted> = {};
    if (parsed.overlay && typeof parsed.overlay === "object") {
      out.overlay = {
        deadZones: parsed.overlay.deadZones !== false,
        banner: parsed.overlay.banner !== false,
      };
    }
    if (typeof parsed.muted === "boolean") out.muted = parsed.muted;
    if (typeof parsed.volume === "number" && Number.isFinite(parsed.volume)) out.volume = clampVolume(parsed.volume);
    if (typeof parsed.loop === "boolean") out.loop = parsed.loop;
    if (typeof parsed.autoplay === "boolean") out.autoplay = parsed.autoplay;
    if (typeof parsed.autoAdvance === "boolean") out.autoAdvance = parsed.autoAdvance;
    return out;
  } catch {
    return {};
  }
}

/** Громкость в пределах 0…1, шаг 1%. */
export function clampVolume(value: number): number {
  if (!Number.isFinite(value)) return 1;
  return Math.round(Math.max(0, Math.min(1, value)) * 100) / 100;
}

const emptySession = () => ({ approved: 0, rejected: 0, rework: 0, undone: 0, startedAt: Date.now() });

export const useReviewPrefs = create<ReviewPrefsState>((set, get) => ({
  ...DEFAULTS,
  session: emptySession(),
  toggleOverlay: (layer) => {
    set((state) => ({ overlay: { ...state.overlay, [layer]: !state.overlay[layer] } }));
    persist(get());
  },
  toggleMuted: () => {
    // включили звук при нулевой громкости — вернуть половину, иначе кнопка «не работает»
    set((state) => (state.muted ? { muted: false, volume: state.volume > 0 ? state.volume : 0.5 } : { muted: true }));
    persist(get());
  },
  setVolume: (value) => {
    const volume = clampVolume(value);
    set({ volume, muted: volume === 0 });
    persist(get());
  },
  changeVolume: (delta) => {
    const state = get();
    state.setVolume((state.muted ? 0 : state.volume) + delta);
  },
  toggleLoop: () => {
    set((state) => ({ loop: !state.loop }));
    persist(get());
  },
  setAutoplay: (value) => {
    set({ autoplay: value });
    persist(get());
  },
  setAutoAdvance: (value) => {
    set({ autoAdvance: value });
    persist(get());
  },
  countDecision: (decision) =>
    set((state) => {
      const session = { ...state.session };
      if (decision === "approve" || decision === "override_qc") session.approved += 1;
      else if (decision === "reject") session.rejected += 1;
      else session.rework += 1;
      return { session };
    }),
  countUndo: (decision) =>
    set((state) => {
      const session = { ...state.session, undone: state.session.undone + 1 };
      if ((decision === "approve" || decision === "override_qc") && session.approved > 0) session.approved -= 1;
      else if (decision === "reject" && session.rejected > 0) session.rejected -= 1;
      else if (decision === "rework" && session.rework > 0) session.rework -= 1;
      return { session };
    }),
  hydrate: () => set({ ...DEFAULTS, ...readPersisted() }),
}));
