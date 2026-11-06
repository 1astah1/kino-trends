"use client";

import { create } from "zustand";

import { draftKey, type EditorDoc, type EditorDraft } from "./draft";

const HISTORY_LIMIT = 80;
/** Правки одного поля подряд (набор в числовом поле, ползунок) склеиваются в один шаг отмены. */
const COALESCE_MS = 700;

interface EditorState {
  doc: EditorDoc | null;
  draft: EditorDraft | null;
  /** Ключ последнего сохранённого черновика: сравнение даёт «есть изменения». */
  savedKey: string;
  selectedId: string | null;
  past: EditorDraft[];
  future: EditorDraft[];
  lastEdit: { key: string; at: number } | null;
  /** Идёт запрос сохранения (второй параллельно не отправляем). */
  saving: boolean;
  setSaving: (saving: boolean) => void;
  /** Загрузить документ (новый вариант или перечитанный после конфликта); история сбрасывается. */
  load: (doc: EditorDoc, draft: EditorDraft) => void;
  select: (id: string | null) => void;
  /**
   * Правка черновика. `coalesce` — ключ поля: правки того же поля чаще, чем раз в 700 мс,
   * становятся одним шагом отмены.
   */
  update: (fn: (draft: EditorDraft) => EditorDraft, opts?: { coalesce?: string }) => void;
  undo: () => void;
  redo: () => void;
  /** Сервер принял черновик `saved` (версия `doc.version`); правки, сделанные во время запроса, остаются. */
  markSaved: (doc: EditorDoc, saved: EditorDraft) => void;
  setDoc: (patch: Partial<EditorDoc>) => void;
  reset: () => void;
}

export const useEditorStore = create<EditorState>((set, get) => ({
  doc: null,
  draft: null,
  savedKey: "",
  selectedId: null,
  past: [],
  future: [],
  lastEdit: null,
  saving: false,
  setSaving: (saving) => set({ saving }),
  load: (doc, draft) => {
    const preferred = draft.layers.find((layer) => layer.kind === "creative") ?? draft.layers[0];
    set({
      doc,
      draft,
      savedKey: draftKey(draft),
      selectedId: preferred?.id ?? null,
      past: [],
      future: [],
      lastEdit: null,
    });
  },
  select: (id) => set({ selectedId: id }),
  update: (fn, opts) => {
    const { draft, past, lastEdit } = get();
    if (!draft) return;
    const next = fn(draft);
    if (next === draft) return;
    const now = Date.now();
    const coalesce = !!opts?.coalesce && lastEdit?.key === opts.coalesce && now - lastEdit.at < COALESCE_MS;
    set({
      draft: next,
      past: coalesce ? past : [...past, draft].slice(-HISTORY_LIMIT),
      future: [],
      lastEdit: opts?.coalesce ? { key: opts.coalesce, at: now } : null,
    });
  },
  undo: () => {
    const { draft, past, future } = get();
    if (!draft || past.length === 0) return;
    const previous = past[past.length - 1];
    set({ draft: previous, past: past.slice(0, -1), future: [draft, ...future], lastEdit: null });
  },
  redo: () => {
    const { draft, past, future } = get();
    if (!draft || future.length === 0) return;
    const [next, ...rest] = future;
    set({ draft: next, past: [...past, draft], future: rest, lastEdit: null });
  },
  markSaved: (doc, saved) => set({ doc, savedKey: draftKey(saved) }),
  setDoc: (patch) => {
    const { doc } = get();
    if (doc) set({ doc: { ...doc, ...patch } });
  },
  reset: () =>
    set({
      doc: null,
      draft: null,
      savedKey: "",
      selectedId: null,
      past: [],
      future: [],
      lastEdit: null,
      saving: false,
    }),
}));

export function useIsDirty(): boolean {
  return useEditorStore((state) => !!state.draft && draftKey(state.draft) !== state.savedKey);
}
