"use client";

import { Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui";
import type { Schemas } from "@/lib/api/client";

import type { BannerTiming } from "./draft";
import { CheckField, NumberField, PanelSection, SelectField } from "./fields";
import { TIMING_MODE_LABELS } from "./labels";

const MODE_OPTIONS = (Object.keys(TIMING_MODE_LABELS) as Array<Schemas["TimingMode"]>).map((value) => ({
  value,
  label: TIMING_MODE_LABELS[value],
}));

/**
 * Тайминг группы «с креативом»: импульс в окне 40–65% ролика + финальный повтор (research §7.2),
 * весь ролик или свои окна. Числа — гипотеза для A/B, сервер проверит пределы.
 */
export function TimingPanel({
  timing,
  duration,
  readOnly,
  onChange,
}: {
  timing: BannerTiming;
  duration: number;
  readOnly: boolean;
  onChange: (patch: Partial<BannerTiming>, coalesce?: string) => void;
}) {
  const [lo, hi] = timing.impulse_window_pct;
  const windows = timing.windows ?? [];
  return (
    <PanelSection title="Тайминг креатива" testId="timing-panel">
      <SelectField
        label="Режим"
        value={timing.mode}
        options={MODE_OPTIONS}
        disabled={readOnly}
        testId="timing-mode"
        onChange={(mode) => {
          if (mode === "windows" && windows.length === 0) {
            const start = Math.round(duration * lo * 10) / 10;
            onChange({ mode, windows: [{ start_sec: start, end_sec: Math.min(duration, start + 4) }] });
          } else onChange({ mode });
        }}
      />
      {timing.mode === "impulse" ? (
        <>
          <div className="grid grid-cols-2 gap-1.5">
            <NumberField
              label="Тишина хука"
              value={timing.hook_silence_sec}
              min={0}
              max={30}
              step={0.5}
              suffix="с"
              disabled={readOnly}
              hint="Первые секунды без оффера"
              onChange={(hook_silence_sec) => onChange({ hook_silence_sec }, "hook")}
            />
            <NumberField
              label="Длина импульса"
              value={timing.impulse_duration_sec}
              min={0.5}
              max={30}
              step={0.5}
              suffix="с"
              disabled={readOnly}
              onChange={(impulse_duration_sec) => onChange({ impulse_duration_sec }, "imp-dur")}
            />
          </div>
          <div className="grid grid-cols-3 gap-1.5">
            <NumberField
              label="Окно с"
              value={Math.round(lo * 100)}
              min={0}
              max={99}
              suffix="%"
              disabled={readOnly}
              testId="impulse-from"
              onChange={(value) => {
                if (value / 100 < hi) onChange({ impulse_window_pct: [value / 100, hi] }, "imp-lo");
              }}
            />
            <NumberField
              label="Окно по"
              value={Math.round(hi * 100)}
              min={1}
              max={100}
              suffix="%"
              disabled={readOnly}
              testId="impulse-to"
              onChange={(value) => {
                if (value / 100 > lo) onChange({ impulse_window_pct: [lo, value / 100] }, "imp-hi");
              }}
            />
            <NumberField
              label="Не короче"
              value={timing.impulse_min_sec}
              min={0.3}
              max={30}
              step={0.5}
              suffix="с"
              disabled={readOnly}
              onChange={(impulse_min_sec) => onChange({ impulse_min_sec }, "imp-min")}
            />
          </div>
          <div className="flex items-end gap-1.5">
            <div className="flex-1">
              <CheckField
                label="Финальный повтор"
                hint="Креатив ещё раз в последние секунды"
                checked={timing.final_repeat}
                disabled={readOnly}
                testId="final-repeat"
                onChange={(final_repeat) => onChange({ final_repeat })}
              />
            </div>
            <NumberField
              label="Длина"
              value={timing.final_repeat_sec}
              min={0.5}
              max={30}
              step={0.5}
              suffix="с"
              className="w-20"
              disabled={readOnly || !timing.final_repeat}
              onChange={(final_repeat_sec) => onChange({ final_repeat_sec }, "final-sec")}
            />
          </div>
          <NumberField
            label="Слить окна ближе, чем"
            value={timing.merge_gap_sec}
            min={0}
            max={10}
            step={0.5}
            suffix="с"
            disabled={readOnly}
            onChange={(merge_gap_sec) => onChange({ merge_gap_sec }, "merge")}
          />
        </>
      ) : null}
      {timing.mode === "windows" ? (
        <div className="flex flex-col gap-1.5">
          {windows.map((w, index) => (
            <div key={index} className="grid grid-cols-[1fr_1fr_auto] items-end gap-1.5">
              <NumberField
                label={`Окно ${index + 1}: с`}
                value={w.start_sec}
                min={0}
                step={0.5}
                suffix="с"
                disabled={readOnly}
                onChange={(start_sec) =>
                  onChange(
                    { windows: windows.map((item, i) => (i === index ? { ...item, start_sec } : item)) },
                    `win-s-${index}`,
                  )
                }
              />
              <NumberField
                label="по"
                value={w.end_sec}
                min={0.1}
                step={0.5}
                suffix="с"
                disabled={readOnly}
                onChange={(end_sec) =>
                  onChange(
                    { windows: windows.map((item, i) => (i === index ? { ...item, end_sec } : item)) },
                    `win-e-${index}`,
                  )
                }
              />
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={`Удалить окно ${index + 1}`}
                disabled={readOnly || windows.length <= 1}
                onClick={() => onChange({ windows: windows.filter((_, i) => i !== index) })}
              >
                <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
              </Button>
            </div>
          ))}
          <Button
            variant="outline"
            size="xs"
            className="self-start"
            disabled={readOnly}
            onClick={() => {
              const last = windows[windows.length - 1];
              const start = last ? Math.min(duration - 1, last.end_sec + 2) : 0;
              onChange({ windows: [...windows, { start_sec: start, end_sec: Math.min(duration, start + 3) }] });
            }}
          >
            <Plus className="h-3.5 w-3.5" aria-hidden="true" />
            Окно
          </Button>
        </div>
      ) : null}
      <div className="grid grid-cols-2 gap-1.5">
        <NumberField
          label="Появление"
          value={timing.fade_in_ms}
          min={0}
          max={3000}
          step={50}
          integer
          suffix="мс"
          disabled={readOnly}
          onChange={(fade_in_ms) => onChange({ fade_in_ms }, "fade-in")}
        />
        <NumberField
          label="Исчезание"
          value={timing.fade_out_ms}
          min={0}
          max={3000}
          step={50}
          integer
          suffix="мс"
          disabled={readOnly}
          onChange={(fade_out_ms) => onChange({ fade_out_ms }, "fade-out")}
        />
      </div>
    </PanelSection>
  );
}
