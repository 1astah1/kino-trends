"use client";

import { CircleCheck, LayoutTemplate, Loader2, RotateCcw, Sparkles, TriangleAlert, X } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { SectionError } from "@/components/common/section-error";
import { Badge, Button, Callout, PillToggle, Skeleton } from "@/components/ui";
import { ConflictError, errorMessage } from "@/lib/api/errors";
import { cn } from "@/lib/cn";
import { type JobLayout, useJobLayout, useSaveJobLayout } from "@/lib/queries/layout";

import {
  applyDraft,
  applyPreset,
  conflictLabel,
  type Draft,
  draftFromLayout,
  overrideFromDraft,
  PRESETS,
  sameDraft,
  SCALE_MAX,
  SCALE_MIN,
  SCALE_STEP,
  type Selection,
  SELECTION_LABELS,
  setScale,
} from "./layout-logic";
import { LayoutStage } from "./layout-stage";

const RENDER_ZONE_LABELS: Record<string, string> = {
  source_zone: "субтитры",
  author_zone: "реплики автора",
  title_zone: "заголовок",
  onscreen_zone: "тезисы",
};

/** `effective.adjustments` сервера → по-русски: что расчёт подвинул сам. */
export function describeAdjustment(code: string): string {
  const [key, value = ""] = code.split(":");
  const range = value.replace("->", " → ");
  switch (key) {
    case "banner_clamped_to_safe_zone":
      return "баннер вписан в safe zone";
    case "banner_scaled_to_safe_zone":
      return `баннер уменьшен до safe zone (×${value || "?"})`;
    case "captions":
      return `субтитры: низ ${range}`;
    case "title":
      return `заголовок: верх ${range}`;
    case "onscreen":
      return `тезисы: верх ${range}`;
    case "title_zone":
      return `заголовок уведён из-под баннера: ${range}`;
    case "onscreen_zone":
      return `тезисы уведены из-под баннера: ${range}`;
    default:
      return code;
  }
}

const PLAYER_BOX =
  "relative mx-auto aspect-[9/16] w-full max-w-[min(100%,calc((100dvh_-_16rem)_*_0.5625))] min-w-[220px] lg:max-w-[min(100%,calc((100dvh_-_21rem)_*_0.5625))]";

export interface LayoutEditorProps {
  jobId: number;
  poster: string | null | undefined;
  canOperate: boolean;
  showDeadZones: boolean;
  onClose: () => void;
}

/**
 * Режим «Раскладка» приёмки (§6.3.7): оператор двигает баннер и текстовые зоны поверх постера, видит
 * пересечения, применяет пресеты и отправляет ролик на перерендер со стадии render (`PUT /jobs/{id}/layout`).
 * Черновик считается локально от авто-раскладки сервера; после сохранения показывается расчёт сервера.
 */
