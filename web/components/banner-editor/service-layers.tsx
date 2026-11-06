"use client";

import { Hash, Megaphone, ShieldAlert, Type } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

import {
  type AdMarking,
  type BannerLayer,
  effectiveCodeUrl,
  type EditorDoc,
  type EditorDraft,
  ERID_PATTERN,
  firstLayerOf,
  renderLayerText,
} from "./draft";
import { CheckField, PanelSection, TextField } from "./fields";

type ServiceKind = "text_code" | "text_erid" | "micro_brand" | "fallback_plate";

function ServiceRow({
  icon,
  title,
  preview,
  layer,
  onToggle,
  onSelect,
  selected,
  readOnly,
  testId,
  children,
}: {
  icon: ReactNode;
  title: string;
  preview: string;
  layer: BannerLayer | undefined;
  onToggle: (on: boolean) => void;
  onSelect: () => void;
  selected: boolean;
  readOnly: boolean;
  testId: string;
  children?: ReactNode;
}) {
  const on = !!layer?.enabled;
  return (
    <div
      className={cn(
        "flex flex-col gap-1.5 rounded-md border p-2 transition-colors duration-150",
        selected ? "border-accent/60 bg-accent/5" : "border-border",
      )}
      data-testid={testId}
    >
      <div className="flex items-center gap-2">
        <input
          type="checkbox"
          className="h-3.5 w-3.5 shrink-0 accent-[var(--accent)]"
          checked={on}
          disabled={readOnly}
          aria-label={`${title}: показывать`}
          data-testid={`${testId}-toggle`}
          onChange={(event) => onToggle(event.target.checked)}
        />
        <span className="text-fg-muted" aria-hidden="true">
          {icon}
        </span>
        <button
          type="button"
          className="min-w-0 flex-1 truncate text-left text-xs font-medium text-fg hover:text-accent"
          onClick={onSelect}
          disabled={!layer}
          title="Выбрать слой на холсте"
        >
          {title}
        </button>
      </div>
      {layer ? (
        <p className="truncate pl-5 font-mono text-2xs text-fg-muted" title={preview}>
          {preview || "—"}
        </p>
      ) : null}
      {children}
    </div>
  );
}

/**
 * Служебные слои поверх креатива (решение владельца): код ролика и «Реклама · erid»; плюс микро-бренд
 * и запасная текстовая плашка — только если креатива нет.
 */
