"use client";

import { yaml } from "@codemirror/lang-yaml";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { tags as t } from "@lezer/highlight";
import CodeMirror, { EditorView } from "@uiw/react-codemirror";
import { useMemo } from "react";

/**
 * Тема редактора на токенах «Аппаратной» (app/globals.css): цвета берутся из CSS-переменных, поэтому
 * светлая и тёмная темы переключаются сами, без перерисовки расширений. Никаких внешних ресурсов —
 * CodeMirror целиком из node_modules.
 */
const consoleTheme = EditorView.theme({
  "&": {
    backgroundColor: "var(--surface)",
    color: "var(--fg)",
    fontSize: "12.5px",
  },
  "&.cm-focused": { outline: "none" },
  ".cm-scroller": {
    fontFamily: "var(--font-mono)",
    lineHeight: "1.55",
  },
  ".cm-content": { caretColor: "var(--accent)", padding: "8px 0" },
  ".cm-line": { padding: "0 12px" },
  ".cm-gutters": {
    backgroundColor: "var(--surface-2)",
    color: "var(--fg-muted)",
    borderRight: "1px solid var(--border)",
  },
  ".cm-lineNumbers .cm-gutterElement": { padding: "0 8px 0 12px", minWidth: "2.5em" },
  ".cm-activeLine": { backgroundColor: "color-mix(in oklab, var(--accent) 7%, transparent)" },
  ".cm-activeLineGutter": {
    backgroundColor: "color-mix(in oklab, var(--accent) 12%, transparent)",
    color: "var(--fg)",
  },
  ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--accent)" },
  "&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection":
    { backgroundColor: "color-mix(in oklab, var(--accent) 22%, transparent)" },
  ".cm-selectionMatch": { backgroundColor: "color-mix(in oklab, var(--human) 18%, transparent)" },
  "&.cm-focused .cm-matchingBracket": {
    backgroundColor: "color-mix(in oklab, var(--positive) 20%, transparent)",
    outline: "1px solid color-mix(in oklab, var(--positive) 50%, transparent)",
  },
  "&.cm-focused .cm-nonmatchingBracket": {
    backgroundColor: "color-mix(in oklab, var(--negative) 20%, transparent)",
  },
  ".cm-searchMatch": {
    backgroundColor: "color-mix(in oklab, var(--human) 25%, transparent)",
    outline: "1px solid color-mix(in oklab, var(--human) 60%, transparent)",
  },
  ".cm-searchMatch.cm-searchMatch-selected": {
    backgroundColor: "color-mix(in oklab, var(--human) 45%, transparent)",
  },
  ".cm-foldPlaceholder": {
    backgroundColor: "var(--surface-2)",
    border: "1px solid var(--border)",
    color: "var(--fg-muted)",
  },
  ".cm-foldGutter .cm-gutterElement": { color: "var(--fg-muted)" },
  ".cm-tooltip": {
    backgroundColor: "var(--surface-2)",
    border: "1px solid var(--border)",
    color: "var(--fg)",
    borderRadius: "6px",
  },
  ".cm-tooltip .cm-tooltip-arrow:before": { borderTopColor: "var(--border)", borderBottomColor: "var(--border)" },
  ".cm-tooltip .cm-tooltip-arrow:after": {
    borderTopColor: "var(--surface-2)",
    borderBottomColor: "var(--surface-2)",
  },
  ".cm-tooltip-autocomplete > ul > li[aria-selected]": {
    backgroundColor: "color-mix(in oklab, var(--accent) 18%, transparent)",
    color: "var(--fg)",
  },
  ".cm-panels": {
    backgroundColor: "var(--surface-2)",
    color: "var(--fg)",
    borderColor: "var(--border)",
  },
  ".cm-panels input, .cm-panels button": {
    backgroundColor: "var(--surface)",
    color: "var(--fg)",
    border: "1px solid var(--border)",
    borderRadius: "4px",
  },
  ".cm-placeholder": { color: "var(--fg-muted)" },
});

/** Подсветка YAML: ключи — циан (акцент), строки — зелёный, числа/булевы — янтарь, комментарии — приглушённые. */
const consoleHighlight = HighlightStyle.define([
  { tag: [t.propertyName, t.definition(t.propertyName)], color: "var(--accent)" },
  { tag: [t.string, t.special(t.string)], color: "var(--positive)" },
  { tag: [t.number, t.bool, t.null, t.atom], color: "var(--human)" },
  { tag: [t.keyword, t.typeName, t.labelName], color: "var(--machine)" },
  { tag: t.comment, color: "var(--fg-muted)", fontStyle: "italic" },
  { tag: [t.meta, t.separator, t.punctuation, t.squareBracket, t.brace], color: "var(--fg-muted)" },
  { tag: t.invalid, color: "var(--negative)", textDecoration: "underline wavy" },
]);

export interface YamlEditorProps {
  value: string;
  onChange?: (value: string) => void;
  readOnly?: boolean;
  height?: string;
  minHeight?: string;
  ariaLabel?: string;
  className?: string;
}

/** Редактор YAML на CodeMirror 6 (`@uiw/react-codemirror` + `@codemirror/lang-yaml`); грузится через next/dynamic. */
export function YamlEditor({
  value,
  onChange,
  readOnly = false,
  height = "480px",
  minHeight,
  ariaLabel,
  className,
}: YamlEditorProps) {
  const extensions = useMemo(
    () => [yaml(), consoleTheme, syntaxHighlighting(consoleHighlight), EditorView.lineWrapping],
    [],
  );
  return (
    <CodeMirror
      value={value}
      onChange={onChange}
      extensions={extensions}
      theme="none"
      height={height}
      minHeight={minHeight}
      readOnly={readOnly}
      editable={!readOnly}
      basicSetup={{
        lineNumbers: true,
        foldGutter: true,
        highlightActiveLine: !readOnly,
        highlightActiveLineGutter: !readOnly,
        bracketMatching: true,
        closeBrackets: true,
        autocompletion: false,
        tabSize: 2,
      }}
      indentWithTab
      aria-label={ariaLabel}
      className={className}
    />
  );
}