export function LayoutEditor({ jobId, poster, canOperate, showDeadZones, onClose }: LayoutEditorProps) {
  const query = useJobLayout(jobId);
  const save = useSaveJobLayout(jobId);
  const data = query.data;
  const saved = useMemo(() => (data ? draftFromLayout(data) : null), [data]);

  const [baseline, setBaseline] = useState<Draft | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [selection, setSelection] = useState<Selection | null>(null);
  if (saved && (!baseline || !sameDraft(saved, baseline))) {
    // Сервер прислал новое сохранённое состояние: нетронутый черновик подхватывает его.
    const dirty = draft !== null && baseline !== null && !sameDraft(draft, baseline);
    setBaseline(saved);
    if (!dirty) setDraft(saved);
  }

  const preview = useMemo(() => (data && draft ? applyDraft(data, draft) : null), [data, draft]);
  const dirty = draft !== null && baseline !== null && !sameDraft(draft, baseline);
  const manual = draft?.mode === "manual";
  const readOnly = !canOperate;
  const pending = save.isPending;

  const update = (next: Draft) => {
    if (readOnly) return;
    setDraft(next);
  };

  const submit = async (rerender: boolean) => {
    if (!draft || !data) return;
    try {
      const res = await save.mutateAsync({ layout: overrideFromDraft(draft), rerender });
      if (res.rerender_started) {
        toast.success("Раскладка применена, ролик перерендеривается", {
          description: "Повтор со стадии render: приёмка покажет ролик снова, когда рендер закончится.",
        });
      } else {
        toast.success("Раскладка сохранена", {
          description: rerender
            ? (res.rerender_hint ?? "Применится при ближайшем рендере.")
            : "Применится при следующем рендере ролика.",
        });
      }
    } catch (error) {
      if (error instanceof ConflictError) {
        toast.warning("Раскладка не применена", { description: error.message });
        return;
      }
      toast.error("Не сохранено", { description: errorMessage(error) });
    }
  };

  const header = (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="flex min-w-0 items-center gap-2">
        <LayoutTemplate className="h-4 w-4 text-fg-muted" aria-hidden="true" />
        <span className="text-sm font-medium text-fg">Раскладка</span>
        {draft ? <Badge variant={manual ? "human" : "neutral"}>{manual ? "вручную" : "авто"}</Badge> : null}
        {dirty ? (
          <Badge variant="warning" outline>
            не сохранено
          </Badge>
        ) : null}
      </div>
      <Button variant="ghost" size="sm" onClick={onClose} aria-label="Закрыть редактор раскладки">
        <X className="h-3.5 w-3.5" aria-hidden="true" />
        Закрыть
      </Button>
    </div>
  );

  return (
    <div className="flex flex-col gap-2" data-testid="layout-editor" data-dirty={dirty ? "true" : "false"}>
      {header}
      <div className={PLAYER_BOX}>
        {query.isPending ? (
          <Skeleton className="absolute inset-0 rounded-none bg-video" />
        ) : query.isError || !data || !draft || !preview ? (
          <div className="absolute inset-0 flex items-center bg-video p-3">
            <SectionError
              error={query.error}
              onRetry={() => void query.refetch()}
              title="Раскладка не загрузилась"
              className="w-full"
            />
          </div>
        ) : (
          <div className="absolute inset-0">
            <LayoutStage
              layout={data}
              draft={draft}
              draftLayout={preview}
              selection={selection}
              poster={poster}
              readOnly={readOnly}
              showDeadZones={showDeadZones}
              onSelect={setSelection}
              onDraftChange={update}
            />
          </div>
        )}
      </div>
      {data && draft && preview ? (
        <LayoutPanel
          layout={data}
          draft={draft}
          preview={preview}
          selection={selection}
          dirty={dirty}
          readOnly={readOnly}
          pending={pending}
          onSelect={setSelection}
          onDraft={update}
          onReset={() => baseline && setDraft(baseline)}
          onSubmit={(rerender) => void submit(rerender)}
        />
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------- панель

function LayoutPanel({
  layout,
  draft,
  preview,
  selection,
  dirty,
  readOnly,
  pending,
  onSelect,
  onDraft,
  onReset,
  onSubmit,
}: {
  layout: JobLayout;
  draft: Draft;
  preview: ReturnType<typeof applyDraft>;
  selection: Selection | null;
  dirty: boolean;
  readOnly: boolean;
  pending: boolean;
  onSelect: (selection: Selection | null) => void;
  onDraft: (draft: Draft) => void;
  onReset: () => void;
  onSubmit: (rerender: boolean) => void;
}) {
  const hasBanner = layout.auto.block !== null;
  const manual = draft.mode === "manual";
  const selectable: Selection[] = hasBanner
    ? ["banner", "captions", "title", "onscreen"]
    : ["captions", "title", "onscreen"];
  const current = selection ?? selectable[0] ?? "captions";
  const selectedRect = current === "banner" ? preview.block : preview[current];
  const rendered = layout.rendered;
  const renderedOverlap = rendered?.overlap_zones ?? [];
  const serverAdjustments = !dirty && manual ? layout.effective.adjustments : [];
  const canRerender = !readOnly && layout.rerender_allowed && !pending;

  return (
    <div
      className="grid gap-3 rounded-md border border-border bg-surface p-3 text-sm md:grid-cols-2"
      data-testid="layout-panel"
    >
      <div className="flex flex-col gap-3">
        <div className="flex flex-col gap-1.5">
          <span className="font-mono text-2xs tracking-wider text-fg-muted uppercase">Элемент</span>
          <PillToggle
            label="Что двигать"
            options={selectable.map((value) => ({ value, label: SELECTION_LABELS[value] }))}
            value={current}
            onChange={(value) => onSelect(value as Selection)}
          />
          <p className="font-mono text-xs text-fg-muted tabular">
            {selectedRect ? `y ${selectedRect.y1}–${selectedRect.y2} · x ${selectedRect.x1}–${selectedRect.x2}` : "—"}
            {current === "banner" && manual ? ` · сдвиг ${draft.dx}, ${draft.dy}` : ""}
          </p>
        </div>

        {hasBanner ? (
          <label className="flex flex-col gap-1">
            <span className="flex items-center justify-between font-mono text-2xs tracking-wider text-fg-muted uppercase">
              Масштаб баннера
              <span className="text-fg tabular">×{draft.scale.toFixed(2)}</span>
            </span>
            <input
              type="range"
              min={SCALE_MIN}
              max={SCALE_MAX}
              step={SCALE_STEP}
              value={draft.scale}
              disabled={readOnly}
              onChange={(event) => onDraft(setScale(draft, Number(event.target.value)))}
              className="accent-[var(--color-human)]"
              aria-label="Масштаб блока баннера"
            />
          </label>
        ) : (
          <p className="text-xs text-fg-muted">Баннер не назначен: двигаются только субтитры и текст.</p>
        )}

        <div className="flex flex-col gap-1.5">
          <span className="font-mono text-2xs tracking-wider text-fg-muted uppercase">Пресеты</span>
          <div className="flex flex-wrap gap-1.5">
            {PRESETS.map((preset) => (
              <Button
                key={preset.id}
                variant="outline"
                size="xs"
                disabled={readOnly}
                title={preset.hint}
                onClick={() => {
                  onDraft(applyPreset(layout, draft, preset.id));
                  onSelect("banner");
                }}
              >
                {preset.label}
              </Button>
            ))}
          </div>
        </div>

        <div className="flex flex-wrap gap-1.5">
          <Button
            variant="outline"
            size="xs"
            disabled={readOnly || !manual}
            onClick={() => onDraft({ ...draft, mode: "auto" })}
            title="Вернуть автоматический расчёт рендера"
          >
            <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
            Авто
          </Button>
          <Button variant="outline" size="xs" disabled={!dirty} onClick={onReset} title="Вернуть сохранённую раскладку">
            <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
            Сбросить
          </Button>
        </div>
        <p className="text-2xs text-fg-muted">
          Перетаскивайте прямоугольники на кадре. Стрелки — 1 px, Shift — 10 px, колесо и +/− над баннером — масштаб,
          Esc — снять выбор.
        </p>
      </div>

      <div className="flex flex-col gap-3">
        <div className="flex flex-col gap-1.5" data-testid="layout-conflicts">
          <span className="font-mono text-2xs tracking-wider text-fg-muted uppercase">Пересечения</span>
          {preview.conflicts.length === 0 ? (
            <p className="inline-flex items-center gap-1.5 text-positive">
              <CircleCheck className="h-4 w-4" aria-hidden="true" />
              Ничего не налезает
            </p>
          ) : (
            <ul className="flex flex-col gap-1">
              {preview.conflicts.map((conflict) => {
                const subject = preview.rects.find((r) => r.id === conflict.a);
                const text = conflictLabel(conflict, preview.rects, conflict.a);
                return (
                  <li key={`${conflict.a}-${conflict.b}`} className="inline-flex items-start gap-1.5 text-negative">
                    <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                    <span>
                      <span className="font-medium">{subject?.label ?? conflict.a}</span> {text}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {serverAdjustments.length ? (
          <p className="text-xs text-fg-muted">
            Расчёт подвинул: {serverAdjustments.map(describeAdjustment).join("; ")}.
          </p>
        ) : null}

        {renderedOverlap.length ? (
          <Callout variant="warning" title="В последнем рендере текст налезал на баннер">
            {renderedOverlap.map((zone) => RENDER_ZONE_LABELS[zone] ?? zone).join(", ")}
            {rendered?.manual ? " (раскладка была ручная)." : "."} Поправьте раскладку и перерендерите.
          </Callout>
        ) : null}

        <div className="mt-auto flex flex-col gap-1.5">
          <div className="flex flex-wrap gap-1.5">
            <Button
              variant="human"
              size="sm"
              disabled={!canRerender}
              title={
                layout.rerender_allowed
                  ? "Сохранить и перерендерить со стадии render"
                  : (layout.rerender_hint ?? undefined)
              }
              onClick={() => onSubmit(true)}
              data-testid="layout-apply"
            >
              {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : null}
              Применить и перерендерить
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={readOnly || pending || !dirty}
              onClick={() => onSubmit(false)}
              data-testid="layout-save"
            >
              Сохранить без рендера
            </Button>
          </div>
          {!layout.rerender_allowed && layout.rerender_hint ? (
            <p className={cn("text-2xs", readOnly ? "text-fg-muted" : "text-warning")}>{layout.rerender_hint}</p>
          ) : null}
          {readOnly ? (
            <p className="text-2xs text-fg-muted">Роль «Наблюдатель»: раскладку можно только смотреть.</p>
          ) : null}
        </div>
      </div>
    </div>
  );
}
