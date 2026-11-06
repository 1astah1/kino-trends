"use client";

import { Layers, Lock, RefreshCw, Sparkles } from "lucide-react";
import { type FormEvent, useId, useMemo, useState } from "react";
import { toast } from "sonner";

import { Badge, Button, Callout, Dialog, FormField, Input, Select, Skeleton, StatusBadge } from "@/components/ui";
import type { Schemas } from "@/lib/api/client";
import { errorMessage, ValidationError } from "@/lib/api/errors";
import { cn } from "@/lib/cn";
import {
  type ClipCandidate,
  type SourceSelectionInfo,
  useCreateClipsBatch,
  useNicheOptions,
  useSelectAgain,
} from "@/lib/queries/clipping";
import { useChannelOptions } from "@/lib/queries/jobs";
import { useClipEditor } from "@/lib/store/clip-editor";

import { formatSeconds, formatTc } from "./clip-math";
import { frameRequest } from "./use-clip-work";
import {
  CANDIDATE_FLAG_LABELS,
  CANDIDATE_ORIGIN_META,
  CANDIDATE_STATUS_META,
  HOOK_TYPE_LABELS,
  SCORE_PART_LABELS,
  SELECTION_MODE_META,
  SELECTOR_LABELS,
} from "./labels";

export type MomentsFilter = "active" | "approved" | "dismissed" | "all";

const FILTERS: Array<{ value: MomentsFilter; label: string }> = [
  { value: "active", label: "Все, кроме отброшенных" },
  { value: "approved", label: "Одобренные" },
  { value: "dismissed", label: "Отброшенные" },
  { value: "all", label: "Все моменты" },
];

function scoreTone(score: number | null): string {
  if (score === null) return "text-fg-muted";
  if (score >= 0.75) return "text-positive";
  if (score >= 0.55) return "text-fg";
  return "text-fg-muted";
}

function ScoreParts({ parts }: { parts: Record<string, number> }) {
  const keys = ["llm", "feat", "len_fit", "trend"].filter((key) => typeof parts[key] === "number");
  if (!keys.length) return null;
  return (
    <span className="flex items-end gap-1" aria-hidden="true">
      {keys.map((key) => (
        <span
          key={key}
          className="flex flex-col items-center gap-px"
          title={`${SCORE_PART_LABELS[key] ?? key}: ${Math.round(parts[key] * 100)}`}
        >
          <span className="flex h-4 w-1.5 items-end overflow-hidden rounded-[1px] bg-surface-2">
            <span
              className="block w-full bg-machine"
              style={{ height: `${Math.max(4, Math.min(100, parts[key] * 100))}%` }}
            />
          </span>
        </span>
      ))}
    </span>
  );
}

/** Момент можно отправить пачкой: не заблокирован, не отклонён и ещё не в ролике. */
export function batchable(candidate: ClipCandidate): boolean {
  return !candidate.blocked && candidate.status !== "dismissed" && candidate.status !== "used" && !candidate.job_id;
}

