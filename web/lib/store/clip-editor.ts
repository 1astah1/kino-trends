"use client";

/**
 * Состояние студии нарезки (docs/ARCHITECTURE.md §7.2 «zustand — редактор нарезки», §7.3).
 *
 * - `bounds` — неприменённые границы кандидатов индексами слов (`start_word_idx`/`end_word_idx`);
 *   «Применить» шлёт PATCH, сервер снапит в тишину и пересчитывает оценку.
 * - `scripts` — авторский слой кандидата до создания ролика; `jobScripts` — несохранённая правка
 *   текста уже созданного ролика (сохраняется PUT с `expected_version`).
 * - Выбор канала, варианта баннера, рефрейма и голоса запоминается в localStorage этого браузера;
 *   тексты кандидатов — тоже (последние 40), чтобы перезагрузка вкладки не съела написанное.
 *
 * Время плеера живёт в отдельном маленьком сторе (`createClipClock`): дорожка, транскрипт и
 * индикаторы подписываются только на него, и студия целиком не перерисовывается 4 раза в секунду.
 */
import { create } from "zustand";
import { createStore, type StoreApi } from "zustand/vanilla";

import type { AuthorDraft, WordRange } from "@/components/clipping/clip-math";
import type { Schemas } from "@/lib/api/client";

type ReframeMode = Schemas["ReframeMode"];
type AuthorVoiceSource = Schemas["AuthorVoiceSource"];
/** Оформление ролика поверх профиля канала (`author_layer.frame`); пустые поля — как у канала. */
export type FrameDraft = Schemas["FrameOptions"];
export const FRAME_CHANNEL: FrameDraft = { shape: "channel" };
const FRAME_SHAPES: readonly FrameDraft["shape"][] = ["channel", "auto", "circle", "rounded", "none"];

const STORAGE_KEY = "as-clip-editor";
const MAX_STORED_SCRIPTS = 40;

export type TimelineZoom = "clip" | "wide" | "full";
export type PlayerView = "wide" | "vertical";

export interface JobScriptDraft {
  script: AuthorDraft;
  /** Версия сервера, от которой начата правка (`expected_version`). */
  baseVersion: number;
}

export interface ClipEditorState {
  /** Выделение слов в транскрипте (для «вход/выход по выделению» и ручного момента). */
  selection: WordRange | null;
  anchor: number | null;
  bounds: Record<number, WordRange>;
  scripts: Record<number, AuthorDraft>;
  scriptsTouched: Record<number, number>;
  jobScripts: Record<number, JobScriptDraft>;
  channelId: string | null;
  campaignId: number | null;
  variantId: number | null;
  reframe: ReframeMode;
  voice: AuthorVoiceSource;
  /** Форма кадра и тонкая настройка для следующих роликов. */
  frame: FrameDraft;
  /** Субтитры в ролике (галочка); снято — `captions: "none"`. */
  captions: boolean;
  zoom: TimelineZoom;
  view: PlayerView;
  loop: boolean;

  selectWord: (index: number, extend: boolean) => void;
  setSelection: (range: WordRange | null) => void;
  clearSelection: () => void;
  setBounds: (candidateId: number, range: WordRange) => void;
  resetBounds: (candidateId: number) => void;
  setScript: (candidateId: number, script: AuthorDraft) => void;
  dropScript: (candidateId: number) => void;
  setJobScript: (jobId: number, draft: JobScriptDraft) => void;
  dropJobScript: (jobId: number) => void;
  setChannel: (channelId: string | null) => void;
  setCampaign: (campaignId: number | null) => void;
  setVariant: (variantId: number | null) => void;
  setReframe: (mode: ReframeMode) => void;
  setVoice: (voice: AuthorVoiceSource) => void;
  setFrame: (patch: Partial<FrameDraft>) => void;
  resetFrame: () => void;
  setCaptions: (value: boolean) => void;
  setZoom: (zoom: TimelineZoom) => void;
  setView: (view: PlayerView) => void;
  toggleLoop: () => void;
  hydrate: () => void;
}

type Persisted = Pick<
  ClipEditorState,
  | "scripts"
  | "scriptsTouched"
  | "channelId"
  | "campaignId"
  | "variantId"
  | "reframe"
  | "voice"
  | "frame"
  | "captions"
  | "view"
  | "loop"
>;

const DEFAULTS: Persisted = {
  scripts: {},
  scriptsTouched: {},
  channelId: null,
  campaignId: null,
  variantId: null,
  reframe: "auto",
  voice: "tts",
  frame: FRAME_CHANNEL,
  captions: true,
  view: "wide",
  loop: false,
};

function persist(state: ClipEditorState) {
  // Храним только последние тексты: localStorage общий на все экраны.
  const recent = Object.entries(state.scriptsTouched)
    .sort((a, b) => b[1] - a[1])
    .slice(0, MAX_STORED_SCRIPTS)
    .map(([id]) => Number(id));
  const scripts: Record<number, AuthorDraft> = {};
  const touched: Record<number, number> = {};
  for (const id of recent) {
    if (state.scripts[id]) {
      scripts[id] = state.scripts[id];
      touched[id] = state.scriptsTouched[id];
    }
  }
  const data: Persisted = {
    scripts,
    scriptsTouched: touched,
    channelId: state.channelId,
    campaignId: state.campaignId,
    variantId: state.variantId,
    reframe: state.reframe,
    voice: state.voice,
    frame: state.frame,
    captions: state.captions,
    view: state.view,
    loop: state.loop,
  };
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
  } catch {
    // хранилище недоступно — выбор проживёт до перезагрузки
  }
}

