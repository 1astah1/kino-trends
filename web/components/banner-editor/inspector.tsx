"use client";

import { Crosshair, Link2, Maximize2, ScanLine, Shrink } from "lucide-react";

import { Badge, Button } from "@/components/ui";
import type { Schemas } from "@/lib/api/client";
import { cn } from "@/lib/cn";
import type { BannerCreative } from "@/lib/queries/creatives";
import { ANCHOR_GRID, CANVAS_H, CANVAS_W, clampSlotIntoSafe, type SafeZoneProfile } from "@/lib/safe-zones";

import { isMovable } from "./canvas-stage";
import {
  type BannerLayer,
  type BannerSlot,
  effectiveTextStyle,
  type EditorDraft,
  nativeScale,
  normalizeSlot,
  SLOT_SCALE_MAX,
  slotHugCreative,
  type TextStyle,
} from "./draft";
import { LIMITS } from "./draft-issues";
import { CheckField, ColorField, NumberField, PanelSection, RangeField, SelectField, TextField } from "./fields";
import { useBannerFontFaces } from "./fonts";
import {
  ALIGN_LABELS,
  ANCHOR_LABELS,
  ATTACH_LABELS,
  CREATIVE_FORMAT_LABELS,
  FIT_LABELS,
  formatBytes,
  formatSec,
  LAYER_SHOW_LABELS,
  layerKindLabel,
} from "./labels";

const FIT_OPTIONS = (Object.keys(FIT_LABELS) as Array<Schemas["FitMode"]>).map((value) => ({
  value,
  label: FIT_LABELS[value],
}));
const SHOW_OPTIONS = (Object.keys(LAYER_SHOW_LABELS) as Array<Schemas["LayerShow"]>).map((value) => ({
  value,
  label: LAYER_SHOW_LABELS[value],
}));
const ATTACH_OPTIONS = (Object.keys(ATTACH_LABELS) as Array<Schemas["TextAttach"]>).map((value) => ({
  value,
  label: ATTACH_LABELS[value],
}));
const ALIGN_OPTIONS = (Object.keys(ALIGN_LABELS) as Array<Schemas["TextAlign"]>).map((value) => ({
  value,
  label: ALIGN_LABELS[value],
}));

export function AnchorGrid({
  value,
  onChange,
  disabled,
}: {
  value: Schemas["Anchor"];
  onChange: (anchor: Schemas["Anchor"]) => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-2xs text-fg-muted">Якорь в слоте</span>
      <div
        role="radiogroup"
        aria-label="Якорь в слоте"
        className="grid w-[72px] grid-cols-3 gap-0.5 rounded-md border border-border p-0.5"
      >
        {ANCHOR_GRID.map((anchor) => {
          const active = anchor === value;
          return (
            <button
              key={anchor}
              type="button"
              role="radio"
              aria-checked={active}
              aria-label={ANCHOR_LABELS[anchor]}
              title={ANCHOR_LABELS[anchor]}
              disabled={disabled}
              onClick={() => onChange(anchor)}
              className={cn(
                "flex h-5 items-center justify-center rounded-sm transition-colors duration-150",
                active ? "bg-accent" : "hover:bg-surface-2",
              )}
            >
              <span className={cn("h-1.5 w-1.5 rounded-full", active ? "bg-accent-fg" : "bg-fg-muted/60")} />
            </button>
          );
        })}
      </div>
    </div>
  );
}

export interface InspectorProps {
  draft: EditorDraft;
  layer: BannerLayer | undefined;
  creative: BannerCreative | undefined;
  /** Куда вписывать слот: пересечение safe zones проверяемых профилей. */
  profile: Pick<SafeZoneProfile, "safe"> | undefined;
  readOnly: boolean;
  onSlot: (patch: Partial<BannerSlot>, coalesce?: string) => void;
  onLayer: (patch: Partial<BannerLayer>, coalesce?: string) => void;
  onStyle: (patch: Partial<TextStyle>, coalesce?: string) => void;
  onReplaceCreative?: () => void;
}