function MomentItem({
  candidate,
  selected,
  onSelect,
  check,
}: {
  candidate: ClipCandidate;
  selected: boolean;
  onSelect: () => void;
  /** Режим «Пачкой»: галочка слева от момента. */
  check?: { checked: boolean; disabled: boolean; onToggle: () => void };
}) {
  const score = candidate.final_score;
  const otherFlags = candidate.content_flags.filter((flag) => !candidate.block_reasons.includes(flag));
  return (
    <li className={check ? "flex items-stretch" : undefined}>
      {check ? (
        <label
          className={cn(
            "flex shrink-0 items-start px-2 pt-3",
            check.disabled ? "cursor-not-allowed opacity-40" : "cursor-pointer",
          )}
          title={check.disabled ? "Заблокирован, отклонён или уже в ролике" : "В пачку"}
        >
          <input
            type="checkbox"
            className="h-4 w-4 accent-[var(--accent)]"
            checked={check.checked}
            disabled={check.disabled}
            onChange={check.onToggle}
            aria-label={`В пачку: момент ${candidate.id}`}
          />
        </label>
      ) : null}
      <button
        type="button"
        onClick={onSelect}
        aria-pressed={selected}
        data-testid={`moment-${candidate.id}`}
        data-candidate-id={candidate.id}
        data-status={candidate.status}
        data-blocked={candidate.blocked ? "true" : "false"}
        className={cn(
          "flex w-full flex-col gap-1 border-l-2 px-3 py-2 text-left transition-colors duration-150",
          selected ? "border-accent bg-accent/10" : "border-transparent hover:bg-surface-2",
          candidate.status === "dismissed" || candidate.superseded ? "opacity-60" : "",
        )}
      >
        <span className="flex items-start gap-2">
          <span
            className={cn("w-7 shrink-0 font-mono text-sm font-semibold tabular", scoreTone(score))}
            title="Итоговая оценка 0–100"
          >
            {score === null ? "—" : Math.round(score * 100)}
          </span>
          <span className="min-w-0 flex-1">
            <span className="line-clamp-2 text-sm text-fg">
              {candidate.title || candidate.hook_text || candidate.transcript_excerpt || `Момент №${candidate.id}`}
            </span>
          </span>
          <ScoreParts parts={candidate.score_parts} />
        </span>
        <span className="flex flex-wrap items-center gap-1 pl-9">
          <span className="font-mono text-2xs text-fg-muted tabular">
            {formatTc(candidate.start_sec, { withCs: false })}–{formatTc(candidate.end_sec, { withCs: false })} ·{" "}
            {formatSeconds(candidate.duration_sec)}
          </span>
          {candidate.origin !== "llm" ? <StatusBadge status={candidate.origin} map={CANDIDATE_ORIGIN_META} /> : null}
          {candidate.status !== "proposed" ? (
            <StatusBadge status={candidate.status} map={CANDIDATE_STATUS_META} />
          ) : null}
          {candidate.superseded ? <Badge variant="neutral">прошлый прогон</Badge> : null}
          {candidate.hook_type ? (
            <span className="text-2xs text-fg-muted">{HOOK_TYPE_LABELS[candidate.hook_type]}</span>
          ) : null}
        </span>
        {candidate.blocked || otherFlags.length || candidate.needs_context ? (
          <span className="flex flex-wrap items-center gap-1 pl-9">
            {candidate.blocked ? (
              <Badge variant="negative" title="Блокирующий флаг: снимите его явно в инспекторе">
                <Lock className="h-3 w-3" aria-hidden="true" />
                {candidate.block_reasons.map((flag) => CANDIDATE_FLAG_LABELS[flag]).join(", ")}
              </Badge>
            ) : null}
            {otherFlags.map((flag) => (
              <Badge key={flag} variant="warning" outline>
                {CANDIDATE_FLAG_LABELS[flag]}
              </Badge>
            ))}
            {candidate.needs_context ? <Badge variant="neutral">нужен контекст</Badge> : null}
          </span>
        ) : null}
      </button>
    </li>
  );
}

/** Подпись режима отбора: эвристику и mock показываем всегда (OWNER-DECISIONS: «ясно подписаны в UI»). */
export function SelectionLabel({ selection }: { selection: SourceSelectionInfo | null | undefined }) {
  if (!selection) return null;
  const weak = selection.mode !== "llm" || selection.fallback;
  return (
    <div
      className={cn(
        "flex flex-col gap-1 rounded-md border px-2.5 py-1.5 text-xs",
        weak ? "border-warning/40 bg-warning/10" : "border-border",
      )}
      data-testid="selection-label"
      data-mode={selection.mode}
    >
      <span className="flex flex-wrap items-center gap-1.5">
        <StatusBadge status={selection.mode} map={SELECTION_MODE_META} />
        <span className="text-fg">{selection.label}</span>
      </span>
      {selection.fallback ? <span className="text-warning">LLM не ответил — моменты подобрала эвристика.</span> : null}
      {selection.mode === "heuristic" ? (
        <span className="text-fg-muted">Оценки — прикидка эвристики по темпу речи, паузам и законченности мысли.</span>
      ) : null}
      <span className="font-mono text-2xs text-fg-muted tabular">
        {selection.n_candidates} моментов · {selection.n_blocked} заблок. · запросов {selection.n_requests}
        {selection.cost_usd ? ` · $${selection.cost_usd.toFixed(3)}` : ""}
      </span>
    </div>
  );
}

/**
 * MomentsPanel (§7.3: «AI-моменты со score и подоценками»). Порядок — по итоговой оценке сервера;
 * у каждого момента — время, длина, происхождение (эвристика и mock подписаны), флаги и блок.
 */
