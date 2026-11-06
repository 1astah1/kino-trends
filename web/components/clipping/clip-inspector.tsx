"use client";

import {
  ArrowLeftToLine,
  ArrowRightToLine,
  Ban,
  Check,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  Lock,
  Play,
  LockOpen,
  RotateCcw,
  Send,
  ThumbsUp,
  Undo2,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type ReactNode, useState } from "react";
import { toast } from "sonner";

import { JOB_STATUS_META, PLATFORM_LABELS } from "@/components/production/job-meta";
import { Badge, Button, Callout, Input, Select, Skeleton, StatusBadge, Textarea } from "@/components/ui";
import type { Schemas } from "@/lib/api/client";
import { BudgetPausedError, ConflictError, errorMessage, ValidationError } from "@/lib/api/errors";
import { cn } from "@/lib/cn";
import { routeHref } from "@/lib/nav";
import { type ClipCandidate, type Source, usePatchCandidate } from "@/lib/queries/clipping";
import { useCampaignOptions, useChannelOptions, useVariantOptions } from "@/lib/queries/jobs";
import { useClipEditor } from "@/lib/store/clip-editor";

import { AuthorLayerEditor } from "./author-layer-editor";
import { FramePicker } from "./frame-picker";
import {
  durationVerdict,
  formatSeconds,
  formatTc,
  segmentText,
  type TranscriptSentence,
  type TranscriptWord,
  VOICE_SEGMENTS,
  type WordRange,
} from "./clip-math";
import {
  CANDIDATE_FLAG_LABELS,
  CANDIDATE_ORIGIN_META,
  CANDIDATE_STATUS_META,
  HOOK_TYPE_LABELS,
  LLM_SCORE_LABELS,
  REFRAME_LABELS,
  SCORE_PART_LABELS,
} from "./labels";
import { OriginalityReportCard } from "./originality-report";
import { useClipWork } from "./use-clip-work";
import { VoiceSegmentRecorder } from "./voice-recorder";

type ReframeMode = Schemas["ReframeMode"];

function Section({
  title,
  children,
  actions,
  testId,
}: {
  title: string;
  children: ReactNode;
  actions?: ReactNode;
  testId?: string;
}) {
  return (
    <section className="flex flex-col gap-2 border-b border-border px-3 py-3" data-testid={testId}>
      <div className="flex items-center justify-between gap-2">
        <h2 className="font-mono text-2xs tracking-wider text-fg-muted uppercase">{title}</h2>
        {actions}
      </div>
      {children}
    </section>
  );
}

// ---------------------------------------------------------------- момент

