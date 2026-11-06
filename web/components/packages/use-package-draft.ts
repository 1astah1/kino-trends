"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { type PackagePatch, type UploadPackage, useUpdatePackageTexts } from "@/lib/queries/packages";

import { textsFieldErrors } from "./publish-errors";

/**
 * Черновик текстов пакета с автосохранением (§7.3 «автосохранение текстов»).
 *
 * Сравниваем нормализованный черновик с последней сохранённой версией и шлём `PATCH` только
 * изменённых полей: через 800 мс тишины, на blur поля и при уходе с пакета. Сервер нормализует
 * заголовок (trim) и хэштеги (без повторов) — сравнение идёт по тем же правилам, иначе автосохранение
 * зациклится. Любая правка переводит `texts_mode` в operator (подпись приходит от сервера).
 */
export const HASHTAG_RE = /^#[^\s#]{1,100}$/u;
const DEBOUNCE_MS = 800;

export interface PackageDraft {
  title: string;
  description: string;
  hashtags: string;
  pinned: string;
}

export type SaveState =
  | { kind: "idle" }
  | { kind: "dirty" }
  | { kind: "saving" }
  | { kind: "saved"; at: number }
  | { kind: "error"; message: string };

interface Normalized {
  title: string;
  description: string;
  hashtags: string[];
  pinned: string | null;
}

/** `#таро расклад, #карта_дня` → `["#таро", "#расклад", "#карта_дня"]` (без повторов). */
export function parseHashtags(text: string): string[] {
  const out: string[] = [];
  for (const raw of text.split(/[\s,]+/u)) {
    const token = raw.trim();
    if (!token) continue;
    const tag = token.startsWith("#") ? token : `#${token}`;
    if (!out.includes(tag)) out.push(tag);
  }
  return out;
}

export function draftOf(pkg: UploadPackage): PackageDraft {
  return {
    title: pkg.texts.title,
    description: pkg.texts.description,
    hashtags: pkg.texts.hashtags.join(" "),
    pinned: pkg.texts.pinned_comment ?? "",
  };
}

function normalize(draft: PackageDraft): Normalized {
  return {
    title: draft.title.trim(),
    description: draft.description,
    hashtags: parseHashtags(draft.hashtags),
    pinned: draft.pinned.trim() ? draft.pinned : null,
  };
}

function normalizedOf(pkg: UploadPackage): Normalized {
  return {
    title: pkg.texts.title.trim(),
    description: pkg.texts.description,
    hashtags: pkg.texts.hashtags,
    pinned: pkg.texts.pinned_comment || null,
  };
}

/** Локальные проверки до запроса (те же, что у сервера), чтобы не слать заведомый 422. */
export function draftErrors(pkg: UploadPackage, draft: PackageDraft): Record<string, string> {
  const errors: Record<string, string> = {};
  const n = normalize(draft);
  if (!n.title) errors.title = "Заголовок не может быть пустым";
  else if ([...n.title].length > pkg.texts.title_max)
    errors.title = `Заголовок длиннее ${pkg.texts.title_max} символов`;
  if ([...n.description].length > pkg.texts.description_max) {
    errors.description = `Описание длиннее ${pkg.texts.description_max} символов`;
  }
  const attribution = (pkg.texts.attribution_text ?? "").trim();
  if (attribution && !n.description.includes(attribution)) {
    errors.description = "Строка атрибуции источника обязана остаться в описании (чужие права, CC BY)";
  }
  const bad = n.hashtags.filter((tag) => !HASHTAG_RE.test(tag));
  if (bad.length) errors.hashtags = `Хэштег — «#слово» без пробелов и лишних #: ${bad.join(" ")}`;
  else if (n.hashtags.length > 15) errors.hashtags = `Не больше 15 хэштегов (сейчас ${n.hashtags.length})`;
  if (draft.pinned.length > 10000) errors.pinned_comment = "Комментарий длиннее 10 000 символов";
  return errors;
}

function diff(saved: Normalized, next: Normalized): PackagePatch {
  const patch: PackagePatch = {};
  if (next.title !== saved.title) patch.title = next.title;
  if (next.description !== saved.description) patch.description = next.description;
  if (next.hashtags.join(" ") !== saved.hashtags.join(" ")) patch.hashtags = next.hashtags;
  if (next.pinned !== saved.pinned) patch.pinned_comment = next.pinned ?? "";
  return patch;
}

export function usePackageDraft(pkg: UploadPackage, editable: boolean) {
  const [draft, setDraft] = useState<PackageDraft>(() => draftOf(pkg));
  const [state, setState] = useState<SaveState>({ kind: "idle" });
  const [serverErrors, setServerErrors] = useState<Record<string, string>>({});
  const saved = useRef<Normalized>(normalizedOf(pkg));
  const latest = useRef(draft);
  useEffect(() => {
    latest.current = draft;
  }, [draft]);
  const timer = useRef<number | null>(null);
  const mutation = useUpdatePackageTexts(pkg.id);
  const mutate = mutation.mutate;

  const localErrors = useMemo(() => draftErrors(pkg, draft), [pkg, draft]);
  const errors = useMemo(() => ({ ...serverErrors, ...localErrors }), [serverErrors, localErrors]);

  const save = useCallback(() => {
    if (timer.current) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
    if (!editable) return;
    const current = latest.current;
    if (Object.keys(draftErrors(pkg, current)).length) return;
    const patch = diff(saved.current, normalize(current));
    if (!Object.keys(patch).length) return;
    setState({ kind: "saving" });
    mutate(patch, {
      onSuccess: (next) => {
        saved.current = normalizedOf(next);
        setServerErrors({});
        const stillDirty = Object.keys(diff(saved.current, normalize(latest.current))).length > 0;
        setState(stillDirty ? { kind: "dirty" } : { kind: "saved", at: Date.now() });
      },
      onError: (error) => {
        const fields = textsFieldErrors(error);
        setServerErrors(fields);
        setState({
          kind: "error",
          message: Object.values(fields)[0] ?? (error instanceof Error ? error.message : "Не удалось сохранить"),
        });
      },
    });
  }, [editable, mutate, pkg]);

  const setField = useCallback(
    (field: keyof PackageDraft, value: string) => {
      latest.current = { ...latest.current, [field]: value };
      setDraft((prev) => ({ ...prev, [field]: value }));
      setServerErrors((prev) => {
        if (!Object.keys(prev).length) return prev;
        const next = { ...prev };
        delete next[field === "pinned" ? "pinned_comment" : field];
        return next;
      });
      setState({ kind: "dirty" });
      if (timer.current) window.clearTimeout(timer.current);
      timer.current = window.setTimeout(save, DEBOUNCE_MS);
    },
    [save],
  );

  // Уход с пакета: несохранённое не теряем (мутация доживёт и без компонента).
  const saveRef = useRef(save);
  useEffect(() => {
    saveRef.current = save;
  }, [save]);
  useEffect(() => () => saveRef.current(), []);

  const normalized = useMemo(() => normalize(draft), [draft]);
  return { draft, normalized, setField, flush: save, state, errors, saving: mutation.isPending };
}
