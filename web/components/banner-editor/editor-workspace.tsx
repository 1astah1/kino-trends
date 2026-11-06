"use client";

import { LoaderCircle, Redo2, RotateCcw, Save, Undo2 } from "lucide-react";
import Link from "next/link";
import { parseAsFloat, parseAsStringLiteral, throttle, useQueryState } from "nuqs";
import { type ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Button, Callout, Dialog, PillToggle } from "@/components/ui";
import type { Schemas } from "@/lib/api/client";
import { ConflictError, errorMessage, ValidationError } from "@/lib/api/errors";
import { cn } from "@/lib/cn";
import { type BannerValidateRequest, useBannerValidation } from "@/lib/queries/banners";
import { type BannerCreative, useCreativesBySha } from "@/lib/queries/creatives";
import { useBannerSafeZones } from "@/lib/queries/safe-zones";
import {
  computeWindows,
  type CreativeSize,
  findProfile,
  intersectSafe,
  placeVariant,
  PLATFORM_VIEWS,
  type PlatformView,
  primaryProfileId,
  validationProfileIds,
} from "@/lib/safe-zones";

import type { CanvasBackground } from "./canvas-overlays";
import { type CanvasOverlays, CanvasStage } from "./canvas-stage";
import { CreativeLibrary, CreativeUploader } from "./creative-library";
import {
  type BannerLayer,
  type BannerSlot,
  draftKey,
  effectiveCodeUrl,
  type EditorDoc,
  type EditorDraft,
  ensureServiceLayer,
  findLayer,
  patchLayer,
  patchSlot,
  patchStyle,
  placeCreative,
  toApiDraft,
} from "./draft";
import { describeValidationError, draftIssues, issuesSummary } from "./draft-issues";
import { CheckField, NumberField, PanelSection } from "./fields";
import { useBannerFontFaces } from "./fonts";
import { Inspector } from "./inspector";
import { LayersPanel } from "./layers-panel";
import { RenderFramePanel } from "./render-frame";
import { ServiceLayersPanel } from "./service-layers";
import { validationCodeLabel } from "./labels";
import { useEditorStore, useIsDirty } from "./store";
import { Timeline } from "./timeline";
import { TimingPanel } from "./timing-panel";
import { ValidationList } from "./validation-list";

const VIEW_IDS = PLATFORM_VIEWS.map((view) => view.id) as PlatformView[];
const BACKGROUNDS: Array<{ value: CanvasBackground; label: string }> = [
  { value: "sample", label: "Образец" },
  { value: "checker", label: "Шахматка" },
  { value: "black", label: "Чёрный" },
];
const AUTOSAVE_MS = 1500;
const VALIDATE_DEBOUNCE_MS = 200;

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return debounced;
}

export type SaveState = "saved" | "dirty" | "saving" | "error" | "conflict";

export interface EditorWorkspaceProps {
  /** Сохранить черновик; вернуть документ с новой версией. Ошибки пробрасываются. */
  save: (draft: EditorDraft, doc: EditorDoc) => Promise<EditorDoc>;
  /** Перечитать документ с сервера (конфликт версий). */
  reload: () => Promise<{ doc: EditorDoc; draft: EditorDraft } | null>;
  readOnly: boolean;
  /** Шапка документа: название, статус, действия. */
  header: (ctx: { saveState: SaveState; saveNow: () => Promise<boolean>; dirty: boolean }) => ReactNode;
  /** Панель вариантов A/B (только у варианта кампании). */
  variantsPanel?: (ctx: { confirmLeave: () => boolean; saveNow: () => Promise<boolean> }) => ReactNode;
  /** Текст микро-бренда по умолчанию (домен продукта). */
  microBrandText?: string;
}

/**
 * Рабочее место редактора баннера (решение владельца: баннер — загруженный креатив).
 * Слева — слои, загрузка и библиотека креативов, варианты A/B; в центре — кадр 1080×1920 с safe zones
 * площадки, зоной субтитров и дорожкой времени; справа — проверка сервера, «Кадр из рендера»,
 * инспектор слоя, служебные слои и тайминг. Автосохранение через 1,5 с с `expected_version`.
 */