export function ServiceLayersPanel({
  draft,
  doc,
  selectedId,
  readOnly,
  onToggleKind,
  onSelect,
  onLayerText,
  onAdMarking,
  onCodeUrlTemplate,
}: {
  draft: EditorDraft;
  doc: EditorDoc | null;
  selectedId: string | null;
  readOnly: boolean;
  onToggleKind: (kind: ServiceKind, on: boolean) => void;
  onSelect: (id: string) => void;
  onLayerText: (id: string, text: string) => void;
  onAdMarking: (patch: Partial<AdMarking>) => void;
  onCodeUrlTemplate: (value: string | null) => void;
}) {
  const code = firstLayerOf(draft, "text_code");
  const erid = firstLayerOf(draft, "text_erid");
  const brand = firstLayerOf(draft, "micro_brand");
  const plate = firstLayerOf(draft, "fallback_plate");
  const hasCreative = draft.layers.some((layer) => layer.kind === "creative" && layer.enabled && layer.creative_sha256);
  const ctx = {
    codeUrlTemplate: effectiveCodeUrl(draft, doc),
    erid: draft.ad_marking.erid,
    advertiser: draft.ad_marking.advertiser_text,
  };
  const eridValue = draft.ad_marking.erid ?? "";
  const eridInvalid = eridValue !== "" && !ERID_PATTERN.test(eridValue);
  const eridMissing = draft.ad_marking.required && !eridValue;

  return (
    <PanelSection title="Служебные слои" testId="service-layers">
      <ServiceRow
        icon={<Hash className="h-3.5 w-3.5" />}
        title="Код ролика"
        preview={renderLayerText(code?.text, ctx)}
        layer={code}
        selected={!!code && code.id === selectedId}
        readOnly={readOnly}
        testId="service-code"
        onToggle={(on) => onToggleKind("text_code", on)}
        onSelect={() => code && onSelect(code.id)}
      >
        {code?.enabled ? (
          <TextField
            label="Шаблон ссылки ({code} — код ролика)"
            value={draft.code_url_template ?? ""}
            placeholder={doc?.effectiveCodeUrlTemplate ?? "u.umka.chat/{code}"}
            mono
            disabled={readOnly}
            maxLength={120}
            hint="Пусто — домен редиректора продукта"
            onChange={(value) => onCodeUrlTemplate(value.trim() ? value : null)}
          />
        ) : null}
      </ServiceRow>

      <ServiceRow
        icon={<ShieldAlert className="h-3.5 w-3.5" />}
        title="Реклама · erid"
        preview={renderLayerText(erid?.text, ctx)}
        layer={erid}
        selected={!!erid && erid.id === selectedId}
        readOnly={readOnly}
        testId="service-erid"
        onToggle={(on) => onToggleKind("text_erid", on)}
        onSelect={() => erid && onSelect(erid.id)}
      >
        <CheckField
          label="Маркировка обязательна"
          hint="38-ФЗ: без erid вариант не включится в ротацию"
          checked={draft.ad_marking.required}
          disabled={readOnly}
          testId="ad-marking-required"
          onChange={(required) => onAdMarking({ required })}
        />
        <TextField
          label="erid от ОРД"
          value={eridValue}
          mono
          disabled={readOnly}
          maxLength={64}
          invalid={eridInvalid || eridMissing}
          testId="erid-input"
          hint={
            eridInvalid
              ? "Только латиница, цифры, «-» и «_», от 4 символов"
              : eridMissing
                ? "Маркировка обязательна — впишите erid"
                : "Подставляется в {erid}"
          }
          onChange={(value) => onAdMarking({ erid: value.trim() ? value.trim() : null })}
        />
        <TextField
          label="Рекламодатель ({advertiser})"
          value={draft.ad_marking.advertiser_text ?? ""}
          disabled={readOnly}
          maxLength={120}
          onChange={(value) => onAdMarking({ advertiser_text: value.trim() ? value : null })}
        />
      </ServiceRow>

      <ServiceRow
        icon={<Type className="h-3.5 w-3.5" />}
        title="Микро-бренд"
        preview={renderLayerText(brand?.text, ctx)}
        layer={brand}
        selected={!!brand && brand.id === selectedId}
        readOnly={readOnly}
        testId="service-brand"
        onToggle={(on) => onToggleKind("micro_brand", on)}
        onSelect={() => brand && onSelect(brand.id)}
      >
        {brand?.enabled ? (
          <TextField
            label="Текст"
            value={brand.text ?? ""}
            disabled={readOnly}
            maxLength={120}
            onChange={(text) => onLayerText(brand.id, text)}
          />
        ) : null}
      </ServiceRow>

      <ServiceRow
        icon={<Megaphone className="h-3.5 w-3.5" />}
        title={hasCreative ? "Запасная плашка (если креатив пропадёт)" : "Запасная плашка — сейчас вместо креатива"}
        preview={renderLayerText(plate?.text, ctx)}
        layer={plate}
        selected={!!plate && plate.id === selectedId}
        readOnly={readOnly}
        testId="service-plate"
        onToggle={(on) => onToggleKind("fallback_plate", on)}
        onSelect={() => plate && onSelect(plate.id)}
      >
        {plate ? (
          <TextField
            label="Текст плашки"
            value={plate.text ?? ""}
            disabled={readOnly}
            maxLength={200}
            onChange={(text) => onLayerText(plate.id, text)}
          />
        ) : null}
      </ServiceRow>
    </PanelSection>
  );
}