const REFRAME_MODES: readonly ReframeMode[] = ["auto", "track", "center", "fit_blur"];

function readPersisted(): Partial<Persisted> {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Partial<Persisted> | null;
    if (!parsed || typeof parsed !== "object") return {};
    const out: Partial<Persisted> = {};
    if (parsed.scripts && typeof parsed.scripts === "object") out.scripts = parsed.scripts;
    if (parsed.scriptsTouched && typeof parsed.scriptsTouched === "object") out.scriptsTouched = parsed.scriptsTouched;
    if (typeof parsed.channelId === "string" || parsed.channelId === null) out.channelId = parsed.channelId;
    if (typeof parsed.campaignId === "number" || parsed.campaignId === null) out.campaignId = parsed.campaignId;
    if (typeof parsed.variantId === "number" || parsed.variantId === null) out.variantId = parsed.variantId;
    if (parsed.reframe && REFRAME_MODES.includes(parsed.reframe)) out.reframe = parsed.reframe;
    if (parsed.voice === "tts" || parsed.voice === "operator_recording") out.voice = parsed.voice;
    if (parsed.frame && typeof parsed.frame === "object" && FRAME_SHAPES.includes(parsed.frame.shape))
      out.frame = parsed.frame;
    if (typeof parsed.captions === "boolean") out.captions = parsed.captions;
    if (parsed.view === "wide" || parsed.view === "vertical") out.view = parsed.view;
    if (typeof parsed.loop === "boolean") out.loop = parsed.loop;
    return out;
  } catch {
    return {};
  }
}

function omit<T>(record: Record<number, T>, key: number): Record<number, T> {
  if (!(key in record)) return record;
  const next = { ...record };
  delete next[key];
  return next;
}

export const useClipEditor = create<ClipEditorState>((set, get) => ({
  ...DEFAULTS,
  selection: null,
  anchor: null,
  bounds: {},
  jobScripts: {},
  zoom: "clip",

  selectWord: (index, extend) =>
    set((state) => {
      if (extend && state.anchor !== null) {
        const a = Math.min(state.anchor, index);
        const b = Math.max(state.anchor, index);
        return { selection: { w0: a, w1: b } };
      }
      return { anchor: index, selection: { w0: index, w1: index } };
    }),
  setSelection: (range) => set({ selection: range, anchor: range ? range.w0 : null }),
  clearSelection: () => set({ selection: null, anchor: null }),
  setBounds: (candidateId, range) => set((state) => ({ bounds: { ...state.bounds, [candidateId]: range } })),
  resetBounds: (candidateId) => set((state) => ({ bounds: omit(state.bounds, candidateId) })),
  setScript: (candidateId, script) => {
    set((state) => ({
      scripts: { ...state.scripts, [candidateId]: script },
      scriptsTouched: { ...state.scriptsTouched, [candidateId]: Date.now() },
    }));
    persist(get());
  },
  dropScript: (candidateId) => {
    set((state) => ({
      scripts: omit(state.scripts, candidateId),
      scriptsTouched: omit(state.scriptsTouched, candidateId),
    }));
    persist(get());
  },
  setJobScript: (jobId, draft) => set((state) => ({ jobScripts: { ...state.jobScripts, [jobId]: draft } })),
  dropJobScript: (jobId) => set((state) => ({ jobScripts: omit(state.jobScripts, jobId) })),
  setChannel: (channelId) => {
    set({ channelId });
    persist(get());
  },
  setCampaign: (campaignId) => {
    set({ campaignId, variantId: null });
    persist(get());
  },
  setVariant: (variantId) => {
    set({ variantId });
    persist(get());
  },
  setReframe: (reframe) => {
    set({ reframe });
    persist(get());
  },
  setVoice: (voice) => {
    set({ voice });
    persist(get());
  },
  setFrame: (patch) => {
    set((state) => ({ frame: { ...state.frame, ...patch } }));
    persist(get());
  },
  resetFrame: () => {
    set((state) => ({ frame: { shape: state.frame.shape } }));
    persist(get());
  },
  setCaptions: (captions) => {
    set({ captions });
    persist(get());
  },
  setZoom: (zoom) => set({ zoom }),
  setView: (view) => {
    set({ view });
    persist(get());
  },
  toggleLoop: () => {
    set((state) => ({ loop: !state.loop }));
    persist(get());
  },
  hydrate: () => set({ ...DEFAULTS, ...readPersisted() }),
}));

// ---------------------------------------------------------------- часы плеера

export interface ClipClockState {
  /** Текущая позиция, секунды источника. */
  t: number;
  duration: number;
  playing: boolean;
  /** Проигрывание момента: остановиться (или вернуться к началу при петле) на этой секунде. */
  stopAt: number | null;
}

export type ClipClock = StoreApi<ClipClockState>;

export function createClipClock(): ClipClock {
  return createStore<ClipClockState>(() => ({ t: 0, duration: 0, playing: false, stopAt: null }));
}

/** Команды плеера для клавиш, дорожки и транскрипта. */
export interface ClipPlayerController {
  seek: (sec: number) => void;
  seekBy: (delta: number) => void;
  toggle: () => void;
  play: () => void;
  pause: () => void;
  /** Проиграть [start, end) и остановиться (или по кругу при петле). */
  playRange: (start: number, end: number) => void;
}