export function EditorWorkspace({
  save,
  reload,
  readOnly,
  header,
  variantsPanel,
  microBrandText,
}: EditorWorkspaceProps) {
  const doc = useEditorStore((state) => state.doc);
  const draft = useEditorStore((state) => state.draft);
  const selectedId = useEditorStore((state) => state.selectedId);
  const canUndo = useEditorStore((state) => state.past.length > 0);
  const canRedo = useEditorStore((state) => state.future.length > 0);
  const { update, select, undo, redo, markSaved, load } = useEditorStore.getState();
  const dirty = useIsDirty();

  const [platformView, setPlatformView] = useQueryState(
    "platform",
    parseAsStringLiteral(VIEW_IDS).withDefault("youtube").withOptions({ history: "replace" }),
  );
  const [tParam, setTParam] = useQueryState(
    "t",
    parseAsFloat.withOptions({ history: "replace", limitUrlUpdates: throttle(300) }),
  );
  const [duration, setDuration] = useState(30);
  const [overlays, setOverlays] = useState<CanvasOverlays>({ safe: true, captions: true, ui: true, ghosts: true });
  const [background, setBackground] = useState<CanvasBackground>("sample");
  const [frameOverlay, setFrameOverlay] = useState<string | null>(null);
  const [frameOverlayOpacity, setFrameOverlayOpacity] = useState(0.5);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [saveState, setSaveState] = useState<SaveState>("saved");
  const [saveError, setSaveError] = useState<string | null>(null);
  /** Ключ черновика, который сервер отверг: автосохранение не повторяет его по кругу. */
  const [failedKey, setFailedKey] = useState<string | null>(null);

  const { loadedCount: fontsLoaded } = useBannerFontFaces();
  const safeZones = useBannerSafeZones();
  const catalog = safeZones.data;
  const profile = findProfile(catalog, primaryProfileId(platformView, catalog));
  const secondaryProfile =
    platformView === "common" ? undefined : findProfile(catalog, catalog?.default_profile ?? "common_organic");
  const clampTarget = intersectSafe([profile, secondaryProfile]);

  const shas = useMemo(
    () => (draft?.layers ?? []).map((layer) => layer.creative_sha256).filter((sha): sha is string => !!sha),
    [draft?.layers],
  );
  const creativesQuery = useCreativesBySha(shas);
  const creatives = creativesQuery.map;
  const sizes = useMemo(() => {
    const out: Record<string, CreativeSize> = {};
    for (const [sha, creative] of Object.entries(creatives)) {
      if (creative.status === "ready" && creative.width && creative.height)
        out[sha] = { width: creative.width, height: creative.height };
    }
    return out;
  }, [creatives]);
  const layout = useMemo(() => placeVariant(draft?.layers ?? [], sizes), [draft?.layers, sizes]);
  const windows = useMemo(() => (draft ? computeWindows(draft.timing, duration).windows : []), [draft, duration]);
  const defaultT = windows[0] ? Math.round(((windows[0].start_sec + windows[0].end_sec) / 2) * 10) / 10 : 1;
  const t = Math.min(duration, Math.max(0, tParam ?? defaultT));

  // ---------------------------------------------------------------- проверка на сервере (debounce)
  // Черновик вне пределов схемы на сервер не уходит (там 422 без указания поля) — список ниже.
  const localIssues = useMemo(() => draftIssues(draft), [draft]);
  const debouncedDraft = useDebounced(draft, VALIDATE_DEBOUNCE_MS);
  const validateRequest: BannerValidateRequest | null = useMemo(() => {
    if (!debouncedDraft || !doc || draftIssues(debouncedDraft).length) return null;
    return {
      draft: toApiDraft(debouncedDraft),
      campaign_id: doc.kind === "variant" ? doc.campaignId : null,
      platform: PLATFORM_VIEWS.find((view) => view.id === platformView)?.platform ?? "youtube",
      duration_sec: duration,
      profile_ids: validationProfileIds(platformView, catalog),
    };
  }, [debouncedDraft, doc, platformView, duration, catalog]);
  const creativesRevision = Object.values(creatives)
    .map((creative) => `${creative.sha256.slice(0, 12)}:${creative.status}`)
    .sort()
    .join(",");
  const validation = useBannerValidation(validateRequest, { enabled: !readOnly, revision: creativesRevision });
  const pendingValidation = validation.isFetching || draft !== debouncedDraft;

  // ---------------------------------------------------------------- сохранение
  const saveNow = useCallback(async (): Promise<boolean> => {
    const state = useEditorStore.getState();
    if (!state.draft || !state.doc || readOnly || state.saving) return false;
    if (draftKey(state.draft) === state.savedKey) return true;
    const snapshot = state.draft;
    const issues = draftIssues(snapshot);
    if (issues.length) {
      setFailedKey(draftKey(snapshot));
      setSaveState("error");
      setSaveError(issuesSummary(issues));
      return false;
    }
    state.setSaving(true);
    setSaveState("saving");
    try {
      const nextDoc = await save(snapshot, state.doc);
      markSaved(nextDoc, snapshot);
      setFailedKey(null);
      setSaveError(null);
      setSaveState("saved");
      return true;
    } catch (error) {
      setFailedKey(draftKey(snapshot));
      if (error instanceof ConflictError) {
        setSaveState("conflict");
        setSaveError("Вариант изменили в другой вкладке или другой оператор.");
      } else {
        setSaveState("error");
        setSaveError(saveErrorMessage(error, snapshot));
      }
      return false;
    } finally {
      useEditorStore.getState().setSaving(false);
    }
  }, [markSaved, readOnly, save]);

  const currentKey = draftKey(draft);
  useEffect(() => {
    if (readOnly || !dirty || saveState === "conflict" || saveState === "saving") return;
    if (failedKey === currentKey) return;
    const id = setTimeout(() => void saveNow(), AUTOSAVE_MS);
    return () => clearTimeout(id);
  }, [currentKey, dirty, failedKey, readOnly, saveNow, saveState]);
  // Что показать: «сохраняю» и «конфликт» — как есть; ошибка — пока черновик тот же, что отверг сервер.
  const shownState: SaveState =
    saveState === "saving" || saveState === "conflict"
      ? saveState
      : saveState === "error" && failedKey === currentKey
        ? "error"
        : dirty
          ? "dirty"
          : "saved";

  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);

  // Ctrl+S — сохранить, Ctrl+Z / Ctrl+Shift+Z — отмена и повтор (вне полей ввода).
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const mod = event.ctrlKey || event.metaKey;
      if (!mod) return;
      const key = event.key.toLowerCase();
      if (key === "s") {
        event.preventDefault();
        void saveNow();
        return;
      }
      const target = event.target as HTMLElement | null;
      if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return;
      if (key === "z" && !event.shiftKey) {
        event.preventDefault();
        undo();
      } else if ((key === "z" && event.shiftKey) || key === "y") {
        event.preventDefault();
        redo();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [redo, saveNow, undo]);

  const resolveConflict = async (mode: "reload" | "overwrite") => {
    const fresh = await reload();
    if (!fresh) return;
    if (mode === "reload") {
      load(fresh.doc, fresh.draft);
      toast.info("Загружена актуальная версия", { description: "Ваши несохранённые правки отброшены." });
    } else {
      const local = useEditorStore.getState().draft;
      useEditorStore.setState({ doc: fresh.doc });
      if (local) useEditorStore.setState({ savedKey: draftKey(fresh.draft) });
    }
    setFailedKey(null);
    setSaveError(null);
    setSaveState("saved");
  };

  const confirmLeave = useCallback(() => {
    if (!useEditorStore.getState().draft) return true;
    const state = useEditorStore.getState();
    if (draftKey(state.draft) === state.savedKey) return true;
    return window.confirm("Есть несохранённые правки. Уйти без сохранения?");
  }, []);

  if (!draft || !doc) return null;

  const selectedLayer = findLayer(draft, selectedId);
  const selectedCreative = selectedLayer?.creative_sha256 ? creatives[selectedLayer.creative_sha256] : undefined;
  const textContext = {
    codeUrlTemplate: effectiveCodeUrl(draft, doc),
    erid: draft.ad_marking.erid,
    advertiser: draft.ad_marking.advertiser_text,
  };
  const layerKinds = Object.fromEntries(draft.layers.map((layer) => [layer.id, layer.kind]));
  const hasPersistent = draft.layers.some((layer) => layer.enabled && layer.show === "persistent");

  const onSlotCommit = (layerId: string, slot: BannerSlot, source: "drag" | "keyboard") => {
    update((d) => patchSlot(d, layerId, slot), source === "keyboard" ? { coalesce: `kbd-${layerId}` } : undefined);
  };
  const onSlot = (patch: Partial<BannerSlot>, coalesce?: string) => {
    if (!selectedLayer) return;
    update(
      (d) => patchSlot(d, selectedLayer.id, patch),
      coalesce ? { coalesce: `${selectedLayer.id}-${coalesce}` } : undefined,
    );
  };
  const onLayer = (patch: Partial<BannerLayer>, coalesce?: string) => {
    if (!selectedLayer) return;
    update(
      (d) => patchLayer(d, selectedLayer.id, patch),
      coalesce ? { coalesce: `${selectedLayer.id}-${coalesce}` } : undefined,
    );
  };
  const onStyle = (patch: Partial<Schemas["TextStyle"]>, coalesce?: string) => {
    if (!selectedLayer) return;
    update(
      (d) => patchStyle(d, selectedLayer.id, patch),
      coalesce ? { coalesce: `${selectedLayer.id}-style-${coalesce}` } : undefined,
    );
  };
  const pickCreative = (creative: BannerCreative) => {
    if (readOnly) return;
    let layerId = "";
    update((d) => {
      const result = placeCreative(d, creative.sha256);
      layerId = result.layerId;
      return result.draft;
    });
    if (layerId) select(layerId);
    setPickerOpen(false);
    if (creative.status !== "ready")
      toast.info("Креатив ещё обрабатывается", { description: "Появится на холсте, когда будет готов." });
  };
  const onToggleKind = (kind: "text_code" | "text_erid" | "micro_brand" | "fallback_plate", on: boolean) => {
    let targetId = "";
    update((d) => {
      const ensured = ensureServiceLayer(d, kind, microBrandText);
      targetId = ensured.layer.id;
      return patchLayer(ensured.draft, ensured.layer.id, { enabled: on });
    });
    if (on && targetId) select(targetId);
  };

  const buildPreviewRequest = ({
    moment,
    background: bg,
    size,
    t: at,
  }: {
    moment: Schemas["PreviewMoment"];
    background: "sample" | "black" | "checker";
    size: Schemas["PreviewSize"];
    t: number;
  }) => {
    const current = useEditorStore.getState().draft ?? draft;
    const issues = draftIssues(current);
    if (issues.length) {
      toast.error("Кадр не получился", { description: issuesSummary(issues) });
      return null;
    }
    return {
      draft: toApiDraft(current),
      campaign_id: doc.kind === "variant" ? doc.campaignId : null,
      platform: PLATFORM_VIEWS.find((view) => view.id === platformView)?.platform ?? "youtube",
      duration_sec: duration,
      profile_id: primaryProfileId(platformView, catalog),
      moment,
      t_sec: moment === "at_time" ? at : null,
      background: bg,
      size,
      code: null,
    };
  };

  return (
    <div
      className="flex h-full min-h-0 flex-col"
      data-testid="banner-editor"
      data-doc-kind={doc.kind}
      data-doc-id={doc.id}
    >
      <div className="flex flex-wrap items-center gap-2 border-b border-border bg-surface px-4 py-2">
        <div className="min-w-0 flex-1">{header({ saveState: shownState, saveNow, dirty })}</div>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Отменить (Ctrl+Z)"
            title="Отменить (Ctrl+Z)"
            disabled={!canUndo || readOnly}
            onClick={undo}
          >
            <Undo2 className="h-4 w-4" aria-hidden="true" />
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Повторить (Ctrl+Shift+Z)"
            title="Повторить (Ctrl+Shift+Z)"
            disabled={!canRedo || readOnly}
            onClick={redo}
          >
            <Redo2 className="h-4 w-4" aria-hidden="true" />
          </Button>
          <SaveIndicator state={shownState} readOnly={readOnly} />
          {readOnly ? null : (
            <Button
              size="sm"
              variant={dirty ? "default" : "outline"}
              disabled={!dirty || shownState === "saving" || shownState === "conflict"}
              onClick={() => void saveNow()}
              data-testid="save-button"
              title="Сохранить (Ctrl+S)"
            >
              <Save className="h-3.5 w-3.5" aria-hidden="true" />
              Сохранить
            </Button>
          )}
        </div>
      </div>

      {shownState === "conflict" ? (
        <Callout
          variant="warning"
          title="Версия устарела"
          className="mx-4 mt-2"
          action={
            <div className="flex gap-1.5">
              <Button size="sm" variant="outline" onClick={() => void resolveConflict("reload")}>
                <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
                Перечитать
              </Button>
              <Button size="sm" onClick={() => void resolveConflict("overwrite")}>
                Сохранить поверх
              </Button>
            </div>
          }
        >
          {saveError} Перечитайте вариант или сохраните свои правки поверх свежей версии.
        </Callout>
      ) : shownState === "error" && saveError ? (
        <Callout variant="negative" title="Не сохранено" className="mx-4 mt-2">
          {saveError}
        </Callout>
      ) : null}

      <div className="grid min-h-0 flex-1 grid-cols-1 overflow-y-auto lg:grid-cols-[264px_minmax(0,1fr)_328px] lg:overflow-hidden">
        {/* Левая колонка */}
        <aside className="min-h-0 border-border lg:overflow-y-auto lg:border-r" aria-label="Слои и креативы">
          <LayersPanel
            draft={draft}
            selectedId={selectedId}
            windows={windows}
            t={t}
            offerLayerId={layout.offerLayerId}
            readOnly={readOnly}
            onSelect={select}
            onToggle={(layer) => update((d) => patchLayer(d, layer.id, { enabled: !layer.enabled }))}
          />
          <PanelSection title="Креативы">
            {readOnly ? null : (
              <CreativeUploader compact productId={doc.productId} onUploaded={(creative) => pickCreative(creative)} />
            )}
            <CreativeLibrary productId={doc.productId} currentShas={shas} onPick={pickCreative} readOnly={readOnly} />
          </PanelSection>
          {variantsPanel ? variantsPanel({ confirmLeave, saveNow }) : null}
        </aside>

        {/* Центр: холст и дорожка */}
        <section className="flex min-h-[640px] min-w-0 flex-col lg:min-h-0" aria-label="Кадр 1080×1920">
          <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
            <PillToggle
              label="Площадка"
              options={PLATFORM_VIEWS.map((view) => ({ value: view.id, label: view.label }))}
              value={platformView}
              onChange={(value) => void setPlatformView(value)}
            />
            <PillToggle label="Фон холста" options={BACKGROUNDS} value={background} onChange={setBackground} />
            <div className="ml-auto flex flex-wrap items-center gap-3">
              <CheckField
                label="Safe zones"
                checked={overlays.safe}
                onChange={(safe) => setOverlays((o) => ({ ...o, safe }))}
              />
              <CheckField
                label="Субтитры"
                checked={overlays.captions}
                onChange={(captions) => setOverlays((o) => ({ ...o, captions }))}
              />
              <CheckField
                label="Интерфейс"
                checked={overlays.ui}
                onChange={(ui) => setOverlays((o) => ({ ...o, ui }))}
              />
              <CheckField
                label="Скрытые в t"
                checked={overlays.ghosts}
                onChange={(ghosts) => setOverlays((o) => ({ ...o, ghosts }))}
              />
            </div>
          </div>
          <div className="relative min-h-0 flex-1 bg-bg p-3">
            {safeZones.isError ? (
              <p className="absolute top-3 left-3 z-10 text-xs text-negative">
                Safe zones не загрузились: {errorMessage(safeZones.error)}
              </p>
            ) : null}
            <div className="flex h-full min-h-[560px] items-center justify-center lg:min-h-0">
              <CanvasStage
                draft={draft}
                creatives={creatives}
                textContext={textContext}
                profile={profile}
                secondaryProfile={secondaryProfile}
                platformView={platformView}
                captionsZone={validation.data?.captions_zone ?? null}
                windows={windows}
                t={t}
                overlays={overlays}
                background={background}
                selectedId={selectedId}
                readOnly={readOnly}
                fontsLoaded={fontsLoaded}
                frameOverlay={frameOverlay}
                frameOverlayOpacity={frameOverlayOpacity}
                onSelect={select}
                onSlotCommit={onSlotCommit}
                onBlockedMove={() =>
                  toast.info("Слой привязан к креативу", {
                    id: "blocked-move",
                    description: "Двигайте креатив — текст поедет за ним, или смените привязку в инспекторе.",
                  })
                }
              />
            </div>
          </div>
          <div className="flex flex-col gap-2 border-t border-border px-3 py-2">
            <div className="flex items-end gap-3">
              <NumberField
                label="Длительность ролика"
                value={duration}
                min={1}
                max={180}
                step={1}
                suffix="с"
                className="w-32"
                testId="duration-input"
                onChange={(value) => setDuration(value)}
              />
              <p className="pb-1 text-2xs text-fg-muted">
                Окна показа зависят от длины ролика. Бегунок — какой момент показывает холст.
              </p>
            </div>
            <Timeline
              duration={duration}
              timing={draft.timing}
              windows={windows}
              t={t}
              hasPersistent={hasPersistent}
              onSeek={(value) => void setTParam(value)}
            />
          </div>
        </section>

        {/* Правая колонка */}
        <aside className="min-h-0 border-border lg:overflow-y-auto lg:border-l" aria-label="Проверка и настройки слоя">
          <ValidationList
            result={validation.data}
            isFetching={pendingValidation && !readOnly}
            isError={validation.isError}
            error={validation.error}
            disabled={readOnly}
            catalog={catalog}
            layerKinds={layerKinds}
            localIssues={localIssues}
            draft={draft}
            onSelectLayer={select}
            onRetry={() => void validation.refetch()}
          />
          <RenderFramePanel
            buildRequest={buildPreviewRequest}
            t={t}
            disabled={readOnly}
            disabledReason={readOnly ? "Кадр рендерят оператор и владелец" : undefined}
            overlay={!!frameOverlay}
            onOverlayChange={(on, url) => setFrameOverlay(on ? url : null)}
            overlayOpacity={frameOverlayOpacity}
            onOverlayOpacity={setFrameOverlayOpacity}
          />
          <Inspector
            draft={draft}
            layer={selectedLayer}
            creative={selectedCreative}
            profile={clampTarget}
            readOnly={readOnly}
            onSlot={onSlot}
            onLayer={onLayer}
            onStyle={onStyle}
            onReplaceCreative={() => setPickerOpen(true)}
          />
          <ServiceLayersPanel
            draft={draft}
            doc={doc}
            selectedId={selectedId}
            readOnly={readOnly}
            onToggleKind={onToggleKind}
            onSelect={select}
            onLayerText={(id, text) => update((d) => patchLayer(d, id, { text }), { coalesce: `${id}-text` })}
            onAdMarking={(patch) =>
              update((d) => ({ ...d, ad_marking: { ...d.ad_marking, ...patch } }), {
                coalesce: `marking-${Object.keys(patch).join()}`,
              })
            }
            onCodeUrlTemplate={(value) => update((d) => ({ ...d, code_url_template: value }), { coalesce: "code-url" })}
          />
          <TimingPanel
            timing={draft.timing}
            duration={duration}
            readOnly={readOnly}
            onChange={(patch, coalesce) =>
              update(
                (d) => ({ ...d, timing: { ...d.timing, ...patch } }),
                coalesce ? { coalesce: `timing-${coalesce}` } : undefined,
              )
            }
          />
        </aside>
      </div>

      <Dialog
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        title="Креатив для варианта"
        description="Загрузите новый файл или выберите из библиотеки. Позиция и тайминг слоя сохранятся."
        className="max-w-xl"
      >
        <div className="flex flex-col gap-3">
          <CreativeUploader compact productId={doc.productId} onUploaded={(creative) => pickCreative(creative)} />
          <CreativeLibrary productId={doc.productId} currentShas={shas} onPick={pickCreative} readOnly={readOnly} />
        </div>
      </Dialog>
    </div>
  );
}