/** Инспектор выбранного слоя: слот, якорь, вписывание, масштаб, прозрачность; у текста — стиль. */
export function Inspector({
  layer,
  creative,
  profile,
  readOnly,
  onSlot,
  onLayer,
  onStyle,
  onReplaceCreative,
}: InspectorProps) {
  const { fonts } = useBannerFontFaces();
  if (!layer) {
    return (
      <PanelSection title="Слой">
        <p className="text-xs text-fg-muted">Выберите слой на холсте или в списке слоёв.</p>
      </PanelSection>
    );
  }
  const slot = layer.slot;
  const isText = layer.kind !== "creative" && !(layer.kind === "micro_brand" && layer.creative_sha256);
  const attached = !isMovable(layer);
  const size = creative?.width && creative.height ? { width: creative.width, height: creative.height } : null;
  const clamped = profile ? clampSlotIntoSafe(slot, profile) : slot;
  const insideSafe = clamped === slot;
  const style = effectiveTextStyle(layer);
  const fontOptions = (fonts.length ? fonts.map((font) => font.name) : [style.font_file]).map((name) => ({
    value: name,
    label: name.replace(/\.(ttf|otf)$/i, ""),
  }));
  if (!fontOptions.some((option) => option.value === style.font_file)) {
    fontOptions.push({ value: style.font_file, label: style.font_file });
  }

  return (
    <div data-testid="inspector" data-layer-id={layer.id}>
      <PanelSection
        title={`Слой · ${layerKindLabel(layer.kind)}`}
        actions={
          <Badge variant={layer.enabled ? "accent" : "neutral"} outline>
            {layer.id}
          </Badge>
        }
      >
        <CheckField
          label="Показывать слой"
          checked={layer.enabled}
          disabled={readOnly}
          onChange={(enabled) => onLayer({ enabled })}
        />
        <SelectField
          label="Когда виден"
          value={layer.show}
          options={SHOW_OPTIONS}
          disabled={readOnly}
          onChange={(show) => onLayer({ show })}
        />

        {layer.kind === "creative" ? (
          <div className="flex flex-col gap-1 rounded-md border border-border bg-surface-2/50 p-2 text-xs">
            {creative ? (
              <>
                <span className="truncate font-medium text-fg" title={creative.name}>
                  {creative.name}
                </span>
                <span className="tabular font-mono text-2xs text-fg-muted">
                  {CREATIVE_FORMAT_LABELS[creative.source_format]} · {creative.width ?? "?"}×{creative.height ?? "?"}
                  {creative.has_alpha ? " · альфа" : ""} ·{" "}
                  {formatBytes(creative.normalized_bytes ?? creative.source_bytes)}
                  {creative.kind === "animated"
                    ? ` · ${formatSec(creative.duration_sec)} ${creative.loop ? "· петля" : ""}`
                    : ""}
                </span>
              </>
            ) : (
              <span className="text-fg-muted">Креатив не выбран</span>
            )}
            {onReplaceCreative && !readOnly ? (
              <Button variant="outline" size="xs" className="self-start" onClick={onReplaceCreative}>
                Заменить креатив
              </Button>
            ) : null}
          </div>
        ) : null}
      </PanelSection>

      <PanelSection
        title="Слот на кадре 1080×1920"
        actions={
          attached ? (
            <Badge variant="machine">
              <Link2 className="h-3 w-3" aria-hidden="true" />
              привязан к креативу
            </Badge>
          ) : null
        }
      >
        {attached ? (
          <p className="text-2xs text-fg-muted">
            Позиция считается от креатива ({ATTACH_LABELS[layer.attach].toLowerCase()}). Чтобы двигать слой свободно,
            выберите «{ATTACH_LABELS.none}» ниже.
          </p>
        ) : null}
        <div className="grid grid-cols-4 gap-1.5">
          <NumberField
            label="X"
            value={slot.x}
            integer
            disabled={readOnly || attached}
            testId="slot-x"
            onChange={(x) => onSlot({ x }, "slot-x")}
          />
          <NumberField
            label="Y"
            value={slot.y}
            integer
            disabled={readOnly || attached}
            testId="slot-y"
            onChange={(y) => onSlot({ y }, "slot-y")}
          />
          <NumberField
            label="Ш"
            value={slot.w}
            min={1}
            max={CANVAS_W * 2}
            integer
            disabled={readOnly}
            testId="slot-w"
            onChange={(w) => onSlot({ w }, "slot-w")}
          />
          <NumberField
            label="В"
            value={slot.h}
            min={1}
            max={CANVAS_H * 2}
            integer
            disabled={readOnly}
            testId="slot-h"
            onChange={(h) => onSlot({ h }, "slot-h")}
          />
        </div>
        {profile && !attached ? (
          <div className="flex flex-wrap items-center gap-1.5">
            <Button
              variant={insideSafe ? "ghost" : "outline"}
              size="xs"
              disabled={readOnly || insideSafe}
              onClick={() => onSlot(normalizeSlot(clamped))}
              title="Сдвинуть и при необходимости уменьшить слот, чтобы он целиком был в безопасной зоне"
            >
              <Crosshair className="h-3.5 w-3.5" aria-hidden="true" />
              {insideSafe ? "В безопасной зоне" : "Вписать в безопасную зону"}
            </Button>
            {size && layer.kind === "creative" ? (
              <Button
                variant="ghost"
                size="xs"
                disabled={readOnly}
                onClick={() => onSlot(slotHugCreative(slot, size))}
                title="Рамка слота по видимому креативу"
              >
                <Shrink className="h-3.5 w-3.5" aria-hidden="true" />
                Слот по креативу
              </Button>
            ) : null}
          </div>
        ) : null}
        <div className="flex items-end gap-3">
          <AnchorGrid value={slot.anchor} disabled={readOnly} onChange={(anchor) => onSlot({ anchor })} />
          <div className="flex min-w-0 flex-1 flex-col gap-1.5">
            {layer.kind === "creative" || layer.creative_sha256 ? (
              <SelectField
                label="Вписывание"
                value={slot.fit}
                options={FIT_OPTIONS}
                disabled={readOnly}
                onChange={(fit) => onSlot({ fit })}
              />
            ) : null}
            <CheckField
              label="Обрезать по слоту"
              checked={slot.clip_to_slot}
              disabled={readOnly}
              onChange={(clip_to_slot) => onSlot({ clip_to_slot })}
            />
          </div>
        </div>
        {layer.kind === "creative" || layer.creative_sha256 ? (
          <div className="flex items-end gap-1.5">
            <div className="min-w-0 flex-1">
              <RangeField
                label="Масштаб поверх вписывания"
                value={slot.scale}
                min={0.2}
                max={3}
                step={0.01}
                disabled={readOnly}
                testId="slot-scale"
                format={(value) => `×${value.toFixed(2).replace(".", ",")}`}
                onChange={(scale) => onSlot({ scale }, "slot-scale")}
              />
            </div>
            {size ? (
              <Button
                variant="ghost"
                size="xs"
                disabled={readOnly}
                title={`Масштаб, при котором креатив ляжет пиксель в пиксель (без мыла), но не больше ×${SLOT_SCALE_MAX}`}
                onClick={() => onSlot({ scale: nativeScale(slot, size) })}
              >
                <Maximize2 className="h-3.5 w-3.5" aria-hidden="true" />
                1:1
              </Button>
            ) : null}
          </div>
        ) : null}
        <div className="grid grid-cols-2 gap-1.5">
          <NumberField
            label="Сдвиг X"
            value={slot.offset_x}
            min={-CANVAS_W}
            max={CANVAS_W}
            integer
            disabled={readOnly}
            onChange={(offset_x) => onSlot({ offset_x }, "off-x")}
          />
          <NumberField
            label="Сдвиг Y"
            value={slot.offset_y}
            min={-CANVAS_H}
            max={CANVAS_H}
            integer
            disabled={readOnly}
            onChange={(offset_y) => onSlot({ offset_y }, "off-y")}
          />
        </div>
        <RangeField
          label="Непрозрачность"
          value={slot.opacity}
          min={0}
          max={1}
          step={0.05}
          disabled={readOnly}
          format={(value) => `${Math.round(value * 100)}%`}
          onChange={(opacity) => onSlot({ opacity }, "slot-opacity")}
        />
        <div className="grid grid-cols-2 gap-1.5">
          <NumberField
            label="Скругление"
            value={slot.corner_radius_px}
            min={LIMITS.slotRadiusPx[0]}
            max={LIMITS.slotRadiusPx[1]}
            integer
            suffix="px"
            disabled={readOnly}
            onChange={(corner_radius_px) => onSlot({ corner_radius_px }, "slot-radius")}
          />
          <NumberField
            label="Порядок (z)"
            value={layer.z}
            min={LIMITS.z[0]}
            max={LIMITS.z[1]}
            integer
            disabled={readOnly}
            onChange={(z) => onLayer({ z }, "z")}
          />
        </div>
        {layer.kind === "creative" && creative?.kind === "animated" ? (
          <SelectField
            label="Повтор анимации"
            value={
              layer.loop_override === null || layer.loop_override === undefined
                ? "auto"
                : layer.loop_override
                  ? "loop"
                  : "once"
            }
            options={[
              { value: "auto", label: `Как в файле (${creative.loop ? "петля" : "один раз"})` },
              { value: "loop", label: "Петля" },
              { value: "once", label: "Один раз" },
            ]}
            disabled={readOnly}
            onChange={(value) => onLayer({ loop_override: value === "auto" ? null : value === "loop" })}
          />
        ) : null}
      </PanelSection>

      {isText ? (
        <PanelSection title="Текст">
          <TextField
            label="Текст (подстановки {code_url}, {code}, {erid}, {advertiser})"
            value={layer.text ?? ""}
            disabled={readOnly}
            maxLength={200}
            onChange={(text) => onLayer({ text }, "text")}
          />
          {layer.kind === "text_code" || layer.kind === "text_erid" ? (
            <div className="grid grid-cols-[1fr_88px] gap-1.5">
              <SelectField
                label="Привязка"
                value={layer.attach}
                options={ATTACH_OPTIONS}
                disabled={readOnly}
                onChange={(attach) => onLayer({ attach })}
              />
              <NumberField
                label="Зазор"
                value={layer.attach_gap_px}
                min={LIMITS.attachGapPx[0]}
                max={LIMITS.attachGapPx[1]}
                integer
                suffix="px"
                disabled={readOnly || layer.attach === "none"}
                onChange={(attach_gap_px) => onLayer({ attach_gap_px }, "gap")}
              />
            </div>
          ) : null}
          <div className="grid grid-cols-2 gap-1.5">
            <SelectField
              label="Шрифт"
              value={style.font_file}
              options={fontOptions}
              disabled={readOnly}
              onChange={(font_file) => onStyle({ font_file })}
            />
            <SelectField
              label="Выравнивание"
              value={style.align}
              options={ALIGN_OPTIONS}
              disabled={readOnly}
              onChange={(align) => onStyle({ align })}
            />
          </div>
          <div className="grid grid-cols-3 gap-1.5">
            <NumberField
              label="Кегль макс."
              value={style.max_font_px}
              min={LIMITS.fontPx[0]}
              max={LIMITS.fontPx[1]}
              integer
              disabled={readOnly}
              onChange={(max_font_px) => onStyle({ max_font_px }, "max-font")}
            />
            <NumberField
              label="Кегль мин."
              value={style.min_font_px}
              min={LIMITS.fontPx[0]}
              max={LIMITS.fontPx[1]}
              integer
              disabled={readOnly}
              onChange={(min_font_px) => onStyle({ min_font_px }, "min-font")}
            />
            <NumberField
              label="Строк"
              value={style.max_lines}
              min={LIMITS.maxLines[0]}
              max={LIMITS.maxLines[1]}
              integer
              disabled={readOnly}
              onChange={(max_lines) => onStyle({ max_lines }, "lines")}
            />
          </div>
          <div className="grid grid-cols-2 gap-1.5">
            <ColorField
              label="Цвет текста"
              value={style.color}
              disabled={readOnly}
              onChange={(color) => onStyle({ color }, "color")}
            />
            <ColorField
              label="Подложка"
              value={style.bg_color}
              disabled={readOnly}
              onChange={(bg_color) => onStyle({ bg_color }, "bg")}
            />
          </div>
          <RangeField
            label="Прозрачность подложки"
            value={style.bg_opacity}
            min={0}
            max={1}
            step={0.05}
            disabled={readOnly}
            format={(value) => `${Math.round(value * 100)}%`}
            onChange={(bg_opacity) => onStyle({ bg_opacity }, "bg-opacity")}
          />
          <div className="grid grid-cols-3 gap-1.5">
            <NumberField
              label="Поле X"
              value={style.padding_x}
              min={LIMITS.paddingPx[0]}
              max={LIMITS.paddingPx[1]}
              integer
              disabled={readOnly}
              onChange={(padding_x) => onStyle({ padding_x }, "pad-x")}
            />
            <NumberField
              label="Поле Y"
              value={style.padding_y}
              min={LIMITS.paddingPx[0]}
              max={LIMITS.paddingPx[1]}
              integer
              disabled={readOnly}
              onChange={(padding_y) => onStyle({ padding_y }, "pad-y")}
            />
            <NumberField
              label="Скругление"
              value={style.corner_radius_px}
              min={LIMITS.styleRadiusPx[0]}
              max={LIMITS.styleRadiusPx[1]}
              integer
              disabled={readOnly}
              onChange={(corner_radius_px) => onStyle({ corner_radius_px }, "radius")}
            />
          </div>
          <CheckField
            label="Подложка по тексту"
            hint="Иначе — во весь слот"
            checked={style.hug_text}
            disabled={readOnly}
            onChange={(hug_text) => onStyle({ hug_text })}
          />
        </PanelSection>
      ) : null}

      <p className="flex items-center gap-1.5 px-3 py-2 text-2xs text-fg-muted">
        <ScanLine className="h-3 w-3" aria-hidden="true" />
        Стрелки — 1 px, Shift — 10 px, Alt — размер, «+»/«−» — масштаб
      </p>
    </div>
  );
}