function MomentFacts({ candidate, canOperate }: { candidate: ClipCandidate; canOperate: boolean }) {
  const patch = usePatchCandidate();
  const [title, setTitle] = useState(candidate.title ?? "");
  const [hook, setHook] = useState(candidate.hook_text ?? "");
  const [notes, setNotes] = useState(candidate.notes ?? "");
  const [tracked, setTracked] = useState(candidate.id);
  if (tracked !== candidate.id) {
    setTracked(candidate.id);
    setTitle(candidate.title ?? "");
    setHook(candidate.hook_text ?? "");
    setNotes(candidate.notes ?? "");
  }

  const send = async (body: Partial<Schemas["ClipCandidatePatch"]>, success?: string) => {
    try {
      await patch.mutateAsync({ id: candidate.id, body: { snap: true, ...body } });
      if (success) toast.success(success);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  };
  const saveText = (field: "title" | "hook_text" | "notes", value: string, current: string | null) => {
    const next = value.trim();
    if (next === (current ?? "")) return;
    void send({ [field]: next || null });
  };

  const setStatus = (status: "proposed" | "approved" | "dismissed") =>
    void send(
      { status },
      status === "dismissed" ? "Момент отброшен" : status === "approved" ? "Момент одобрен" : "Момент возвращён",
    );

  const llm = candidate.llm_scores;
  return (
    <Section
      title="Момент"
      testId="moment-facts"
      actions={
        <span className="flex items-center gap-1">
          <StatusBadge status={candidate.origin} map={CANDIDATE_ORIGIN_META} />
          <StatusBadge status={candidate.status} map={CANDIDATE_STATUS_META} />
        </span>
      }
    >
      <p className="text-xs text-fg-muted" data-testid="origin-label">
        {candidate.origin_label}
      </p>
      <div className="flex flex-col gap-1.5">
        <Input
          aria-label="Заголовок момента"
          value={title}
          maxLength={90}
          disabled={!canOperate}
          onChange={(event) => setTitle(event.target.value)}
          onBlur={() => saveText("title", title, candidate.title)}
          placeholder="Заголовок момента"
        />
        <Input
          aria-label="Хук момента"
          value={hook}
          maxLength={140}
          disabled={!canOperate}
          onChange={(event) => setHook(event.target.value)}
          onBlur={() => saveText("hook_text", hook, candidate.hook_text)}
          placeholder="Хук: первая фраза, которая держит"
        />
      </div>
      {candidate.reason ? (
        <p className="text-xs text-fg">
          <span className="text-fg-muted">Почему: </span>
          {candidate.reason}
        </p>
      ) : null}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs text-fg-muted">
        <span>
          Итог:{" "}
          <span className="font-mono text-fg tabular">
            {candidate.final_score === null ? "—" : Math.round(candidate.final_score * 100)}
          </span>
        </span>
        {Object.entries(candidate.score_parts).map(([key, part]) => (
          <span key={key}>
            {SCORE_PART_LABELS[key] ?? key}: <span className="font-mono text-fg tabular">{Math.round(part * 100)}</span>
          </span>
        ))}
        {candidate.hook_type ? <span>хук: {HOOK_TYPE_LABELS[candidate.hook_type]}</span> : null}
        {candidate.needs_context ? <span className="text-warning">нужен контекст (×0,7)</span> : null}
      </div>
      {llm ? (
        <details className="text-2xs">
          <summary className="cursor-pointer text-fg-muted">
            Подоценки {candidate.origin === "llm" ? "модели" : "эвристики"}
          </summary>
          <dl className="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5">
            {(Object.keys(LLM_SCORE_LABELS) as Array<keyof typeof LLM_SCORE_LABELS>).map((key) => (
              <div key={key} className="flex items-center gap-2">
                <dt className="w-32 text-fg-muted">{LLM_SCORE_LABELS[key]}</dt>
                <dd className="flex flex-1 items-center gap-1">
                  <span className="h-1 flex-1 overflow-hidden rounded-full bg-surface-2">
                    <span className="block h-full bg-machine" style={{ width: `${(llm[key] / 10) * 100}%` }} />
                  </span>
                  <span className="w-5 text-right font-mono text-fg tabular">
                    {llm[key].toLocaleString("ru-RU", { maximumFractionDigits: 1 })}
                  </span>
                </dd>
              </div>
            ))}
          </dl>
        </details>
      ) : null}

      {candidate.content_flags.length || candidate.unblocked_flags.length ? (
        <div className="flex flex-col gap-1" data-testid="moment-flags">
          {candidate.block_reasons.map((flag) => (
            <div
              key={flag}
              className="flex flex-wrap items-center gap-2 rounded-md bg-negative/10 px-2 py-1 text-xs text-negative"
            >
              <Lock className="h-3.5 w-3.5" aria-hidden="true" />
              {CANDIDATE_FLAG_LABELS[flag]} — блокирует рендер
              {canOperate ? (
                <Button
                  variant="outline"
                  size="xs"
                  className="ml-auto"
                  onClick={() => {
                    if (
                      window.confirm(
                        `Снять блок «${CANDIDATE_FLAG_LABELS[flag]}»? Решение записывается в аудит — проверьте, что утверждений нет.`,
                      )
                    )
                      void send({ unblock_flags: [flag] }, "Блок снят");
                  }}
                >
                  <LockOpen className="h-3 w-3" aria-hidden="true" />
                  Снять блок
                </Button>
              ) : null}
            </div>
          ))}
          {candidate.unblocked_flags.map((flag) => (
            <div key={flag} className="flex flex-wrap items-center gap-2 text-xs text-fg-muted">
              <LockOpen className="h-3.5 w-3.5" aria-hidden="true" />
              {CANDIDATE_FLAG_LABELS[flag]} — блок снят оператором
              {canOperate ? (
                <Button
                  variant="ghost"
                  size="xs"
                  className="ml-auto"
                  onClick={() => void send({ block_flags: [flag] }, "Блок возвращён")}
                >
                  Вернуть блок
                </Button>
              ) : null}
            </div>
          ))}
          {candidate.content_flags
            .filter((flag) => !candidate.block_reasons.includes(flag) && !candidate.unblocked_flags.includes(flag))
            .map((flag) => (
              <Badge key={flag} variant="warning" outline className="self-start">
                {CANDIDATE_FLAG_LABELS[flag]}
              </Badge>
            ))}
          {candidate.forbidden_hits.length ? (
            <p className="text-2xs text-fg-muted">
              Запрещённые фразы: {candidate.forbidden_hits.map((hit) => `«${hit.phrase}»`).join(", ")}
            </p>
          ) : null}
        </div>
      ) : null}

      {canOperate && candidate.status !== "used" ? (
        <div className="flex flex-wrap gap-1.5">
          {candidate.status !== "approved" && candidate.status !== "dismissed" ? (
            <Button variant="outline" size="xs" onClick={() => setStatus("approved")}>
              <ThumbsUp className="h-3 w-3" aria-hidden="true" />
              Одобрить
            </Button>
          ) : null}
          {candidate.status === "approved" ? (
            <Button variant="ghost" size="xs" onClick={() => setStatus("proposed")}>
              <Undo2 className="h-3 w-3" aria-hidden="true" />
              Снять одобрение
            </Button>
          ) : null}
          {candidate.status !== "dismissed" ? (
            <Button variant="ghost" size="xs" onClick={() => setStatus("dismissed")} data-testid="dismiss-moment">
              <Ban className="h-3 w-3" aria-hidden="true" />
              Отбросить (Del)
            </Button>
          ) : (
            <Button variant="outline" size="xs" onClick={() => setStatus("proposed")}>
              <RotateCcw className="h-3 w-3" aria-hidden="true" />
              Вернуть
            </Button>
          )}
        </div>
      ) : null}
      <Textarea
        aria-label="Заметки к моменту"
        value={notes}
        rows={2}
        maxLength={2000}
        disabled={!canOperate}
        onChange={(event) => setNotes(event.target.value)}
        onBlur={() => saveText("notes", notes, candidate.notes)}
        placeholder="Заметки"
        className="text-xs"
      />
    </Section>
  );
}

// ---------------------------------------------------------------- границы

function BoundsEditor({
  candidate,
  words,
  draft,
  dirty,
  times,
  locked,
  platform,
  applying,
  onNudge,
  onInAtPlayhead,
  onOutAtPlayhead,
  onApply,
  onReset,
  onPlay,
}: {
  candidate: ClipCandidate;
  words: readonly TranscriptWord[];
  draft: WordRange | null;
  dirty: boolean;
  times: { start: number; end: number };
  locked: string | null;
  platform: Schemas["Platform"] | null;
  applying: boolean;
  onNudge: (edge: "in" | "out", delta: number) => void;
  onInAtPlayhead: () => void;
  onOutAtPlayhead: () => void;
  onApply: () => void;
  onReset: () => void;
  onPlay: () => void;
}) {
  const duration = times.end - times.start;
  const verdict = durationVerdict(duration, platform);
  const snap = candidate.snap;
  const disabled = !!locked || !draft;
  const edge = (label: string, edgeKey: "in" | "out", sec: number, wordIdx: number | undefined) => (
    <div className="flex flex-col gap-1">
      <span className="text-2xs text-fg-muted">{label}</span>
      <span className="font-mono text-sm text-fg tabular" data-testid={`clip-${edgeKey}`}>
        {formatTc(sec)}
      </span>
      <span className="truncate text-2xs text-fg-muted" title={wordIdx !== undefined ? words[wordIdx]?.w : undefined}>
        {wordIdx !== undefined && words[wordIdx] ? `«${words[wordIdx].w}»` : "—"}
      </span>
      <span className="flex gap-1">
        <Button
          variant="outline"
          size="icon-sm"
          disabled={disabled}
          onClick={() => onNudge(edgeKey, -1)}
          aria-label={`${label}: на слово раньше`}
          data-testid={`${edgeKey}-earlier`}
        >
          <ChevronLeft className="h-3.5 w-3.5" aria-hidden="true" />
        </Button>
        <Button
          variant="outline"
          size="icon-sm"
          disabled={disabled}
          onClick={() => onNudge(edgeKey, 1)}
          aria-label={`${label}: на слово позже`}
          data-testid={`${edgeKey}-later`}
        >
          <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
        </Button>
        <Button
          variant="ghost"
          size="xs"
          disabled={disabled}
          onClick={edgeKey === "in" ? onInAtPlayhead : onOutAtPlayhead}
          title={edgeKey === "in" ? "Вход по головке (I)" : "Выход по головке (O)"}
        >
          {edgeKey === "in" ? (
            <ArrowLeftToLine className="h-3 w-3" aria-hidden="true" />
          ) : (
            <ArrowRightToLine className="h-3 w-3" aria-hidden="true" />
          )}
          {edgeKey === "in" ? "I" : "O"}
        </Button>
      </span>
    </div>
  );

  return (
    <Section
      title="Границы"
      testId="bounds-editor"
      actions={
        <Button variant="ghost" size="xs" onClick={onPlay} title="Проиграть момент (P)">
          <Play className="h-3 w-3" aria-hidden="true" />
          Момент
        </Button>
      }
    >
      <div className="grid grid-cols-2 gap-3">
        {edge("Вход", "in", times.start, draft?.w0)}
        {edge("Выход", "out", times.end, draft?.w1)}
      </div>
      <div className="flex flex-col gap-1">
        <div className="flex items-baseline justify-between text-xs">
          <span className="text-fg-muted">Длина</span>
          <span className="font-mono text-sm font-semibold tabular" data-testid="clip-duration">
            {formatSeconds(duration)}
          </span>
        </div>
        <DurationBar seconds={duration} platform={platform} />
        <span
          className={cn(
            "text-2xs",
            verdict.blocking ? "text-negative" : verdict.zone === "ideal" ? "text-positive" : "text-warning",
          )}
        >
          {verdict.label}
        </span>
      </div>
      {snap && !dirty ? (
        <p className="text-2xs text-fg-muted">
          Прилипание: {formatSeconds(snap.lead_sec, 2)} тишины до первого слова, {formatSeconds(snap.tail_sec, 2)} после
          последнего.
        </p>
      ) : null}
      {dirty ? (
        <div
          className="flex flex-wrap items-center gap-2 rounded-md border border-accent/40 bg-accent/10 px-2 py-1.5"
          data-testid="bounds-dirty"
        >
          <span className="text-xs text-fg">Границы изменены</span>
          <Button size="xs" onClick={onApply} disabled={applying} data-testid="apply-bounds">
            <Check className="h-3 w-3" aria-hidden="true" />
            {applying ? "Применяем…" : "Применить (Enter)"}
          </Button>
          <Button variant="ghost" size="xs" onClick={onReset} data-testid="reset-bounds">
            Сбросить
          </Button>
        </div>
      ) : null}
      {locked ? <p className="text-2xs text-fg-muted">{locked}</p> : null}
    </Section>
  );
}

/** Шкала длины: 0–60 с, зона цели 15–45 с, жёсткий край VK. */
function DurationBar({ seconds, platform }: { seconds: number; platform: Schemas["Platform"] | null }) {
  const max = 60;
  const pos = Math.max(0, Math.min(1, seconds / max)) * 100;
  return (
    <div className="relative h-2 rounded-full bg-surface-2" aria-hidden="true">
      <div
        className="absolute inset-y-0 rounded-full bg-positive/30"
        style={{ left: `${(15 / max) * 100}%`, width: `${(30 / max) * 100}%` }}
      />
      {platform === "vk" ? (
        <div className="absolute inset-y-0 w-px bg-negative" style={{ left: `${(58 / max) * 100}%` }} />
      ) : null}
      <div className="absolute -top-0.5 h-3 w-1 -translate-x-1/2 rounded-sm bg-fg" style={{ left: `${pos}%` }} />
    </div>
  );
}

// ---------------------------------------------------------------- куда и как

function TargetPicker({
  channelId,
  disabled,
  job,
}: {
  channelId: string | null;
  disabled: boolean;
  job: Schemas["Job"] | null;
}) {
  const channels = useChannelOptions();
  const campaigns = useCampaignOptions();
  const campaignId = useClipEditor((state) => state.campaignId);
  const variantId = useClipEditor((state) => state.variantId);
  const reframe = useClipEditor((state) => state.reframe);
  const setChannel = useClipEditor((state) => state.setChannel);
  const setCampaign = useClipEditor((state) => state.setCampaign);
  const setVariant = useClipEditor((state) => state.setVariant);
  const setReframe = useClipEditor((state) => state.setReframe);
  const variants = useVariantOptions(campaignId);

  if (job) {
    return (
      <Section title="Куда" testId="clip-target">
        <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
          <dt className="text-fg-muted">Канал</dt>
          <dd className="truncate text-fg">
            {job.channel.title ?? job.channel.id} · {PLATFORM_LABELS[job.channel.platform]}
          </dd>
          <dt className="text-fg-muted">Вариант баннера</dt>
          <dd className="truncate text-fg">
            {job.banner_variant ? `${job.banner_variant.code} · ${job.banner_variant.name}` : "по кампании"}
          </dd>
        </dl>
      </Section>
    );
  }

  return (
    <Section title="Куда" testId="clip-target">
      <div className="grid grid-cols-2 gap-2">
        <label className="col-span-2 flex flex-col gap-1 text-2xs text-fg-muted">
          Канал
          <Select
            value={channelId ?? ""}
            onChange={(event) => setChannel(event.target.value || null)}
            disabled={disabled || channels.isPending}
            aria-label="Канал"
            data-testid="clip-channel"
          >
            {!channelId ? <option value="">Выберите канал</option> : null}
            {(channels.data ?? []).map((channel) => (
              <option key={channel.id} value={channel.id}>
                {channel.title ?? channel.id} · {PLATFORM_LABELS[channel.platform]}
              </option>
            ))}
          </Select>
        </label>
        <label className="flex flex-col gap-1 text-2xs text-fg-muted">
          Кампания
          <Select
            value={campaignId ? String(campaignId) : ""}
            onChange={(event) => setCampaign(event.target.value ? Number(event.target.value) : null)}
            disabled={disabled}
            aria-label="Кампания"
          >
            <option value="">Баннер канала (по умолчанию)</option>
            {(campaigns.data ?? []).map((campaign) => (
              <option key={campaign.id} value={campaign.id}>
                {campaign.name}
              </option>
            ))}
          </Select>
        </label>
        <label className="flex flex-col gap-1 text-2xs text-fg-muted">
          Вариант баннера
          <Select
            value={variantId ? String(variantId) : ""}
            onChange={(event) => setVariant(event.target.value ? Number(event.target.value) : null)}
            disabled={disabled || !campaignId}
            aria-label="Вариант баннера"
          >
            <option value="">По весам кампании</option>
            {(variants.data ?? []).map((variant) => (
              <option key={variant.id} value={variant.id}>
                {variant.code} · {variant.name}
              </option>
            ))}
          </Select>
        </label>
        <label className="col-span-2 flex flex-col gap-1 text-2xs text-fg-muted">
          Кадр 9:16
          <Select
            value={reframe}
            onChange={(event) => setReframe(event.target.value as ReframeMode)}
            disabled={disabled}
            aria-label="Кадр 9:16"
          >
            {(Object.keys(REFRAME_LABELS) as ReframeMode[]).map((mode) => (
              <option key={mode} value={mode}>
                {REFRAME_LABELS[mode]}
              </option>
            ))}
          </Select>
        </label>
      </div>
    </Section>
  );
}

// ---------------------------------------------------------------- инспектор

/**
 * ClipInspector (§7.3: «инспектор: in/out, зоны длительности, авторский слой, индикатор 70/30 для
 * чужих источников, продукт и вариант; В рендер»). Оценка оригинальности пересчитывается на сервере
 * при каждой правке текста и границ (с задержкой), финальный отчёт ролика — после стадии qc.
 */
export function ClipInspector({
  source,
  candidate,
  words,
  sentences,
  draft,
  boundsDirty,
  draftTimes,
  canOperate,
  applying,
  onNudge,
  onInAtPlayhead,
  onOutAtPlayhead,
  applyBounds,
  resetBounds,
  onPlayClip,
}: {
  source: Source;
  candidate: ClipCandidate;
  words: readonly TranscriptWord[];
  /** Предложения клипа (для вставок авторского слоя). */
  sentences: readonly TranscriptSentence[];
  draft: WordRange | null;
  boundsDirty: boolean;
  draftTimes: { start: number; end: number };
  canOperate: boolean;
  applying: boolean;
  onNudge: (edge: "in" | "out", delta: number) => void;
  onInAtPlayhead: () => void;
  onOutAtPlayhead: () => void;
  applyBounds: () => Promise<unknown>;
  resetBounds: () => void;
  onPlayClip: () => void;
}) {
  const channels = useChannelOptions();
  const storedChannel = useClipEditor((state) => state.channelId);
  const campaignId = useClipEditor((state) => state.campaignId);
  const variantId = useClipEditor((state) => state.variantId);
  const reframe = useClipEditor((state) => state.reframe);
  const voice = useClipEditor((state) => state.voice);
  const setVoice = useClipEditor((state) => state.setVoice);
  const frame = useClipEditor((state) => state.frame);
  const captions = useClipEditor((state) => state.captions);
  const channelList = channels.data ?? [];
  const channelId =
    storedChannel && channelList.some((c) => c.id === storedChannel) ? storedChannel : (channelList[0]?.id ?? null);
  const channel = channelList.find((c) => c.id === channelId) ?? null;

  const work = useClipWork({
    source,
    candidate,
    canOperate,
    boundsDirty,
    draftTimes,
    applyBounds,
    target: { channelId, campaignId, variantId, reframe, voice, frame, captions },
  });
  const [confirmFail, setConfirmFail] = useState(false);
  const [sent, setSent] = useState<{ id: number; uid: string; candidateId: number } | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const router = useRouter();

  // Смена момента или вердикта оценки — сбросить подтверждение «всё равно в рендер».
  const trackKey = `${candidate.id}|${work.estimate?.verdict ?? ""}`;
  const [tracked, setTracked] = useState(trackKey);
  if (tracked !== trackKey) {
    if (!tracked.startsWith(`${candidate.id}|`)) setActionError(null);
    setTracked(trackKey);
    setConfirmFail(false);
  }

  const platform = work.job?.channel.platform ?? channel?.platform ?? null;
  const locked =
    work.phase === "local"
      ? null
      : work.phase === "draft"
        ? "Границы зафиксированы в черновике ролика. Чтобы изменить — отмените черновик."
        : "Границы зафиксированы в ролике.";
  const duration = draftTimes.end - draftTimes.start;
  const durationBlock = durationVerdict(duration, platform);
  const blockers = [...work.blockers];
  if (work.phase === "local" && durationBlock.blocking) blockers.push(durationBlock.label);
  const estimate = work.estimate;
  const gateFail = !!estimate && estimate.verdict === "fail";
  const finalReport = work.job?.originality ?? null;

  const handleError = (error: unknown) => {
    if (error instanceof BudgetPausedError) setActionError("Платные шаги на паузе (бюджет): ролик не запущен");
    else if (error instanceof ConflictError && error.code === "clip_candidate_blocked")
      setActionError("Момент заблокирован флагами — снимите блок");
    else if (error instanceof ConflictError && error.code === "clip_candidate_in_use")
      setActionError("По моменту уже есть ролик — он открыт в Конвейере");
    else if (error instanceof ValidationError) setActionError(Object.values(error.fieldErrors)[0] ?? error.message);
    else setActionError(errorMessage(error));
  };

  const onSend = async () => {
    setActionError(null);
    if (gateFail && !confirmFail) {
      setConfirmFail(true);
      return;
    }
    try {
      const job = await work.sendToRender();
      setSent({ id: job.id, uid: job.video_uid, candidateId: candidate.id });
      setConfirmFail(false);
      // Сверху: снизу справа тост закрывал ссылку «Открыть в Конвейере» в панели отправки, а наведение
      // на тост останавливает его таймер — ссылка оставалась недоступной.
      toast.success(`Ролик ${job.video_uid} отправлен в рендер`, {
        position: "top-center",
        action: { label: "Конвейер", onClick: () => router.push(routeHref("/production", { job: String(job.id) })) },
      });
    } catch (error) {
      handleError(error);
    }
  };

  const onDraft = async () => {
    setActionError(null);
    const hasText = work.value.cold_open.vo_text.trim() || work.value.outro.vo_text.trim() || work.value.inserts.length;
    if (hasText && !window.confirm("Черновик модели заменит текущий авторский текст. Продолжить?")) return;
    try {
      await work.requestDraft();
      toast.success("Модель пишет черновик", { description: "Текст появится через несколько секунд." });
    } catch (error) {
      handleError(error);
    }
  };

  const onCreateDraftJob = async () => {
    setActionError(null);
    try {
      await work.ensureDraftJob();
      toast.success("Черновик ролика создан: можно записывать свой голос");
    } catch (error) {
      handleError(error);
    }
  };

  const voiceSegments = work.authorVoice?.segments ?? [];
  const sentHere = sent && sent.candidateId === candidate.id ? sent : null;
  const jobLink = work.job ? routeHref("/production", { job: String(work.job.id) }) : null;

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid="clip-inspector" data-phase={work.phase}>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {work.job && work.phase !== "local" ? (
          <div
            className="flex flex-wrap items-center gap-2 border-b border-border bg-surface-2 px-3 py-2 text-xs"
            data-testid="clip-job"
          >
            <span className="text-fg-muted">Ролик</span>
            <span className="font-mono text-fg">{work.job.video_uid}</span>
            <StatusBadge status={work.job.status} map={JOB_STATUS_META} />
            {jobLink ? (
              <Link href={jobLink} className="ml-auto inline-flex items-center gap-1 text-accent hover:underline">
                <ExternalLink className="h-3 w-3" aria-hidden="true" />
                Конвейер
              </Link>
            ) : null}
            {work.job.status === "pending_review" || work.job.status === "qc_failed" ? (
              <Link
                href={routeHref("/review", { job: String(work.job.id) })}
                className="inline-flex items-center gap-1 text-human hover:underline"
              >
                Приёмка
              </Link>
            ) : null}
          </div>
        ) : null}
        {work.deadJob ? (
          <p className="border-b border-border px-3 py-1.5 text-2xs text-fg-muted">
            Прошлый ролик {work.deadJob.video_uid}: {JOB_STATUS_META[work.deadJob.status].label.toLowerCase()} — можно
            отправить момент заново.
          </p>
        ) : null}

        <MomentFacts candidate={candidate} canOperate={canOperate} />
        <BoundsEditor
          candidate={candidate}
          words={words}
          draft={draft}
          dirty={boundsDirty}
          times={draftTimes}
          locked={locked}
          platform={platform}
          applying={applying}
          onNudge={onNudge}
          onInAtPlayhead={onInAtPlayhead}
          onOutAtPlayhead={onOutAtPlayhead}
          onApply={() => void applyBounds().catch(handleError)}
          onReset={resetBounds}
          onPlay={onPlayClip}
        />
        <TargetPicker channelId={channelId} disabled={!canOperate} job={work.phase === "local" ? null : work.job} />
        {work.phase === "local" ? <FramePicker disabled={!canOperate} /> : null}

        <Section title="Авторский слой" testId="author-section">
          {work.phase === "loading" ? (
            <Skeleton className="h-40 w-full" />
          ) : (
            <AuthorLayerEditor
              value={work.value}
              onChange={work.onChange}
              sentences={sentences}
              disabled={!work.editable}
              info={
                work.authorScript
                  ? {
                      mode: work.authorScript.mode,
                      label: work.authorScript.label,
                      needsOperatorEdit: work.authorScript.needs_operator_edit,
                      llmConfigured: work.authorScript.llm_configured,
                    }
                  : {
                      // До ролика: подсказка о ключе LLM — из итога отбора источника.
                      mode: null,
                      label: null,
                      needsOperatorEdit: false,
                      llmConfigured: source.selection?.llm_configured ?? null,
                    }
              }
              issues={work.authorScript?.issues ?? []}
              onDraft={canOperate ? () => void onDraft() : null}
              drafting={work.drafting}
              draftDisabledReason={
                work.phase === "local" && !channelId
                  ? "Черновик пишется для ролика: выберите канал"
                  : work.phase === "active" && !work.editable
                    ? "Текст правится до озвучки или на доработке"
                    : null
              }
              saveState={work.saveState}
            />
          )}
          {work.saveError ? (
            <Callout
              variant={work.saveError.conflict ? "warning" : "negative"}
              title="Текст не сохранён"
              action={
                work.saveError.conflict ? (
                  <span className="flex flex-col gap-1">
                    <Button size="xs" variant="outline" onClick={() => void work.resolveConflict(false)}>
                      Взять с сервера
                    </Button>
                    <Button size="xs" onClick={() => void work.resolveConflict(true)}>
                      Сохранить мой
                    </Button>
                  </span>
                ) : undefined
              }
            >
              {work.saveError.message}
            </Callout>
          ) : null}
        </Section>

        <Section title="Голос автора" testId="voice-section">
          {work.phase === "local" ? (
            <div className="flex flex-col gap-2">
              <div
                className="inline-flex w-fit items-center gap-0.5 rounded-md border border-border p-0.5"
                role="group"
                aria-label="Чем озвучить"
              >
                {(["tts", "operator_recording"] as const).map((option) => (
                  <button
                    key={option}
                    type="button"
                    aria-pressed={voice === option}
                    onClick={() => setVoice(option)}
                    disabled={!canOperate}
                    className={cn(
                      "h-6 rounded-sm px-2 text-xs",
                      voice === option ? "bg-accent text-accent-fg" : "text-fg-muted hover:text-fg",
                    )}
                  >
                    {option === "tts" ? "Синтез (TTS)" : "Мой голос"}
                  </button>
                ))}
              </div>
              <p className="text-2xs text-fg-muted">
                Живой голос автора — сильнейший сигнал оригинальности. Запись привязана к ролику: создайте черновик
                ролика (без запуска) и запишите реплики. Синтетический голос требует AI-метку в пакете.
              </p>
              {canOperate ? (
                <Button
                  variant="outline"
                  size="sm"
                  className="self-start"
                  onClick={() => void onCreateDraftJob()}
                  disabled={work.creatingJob || !channelId || candidate.blocked || source.status !== "ready"}
                  data-testid="create-draft-job"
                >
                  {work.creatingJob ? "Создаём…" : "Создать черновик ролика для записи"}
                </Button>
              ) : null}
            </div>
          ) : work.phase === "loading" ? (
            <Skeleton className="h-16 w-full" />
          ) : (
            <div className="flex flex-col gap-2">
              {work.authorVoice?.synthetic ? (
                <p className="text-2xs text-warning">Есть реплики синтезом: в пакете будет обязательная AI-метка.</p>
              ) : null}
              {VOICE_SEGMENTS.filter(
                (segment) => segment === "cold_open" || segment === "outro" || segmentText(work.value, segment),
              ).map((segment) => (
                <VoiceSegmentRecorder
                  key={segment}
                  jobId={work.liveJobId}
                  segment={segment}
                  text={segmentText(work.value, segment)}
                  state={voiceSegments.find((item) => item.segment === segment)}
                  disabled={!canOperate}
                  onBeforeUpload={work.flush}
                />
              ))}
              {work.phase === "draft" && canOperate ? (
                <Button
                  variant="ghost"
                  size="xs"
                  className="self-start"
                  onClick={() => {
                    if (
                      window.confirm(
                        "Отменить черновик ролика? Текст останется в студии, записи голоса удалятся с роликом.",
                      )
                    )
                      void work.cancelDraftJob().catch(handleError);
                  }}
                  disabled={work.cancelling}
                >
                  Отменить черновик ролика
                </Button>
              ) : null}
            </div>
          )}
        </Section>

        <Section title="Оригинальность" testId="originality-section">
          {finalReport ? (
            <OriginalityReportCard report={finalReport} />
          ) : estimate ? (
            <OriginalityReportCard report={estimate} loading={work.estimateLoading} />
          ) : work.estimateError ? (
            <p className="text-xs text-negative">Оценка не посчиталась: {errorMessage(work.estimateError)}</p>
          ) : canOperate ? (
            <Skeleton className="h-24 w-full" />
          ) : (
            <p className="text-xs text-fg-muted">Оценка доступна оператору.</p>
          )}
          {finalReport && estimate ? (
            <details className="text-xs">
              <summary className="cursor-pointer text-fg-muted">Оценка по текущему тексту</summary>
              <OriginalityReportCard report={estimate} compact loading={work.estimateLoading} className="mt-2" />
            </details>
          ) : null}
        </Section>
      </div>

      {/* SendToRenderBar */}
      <div className="flex flex-col gap-2 border-t border-border bg-surface px-3 py-2.5" data-testid="send-bar">
        {sentHere ? (
          <Callout variant="info" title={`Ролик ${sentHere.uid} в очереди`}>
            <Link
              href={routeHref("/production", { job: String(sentHere.id) })}
              className="inline-flex items-center gap-1 text-accent hover:underline"
              data-testid="open-in-production"
            >
              <ExternalLink className="h-3 w-3" aria-hidden="true" />
              Открыть в Конвейере
            </Link>
          </Callout>
        ) : null}
        {actionError ? (
          <p role="alert" className="text-xs text-negative" data-testid="send-error">
            {actionError}
          </p>
        ) : null}
        {work.phase === "active" && work.job && work.job.status !== "rework" ? (
          <p className="text-xs text-fg-muted">
            Момент уже в работе.{" "}
            {jobLink ? (
              <Link href={jobLink} className="text-accent hover:underline" data-testid="open-job-in-production">
                Открыть ролик в Конвейере
              </Link>
            ) : null}
          </p>
        ) : (
          <>
            {blockers.length ? (
              <ul className="flex flex-col gap-0.5 text-2xs text-negative" data-testid="send-blockers">
                {blockers.map((text) => (
                  <li key={text}>• {text}</li>
                ))}
              </ul>
            ) : null}
            {work.notices.length ? (
              <ul className="flex flex-col gap-0.5 text-2xs text-warning" data-testid="send-notices">
                {work.notices.map((text) => (
                  <li key={text}>• {text}</li>
                ))}
              </ul>
            ) : null}
            {gateFail && confirmFail ? (
              <p className="text-xs text-negative">
                По оценке ролик не пройдёт гейт оригинальности и уйдёт в «Не прошёл QC». Допишите авторский слой или
                подтвердите ещё раз.
              </p>
            ) : null}
            <div className="flex items-center gap-2">
              <span className="min-w-0 flex-1 truncate text-2xs text-fg-muted">
                {channel && work.phase === "local"
                  ? `${channel.title ?? channel.id} · ${PLATFORM_LABELS[channel.platform]} · ${formatSeconds(duration)}`
                  : work.phase === "draft"
                    ? "Черновик ролика: сохранить текст и запустить"
                    : ""}
              </span>
              <Button
                variant={gateFail && confirmFail ? "destructive" : "default"}
                onClick={() => void onSend()}
                disabled={!!blockers.length || work.sending || work.phase === "loading"}
                data-testid="send-to-render"
              >
                <Send className="h-4 w-4" aria-hidden="true" />
                {work.sending
                  ? "Отправляем…"
                  : gateFail && confirmFail
                    ? "Всё равно в рендер"
                    : work.phase === "active"
                      ? "Запустить заново"
                      : "В рендер"}
              </Button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