/**
 * Почему сервер не принял черновик, по-русски. У активного варианта ошибки проверки приходят
 * в `details.warnings` (коды баннера), у полей — `details.errors[].loc` (слой, стиль, шрифт).
 */
function saveErrorMessage(error: unknown, draft: EditorDraft): string {
  if (error instanceof ValidationError) {
    const warnings = error.details.warnings;
    if (Array.isArray(warnings) && warnings.length) {
      const labels = (warnings as Array<{ code?: string }>).map((w) => validationCodeLabel(String(w.code ?? "")));
      return `Вариант в ротации, а у баннера ошибки: ${Array.from(new Set(labels)).slice(0, 3).join("; ")}. Исправьте или снимите вариант с ротации.`;
    }
    const described = describeValidationError(error, draft);
    if (described) return described;
  }
  return errorMessage(error);
}

function SaveIndicator({ state, readOnly }: { state: SaveState; readOnly: boolean }) {
  if (readOnly) return <span className="px-2 text-2xs text-fg-muted">только просмотр</span>;
  const map: Record<SaveState, { text: string; className: string }> = {
    saved: { text: "сохранено", className: "text-fg-muted" },
    dirty: { text: "есть правки", className: "text-human" },
    saving: { text: "сохраняю…", className: "text-machine" },
    error: { text: "не сохранено", className: "text-negative" },
    conflict: { text: "конфликт версий", className: "text-warning" },
  };
  const item = map[state];
  return (
    <span
      className={cn("inline-flex items-center gap-1 px-2 text-2xs", item.className)}
      data-testid="save-state"
      data-state={state}
      aria-live="polite"
    >
      {state === "saving" ? <LoaderCircle className="h-3 w-3 animate-spin" aria-hidden="true" /> : null}
      {item.text}
    </span>
  );
}

export function EditorLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <Link href={href} className="text-accent hover:underline">
      {children}
    </Link>
  );
}