export function MomentsPanel({
  candidates,
  selection,
  isLoading,
  selectedId,
  onSelect,
  filter,
  onFilter,
  includeSuperseded,
  onIncludeSuperseded,
  sourceId,
  canOperate,
  sourceReady,
}: {
  candidates: readonly ClipCandidate[];
  selection: SourceSelectionInfo | null | undefined;
  isLoading: boolean;
  selectedId: number | null;
  onSelect: (id: number) => void;
  filter: MomentsFilter;
  onFilter: (filter: MomentsFilter) => void;
  includeSuperseded: boolean;
  onIncludeSuperseded: (value: boolean) => void;
  sourceId: number;
  canOperate: boolean;
  sourceReady: boolean;
}) {
  const [reselect, setReselect] = useState(false);
  const [batchMode, setBatchMode] = useState(false);
  const [picked, setPicked] = useState<ReadonlySet<number>>(() => new Set());
  const visible = useMemo(
    () =>
      candidates.filter((c) => {
        if (filter === "all") return true;
        if (filter === "dismissed") return c.status === "dismissed";
        if (filter === "approved") return c.status === "approved";
        return c.status !== "dismissed";
      }),
    [candidates, filter],
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid="moments-panel">
      <div className="flex flex-col gap-2 border-b border-border px-3 py-2">
        <div className="flex items-center justify-between gap-2">
          <span className="flex items-center gap-1.5 font-mono text-2xs tracking-wider text-fg-muted uppercase">
            <Sparkles className="h-3 w-3 text-machine" aria-hidden="true" />
            Моменты
          </span>
          {canOperate ? (
            <span className="flex items-center gap-1">
              <Button
                variant={batchMode ? "outline" : "ghost"}
                size="xs"
                onClick={() => {
                  setBatchMode((value) => !value);
                  setPicked(new Set());
                }}
                disabled={!sourceReady}
                aria-pressed={batchMode}
                title="Отметить несколько моментов и отправить в рендер одной кнопкой"
              >
                <Layers className="h-3 w-3" aria-hidden="true" />
                Пачкой
              </Button>
              <Button variant="ghost" size="xs" onClick={() => setReselect(true)} disabled={!sourceReady}>
                <RefreshCw className="h-3 w-3" aria-hidden="true" />
                Отобрать заново
              </Button>
            </span>
          ) : null}
        </div>
        <SelectionLabel selection={selection} />
        <Select
          aria-label="Какие моменты показать"
          value={filter}
          onChange={(event) => onFilter(event.target.value as MomentsFilter)}
          className="h-7 text-xs"
          data-testid="moments-filter"
        >
          {FILTERS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </Select>
        <label className="flex items-center gap-1.5 text-2xs text-fg-muted">
          <input
            type="checkbox"
            checked={includeSuperseded}
            onChange={(event) => onIncludeSuperseded(event.target.checked)}
          />
          показать прошлые прогоны отбора
        </label>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {isLoading ? (
          <div className="flex flex-col gap-2 p-3" aria-busy="true">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-14 w-full" />
            ))}
          </div>
        ) : visible.length === 0 ? (
          <p className="p-4 text-sm text-fg-muted">
            {candidates.length
              ? "В этом фильтре пусто."
              : sourceReady
                ? "Моментов нет. Отберите заново или выделите фрагмент в транскрипте и нажмите «Новый момент»."
                : "Моменты появятся после распознавания и отбора."}
          </p>
        ) : (
          <ul className="flex flex-col divide-y divide-border" aria-label="Моменты источника">
            {visible.map((candidate) => (
              <MomentItem
                key={candidate.id}
                candidate={candidate}
                selected={candidate.id === selectedId}
                onSelect={() => onSelect(candidate.id)}
                check={
                  batchMode
                    ? {
                        checked: picked.has(candidate.id),
                        disabled: !batchable(candidate),
                        onToggle: () =>
                          setPicked((prev) => {
                            const next = new Set(prev);
                            if (next.has(candidate.id)) next.delete(candidate.id);
                            else next.add(candidate.id);
                            return next;
                          }),
                      }
                    : undefined
                }
              />
            ))}
          </ul>
        )}
      </div>
      {batchMode ? (
        <BatchBar
          sourceId={sourceId}
          candidates={visible}
          picked={picked}
          onPick={setPicked}
          onDone={() => {
            setPicked(new Set());
            setBatchMode(false);
          }}
        />
      ) : null}
      {reselect ? (
        <SelectAgainDialog sourceId={sourceId} selection={selection} onClose={() => setReselect(false)} />
      ) : null}
    </div>
  );
}

