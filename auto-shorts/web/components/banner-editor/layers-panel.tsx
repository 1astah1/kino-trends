"use client";

import { Eye, EyeOff, Hash, Image as ImageIcon, Link2, Megaphone, ShieldAlert, Type } from "lucide-react";

import { cn } from "@/lib/cn";
import { isVisibleAt, type TimeSpan } from "@/lib/safe-zones";

import type { BannerLayer, EditorDraft } from "./draft";
import { PanelSection } from "./fields";
import { layerKindLabel } from "./labels";

const KIND_ICON = {
  creative: ImageIcon,
  text_code: Hash,
  text_erid: ShieldAlert,
  micro_brand: Type,
  fallback_plate: Megaphone,
} as const;

/** Слои варианта сверху вниз по z: выбор, видимость в момент t, быстрый выключатель. */
export function LayersPanel({
  draft,
  selectedId,
  windows,
  t,
  offerLayerId,
  readOnly,
  onSelect,
  onToggle,
}: {
  draft: EditorDraft;
  selectedId: string | null;
  windows: TimeSpan[];
  t: number;
  offerLayerId: string | null;
  readOnly: boolean;
  onSelect: (id: string) => void;
  onToggle: (layer: BannerLayer) => void;
}) {
  const ordered = [...draft.layers].sort((a, b) => b.z - a.z);
  return (
    <PanelSection title="Слои" testId="layers-panel">
      <ul className="flex flex-col gap-0.5" aria-label="Слои варианта">
        {ordered.map((layer) => {
          const Icon = KIND_ICON[layer.kind] ?? ImageIcon;
          const selected = layer.id === selectedId;
          const inFrame = layer.enabled && isVisibleAt(layer.show, windows, t);
          const attached = layer.attach !== "none" && (layer.kind === "text_code" || layer.kind === "text_erid");
          const idle =
            (layer.kind === "creative" || layer.kind === "fallback_plate") &&
            offerLayerId !== null &&
            offerLayerId !== layer.id;
          return (
            <li key={layer.id}>
              <div
                className={cn(
                  "flex h-8 items-center gap-2 rounded-md px-2 text-xs transition-colors duration-150",
                  selected ? "bg-accent/15 text-fg" : "text-fg-muted hover:bg-surface-2",
                )}
              >
                <button
                  type="button"
                  className="flex min-w-0 flex-1 items-center gap-2 text-left"
                  onClick={() => onSelect(layer.id)}
                  aria-pressed={selected}
                  data-testid={`layer-row-${layer.id}`}
                >
                  <Icon className={cn("h-3.5 w-3.5 shrink-0", selected && "text-accent")} aria-hidden="true" />
                  <span className={cn("truncate", !layer.enabled && "line-through opacity-60")}>
                    {layerKindLabel(layer.kind)}
                  </span>
                  {attached ? (
                    <Link2 className="h-3 w-3 shrink-0 text-machine" aria-label="привязан к креативу" />
                  ) : null}
                  {idle && layer.enabled ? (
                    <span className="shrink-0 text-2xs text-fg-muted/70">не в кадре</span>
                  ) : null}
                  <span
                    className={cn(
                      "ml-auto h-1.5 w-1.5 shrink-0 rounded-full",
                      inFrame && !idle ? "bg-human" : "bg-transparent",
                    )}
                    title={inFrame ? "Виден в момент t" : "Не виден в момент t"}
                    aria-hidden="true"
                  />
                </button>
                <button
                  type="button"
                  className="shrink-0 rounded-sm p-0.5 text-fg-muted hover:text-fg disabled:opacity-40"
                  aria-label={
                    layer.enabled
                      ? `Выключить слой ${layerKindLabel(layer.kind)}`
                      : `Включить слой ${layerKindLabel(layer.kind)}`
                  }
                  aria-pressed={layer.enabled}
                  disabled={readOnly}
                  onClick={() => onToggle(layer)}
                >
                  {layer.enabled ? (
                    <Eye className="h-3.5 w-3.5" aria-hidden="true" />
                  ) : (
                    <EyeOff className="h-3.5 w-3.5" aria-hidden="true" />
                  )}
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    </PanelSection>
  );
}