const SHAPE_LABELS: Record<string, string> = {
  channel: "оформление канала",
  auto: "авто",
  circle: "круг",
  rounded: "скруглённая рамка",
  none: "обычный кадр",
};
const BATCH_MAX = 20;

/** Низ панели в режиме «Пачкой»: сколько выбрано, куда и как уйдёт, кнопка «В рендер». */
function BatchBar({
  sourceId,
  candidates,
  picked,
  onPick,
  onDone,
}: {
  sourceId: number;
  candidates: readonly ClipCandidate[];
  picked: ReadonlySet<number>;
  onPick: (next: ReadonlySet<number>) => void;
  onDone: () => void;
}) {
  const channels = useChannelOptions();
  const stored = useClipEditor((state) => state.channelId);
  const campaignId = useClipEditor((state) => state.campaignId);
  const variantId = useClipEditor((state) => state.variantId);
  const reframe = useClipEditor((state) => state.reframe);
  const voice = useClipEditor((state) => state.voice);
  const frame = useClipEditor((state) => state.frame);
  const captions = useClipEditor((state) => state.captions);
  const setChannel = useClipEditor((state) => state.setChannel);
  const setFrame = useClipEditor((state) => state.setFrame);
  const setCaptions = useClipEditor((state) => state.setCaptions);
  const create = useCreateClipsBatch(sourceId);
  const list = channels.data ?? [];
  const channel = list.find((c) => c.id === stored) ?? list[0] ?? null;
  const free = candidates.filter(batchable);
  const chosen = candidates.filter((c) => picked.has(c.id) && batchable(c));
  const tooMany = chosen.length > BATCH_MAX;

  const submit = () => {
    if (!channel || !chosen.length || tooMany) return;
    const items: Schemas["ClipBatchItem"][] = chosen.map((c) => ({
      start_sec: c.start_sec,
      end_sec: c.end_sec,
      candidate_id: c.id,
      channel_id: channel.id,
      campaign_id: campaignId,
      banner_variant_id: variantId,
      author_layer: { mode: "llm_draft", voice, captions: captions ? "both" : "none", frame: frameRequest(frame) },
      reframe: { mode: reframe },
      priority: 100,
      start: true,
    }));
    create.mutate(items, {
      onSuccess: (res) => {
        toast.success(`В рендер: ${res.items.length} на «${channel.title ?? channel.id}»`);
        onDone();
      },
      onError: (error) => toast.error(errorMessage(error)),
    });
  };

  return (
    <div className="flex flex-col gap-1.5 border-t border-border bg-surface px-3 py-2" data-testid="moments-batch">
      <div className="flex items-center justify-between gap-2 text-2xs text-fg-muted">
        <span>
          Выбрано <span className="font-mono text-fg tabular">{chosen.length}</span> из {free.length} свободных
        </span>
        <span className="flex gap-2">
          <button
            type="button"
            className="hover:text-fg"
            onClick={() => onPick(new Set(free.slice(0, BATCH_MAX).map((c) => c.id)))}
          >
            {free.length > BATCH_MAX ? `первые ${BATCH_MAX}` : "все"}
          </button>
          <button type="button" className="hover:text-fg" onClick={() => onPick(new Set())}>
            снять
          </button>
        </span>
      </div>
      <div className="grid grid-cols-2 gap-1.5">
        <Select
          aria-label="Канал для пачки"
          value={channel?.id ?? ""}
          onChange={(event) => setChannel(event.target.value || null)}
          className="h-7 text-xs"
          disabled={channels.isPending}
        >
          {!channel ? <option value="">Выберите канал</option> : null}
          {list.map((c) => (
            <option key={c.id} value={c.id}>
              {c.title ?? c.id}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Форма кадра для пачки"
          value={frame.shape}
          onChange={(event) => setFrame({ shape: event.target.value as Schemas["FrameShape"] })}
          className="h-7 text-xs"
        >
          {Object.entries(SHAPE_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </Select>
      </div>
      <label className="flex items-center gap-1.5 text-2xs text-fg-muted">
        <input
          type="checkbox"
          className="h-3.5 w-3.5 accent-[var(--accent)]"
          checked={captions}
          onChange={(event) => setCaptions(event.target.checked)}
        />
        субтитры · тонкая настройка — в блоке «Оформление» у момента
      </label>
      {tooMany ? <p className="text-2xs text-warning">За раз не больше {BATCH_MAX} моментов.</p> : null}
      <Button size="sm" onClick={submit} disabled={!channel || !chosen.length || tooMany || create.isPending}>
        {create.isPending ? "Отправляю…" : `В рендер (${chosen.length})`}
      </Button>
    </div>
  );
}

type Selector = "auto" | "llm" | "heuristic";

function SelectAgainDialog({
  sourceId,
  selection,
  onClose,
}: {
  sourceId: number;
  selection: SourceSelectionInfo | null | undefined;
  onClose: () => void;
}) {
  const formId = useId();
  const params = selection?.params;
  const [selector, setSelector] = useState<Selector>(params?.selector ?? "auto");
  const [target, setTarget] = useState(String(params?.target_clip_sec ?? 30));
  const [maxCandidates, setMaxCandidates] = useState(String(params?.max_candidates ?? 12));
  const [tighten, setTighten] = useState(params?.tighten_pauses ?? false);
  const [nicheId, setNicheId] = useState("");
  const [saveDefault, setSaveDefault] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const niches = useNicheOptions();
  const mutation = useSelectAgain(sourceId);

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    const targetSec = Number(target.replace(",", "."));
    const max = Number(maxCandidates);
    if (!Number.isFinite(targetSec) || targetSec < 10 || targetSec > 58) {
      setError("Цель длины: 10–58 с");
      return;
    }
    if (!Number.isInteger(max) || max < 1 || max > 30) {
      setError("Моментов: 1–30");
      return;
    }
    try {
      const accepted = await mutation.mutateAsync({
        selector,
        target_clip_sec: targetSec,
        min_clip_sec: Math.min(15, targetSec),
        max_candidates: max,
        tighten_pauses: tighten,
        niche_id: nicheId || null,
        save_as_default: saveDefault,
      });
      toast.success(`Отбор запущен (задача №${accepted.task_id})`, {
        description: "Моменты обновятся, когда задача закончится.",
      });
      onClose();
    } catch (err) {
      setError(err instanceof ValidationError ? (Object.values(err.fieldErrors)[0] ?? err.message) : errorMessage(err));
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title="Отобрать моменты заново"
      description="Прежние автоматические моменты без роликов уйдут в «прошлые прогоны»; ручные и взятые в работу останутся."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Отмена
          </Button>
          <Button type="submit" form={formId} disabled={mutation.isPending}>
            {mutation.isPending ? "Запускаем…" : "Запустить отбор"}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={onSubmit} className="flex flex-col gap-3">
        <FormField label="Кто выбирает" htmlFor={`${formId}-selector`}>
          <Select
            id={`${formId}-selector`}
            value={selector}
            onChange={(event) => setSelector(event.target.value as Selector)}
          >
            {(Object.keys(SELECTOR_LABELS) as Selector[]).map((key) => (
              <option key={key} value={key}>
                {SELECTOR_LABELS[key]}
              </option>
            ))}
          </Select>
        </FormField>
        <div className="grid grid-cols-2 gap-3">
          <FormField label="Цель длины, с" htmlFor={`${formId}-target`} hint="10–58">
            <Input
              id={`${formId}-target`}
              inputMode="decimal"
              value={target}
              onChange={(event) => setTarget(event.target.value)}
            />
          </FormField>
          <FormField label="Моментов" htmlFor={`${formId}-max`} hint="1–30">
            <Input
              id={`${formId}-max`}
              inputMode="numeric"
              value={maxCandidates}
              onChange={(event) => setMaxCandidates(event.target.value)}
            />
          </FormField>
        </div>
        <FormField label="Ниша (словарь запретов)" htmlFor={`${formId}-niche`}>
          <Select id={`${formId}-niche`} value={nicheId} onChange={(event) => setNicheId(event.target.value)}>
            <option value="">Как у источника</option>
            {(niches.data ?? []).map((niche) => (
              <option key={niche.id} value={niche.id}>
                {niche.label}
              </option>
            ))}
          </Select>
        </FormField>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={tighten} onChange={(event) => setTighten(event.target.checked)} />
          Джамп-каты по паузам (не для музыки)
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={saveDefault} onChange={(event) => setSaveDefault(event.target.checked)} />
          Запомнить параметры для источника
        </label>
        {selector === "llm" ? (
          <Callout variant="info">LLM — платный шаг: расход идёт через дневной бюджет.</Callout>
        ) : null}
        {error ? (
          <p role="alert" className="text-xs text-negative">
            {error}
          </p>
        ) : null}
      </form>
    </Dialog>
  );
}
