"use client";

import { Clapperboard, Plus, Scissors, X } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Dialog as RadixDialog } from "radix-ui";
import { type FormEvent, useMemo, useState } from "react";
import { toast } from "sonner";

import { PLATFORM_LABELS } from "@/components/production/job-meta";
import { Button, Callout, FormField, Input, PillToggle, Select, Textarea } from "@/components/ui";
import type { Schemas } from "@/lib/api/client";
import { BudgetPausedError, ConflictError, errorMessage, ValidationError } from "@/lib/api/errors";
import { formatUsd } from "@/lib/format";
import { routeHref } from "@/lib/nav";
import { budgetUsage, useBudget } from "@/lib/queries/budget";
import {
  type CreateJobsInput,
  newIdempotencyKey,
  useCampaignOptions,
  useChannelOptions,
  useCreateJobs,
  useProviderOptions,
  useVariantOptions,
} from "@/lib/queries/jobs";

import { useNewJobSheet } from "./new-job-store";

const MAX_TOPICS = 20;
const MAX_TOPIC_LEN = 300;
/** Длина клипа (services/jobs.py): 3–180 с, для VK — не больше 60 с. */
const CLIP_MIN_SEC = 3;
const CLIP_MAX_SEC = 180;
const VK_MAX_SEC = 60;

type Kind = "generate" | "clip";

/**
 * Решение владельца (docs/OWNER-DECISIONS.md): приоритет — трек нарезки, трек ИИ-генерации (M2-B:
 * провайдеры и стадии generate) в этой сборке не подключён — запущенная генерация упадёт на первой
 * стадии (`stage_not_registered`). Поэтому форма открывается на «Клип», а «Генерация» помечена.
 * Когда стадии M2-B зарегистрируют, флаг снимается.
 */
const GENERATE_TRACK_READY = false;

/** `1204.3`, `20:04.3`, `1:20:04` → секунды; пусто или мусор — null. */
export function parseTimeInput(raw: string): number | null {
  const value = raw.trim().replace(",", ".");
  if (!value) return null;
  const parts = value.split(":");
  if (parts.length > 3 || parts.some((part) => !/^\d+(\.\d+)?$/.test(part))) return null;
  return parts.reduce((acc, part) => acc * 60 + Number(part), 0);
}

export function splitTopics(raw: string): string[] {
  return raw
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

type FieldErrors = Partial<Record<"channel" | "topics" | "options" | "priority" | "source" | "range", string>>;

/**
 * NewJobSheet (§7.5: SourceTabs, ChannelSelect, TopicsInput, ProductVariantPicker, EstimateSummary).
 * Одна тема — `POST /jobs`, 2–20 тем — `POST /jobs/batch`; клип по диапазону источника — `POST /jobs`
 * c `kind=clip` (обычно клипы создаются из студии нарезки). Ключ идемпотентности живёт одно открытие.
 */
export function NewJobSheet({ open }: { open: boolean }) {
  const router = useRouter();
  const close = useNewJobSheet((state) => state.close);
  const preset = useNewJobSheet((state) => state.preset);
  const [idempotencyKey] = useState(newIdempotencyKey);

  const channels = useChannelOptions({ enabled: open });
  const campaigns = useCampaignOptions({ enabled: open });
  const providers = useProviderOptions({ enabled: open });
  const budget = useBudget();
  const create = useCreateJobs();

  const [kind, setKind] = useState<Kind>(
    preset?.kind ?? (preset?.topics?.length || preset?.trendBriefId ? "generate" : "clip"),
  );
  const [channelId, setChannelId] = useState(preset?.channelId ?? "");
  const [topicsText, setTopicsText] = useState((preset?.topics ?? []).join("\n"));
  const [campaignId, setCampaignId] = useState<number | null>(preset?.campaignId ?? null);
  const [variantId, setVariantId] = useState<number | null>(preset?.bannerVariantId ?? null);
  const [priority, setPriority] = useState("100");
  const [start, setStart] = useState(true);
  const [sceneCount, setSceneCount] = useState("");
  const [durMin, setDurMin] = useState("");
  const [durMax, setDurMax] = useState("");
  const [llm, setLlm] = useState("");
  const [tts, setTts] = useState("");
  const [image, setImage] = useState("");
  const [sourceId, setSourceId] = useState(preset?.source ? String(preset.source.source_id) : "");
  const [clipStart, setClipStart] = useState(preset?.source ? String(preset.source.start_sec) : "");
  const [clipEnd, setClipEnd] = useState(preset?.source ? String(preset.source.end_sec) : "");
  const [errors, setErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);

  const variants = useVariantOptions(campaignId);
  const topics = useMemo(() => splitTopics(topicsText), [topicsText]);
  const selectedChannelId = channelId || channels.data?.[0]?.id || "";
  const selectedChannel = channels.data?.find((item) => item.id === selectedChannelId) ?? null;
  const batch = kind === "generate" && topics.length > 1;
  /** Генерацию без подключённых стадий не запускаем: только черновик. */
  const generateBlocked = kind === "generate" && !GENERATE_TRACK_READY;
  const startNow = start && !generateBlocked;
  const count = kind === "generate" ? Math.max(1, topics.length) : 1;
  const usage = budgetUsage(budget.data);
  const byKind = (providerKind: Schemas["ProviderKind"]) =>
    (providers.data ?? []).filter((item) => item.kind === providerKind && item.enabled);

  const validate = (): { ok: boolean; body?: CreateJobsInput } => {
    const next: FieldErrors = {};
    if (!selectedChannelId) next.channel = "Выберите канал";
    const prio = Number(priority);
    if (!Number.isInteger(prio) || prio < 0 || prio > 1000) next.priority = "Целое 0–1000";

    if (kind === "generate") {
      if (topics.length > MAX_TOPICS) next.topics = `Не больше ${MAX_TOPICS} тем за раз`;
      else if (topics.some((topic) => topic.length > MAX_TOPIC_LEN))
        next.topics = `Тема длиннее ${MAX_TOPIC_LEN} символов`;
      const scenes = sceneCount.trim() ? Number(sceneCount) : null;
      const dmin = durMin.trim() ? Number(durMin.replace(",", ".")) : null;
      const dmax = durMax.trim() ? Number(durMax.replace(",", ".")) : null;
      if (scenes !== null && (!Number.isInteger(scenes) || scenes < 2 || scenes > 20))
        next.options = "Сцен: целое 2–20";
      else if ((dmin === null) !== (dmax === null)) next.options = "Длительность: укажите и от, и до";
      else if (dmin !== null && dmax !== null && (!(dmin > 0) || !(dmax >= dmin)))
        next.options = "Длительность: 0 < от ≤ до";
      setErrors(next);
      if (Object.keys(next).length) return { ok: false };
      const options: Schemas["GenerateOptions"] = {};
      if (scenes !== null) options.scene_count = scenes;
      if (dmin !== null && dmax !== null) options.duration_target_sec = [dmin, dmax];
      if (llm) options.llm_provider = llm;
      if (tts) options.tts_provider = tts;
      if (image) options.image_provider = image;
      if (batch) {
        return {
          ok: true,
          body: {
            mode: "batch",
            idempotencyKey,
            body: {
              kind: "generate",
              channel_id: selectedChannelId,
              topics,
              campaign_id: campaignId,
              banner_variant_id: variantId,
              start: startNow,
            },
          },
        };
      }
      return {
        ok: true,
        body: {
          mode: "single",
          idempotencyKey,
          body: {
            kind: "generate",
            channel_id: selectedChannelId,
            topic: topics[0] ?? null,
            trend_brief_id: preset?.trendBriefId ?? null,
            campaign_id: campaignId,
            banner_variant_id: variantId,
            priority: prio,
            options,
            start: startNow,
          },
        },
      };
    }

    const src = Number(sourceId);
    const from = parseTimeInput(clipStart);
    const to = parseTimeInput(clipEnd);
    if (!Number.isInteger(src) || src <= 0) next.source = "Номер источника — целое число";
    if (from === null || to === null) next.range = "Начало и конец: секунды или мм:сс";
    else if (to <= from) next.range = "Конец должен быть позже начала";
    else {
      const length = to - from;
      const limit = selectedChannel?.platform === "vk" ? VK_MAX_SEC : CLIP_MAX_SEC;
      if (length < CLIP_MIN_SEC || length > limit)
        next.range = `Длина клипа ${CLIP_MIN_SEC}–${limit} с (сейчас ${length.toFixed(1)} с)`;
    }
    setErrors(next);
    if (Object.keys(next).length || from === null || to === null) return { ok: false };
    return {
      ok: true,
      body: {
        mode: "single",
        idempotencyKey,
        body: {
          kind: "clip",
          channel_id: selectedChannelId,
          source: { source_id: src, start_sec: from, end_sec: to },
          campaign_id: campaignId,
          banner_variant_id: variantId,
          priority: prio,
          start: startNow,
        },
      },
    };
  };

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    setFormError(null);
    const { ok, body } = validate();
    if (!ok || !body) return;
    create.mutate(body, {
      onSuccess: (jobs) => {
        const first = jobs[0];
        toast.success(
          jobs.length === 1 ? `Ролик ${first?.video_uid ?? ""} создан` : `Создано роликов: ${jobs.length}`,
          { description: startNow ? "Ушли в очередь конвейера." : "Сохранены черновиками." },
        );
        close();
        if (first) router.push(routeHref("/production", { job: String(first.id) }));
      },
      onError: (error) => {
        if (error instanceof ValidationError) {
          const fields = error.fieldErrors;
          const mapped: FieldErrors = {};
          for (const [path, message] of Object.entries(fields)) {
            if (path.startsWith("channel_id")) mapped.channel = message;
            else if (path.startsWith("topic")) mapped.topics = message;
            else if (path.startsWith("options")) mapped.options = message;
            else if (path.startsWith("priority")) mapped.priority = message;
            else if (path.startsWith("source.source_id")) mapped.source = message;
            else if (path.startsWith("source")) mapped.range = message;
          }
          setErrors(mapped);
          setFormError(Object.keys(mapped).length ? null : error.message);
          return;
        }
        if (error instanceof BudgetPausedError) {
          setFormError(
            "Платные шаги на паузе (бюджет дня). Снимите паузу в Настройках → Бюджет или дождитесь нового дня.",
          );
          return;
        }
        if (error instanceof ConflictError) {
          setFormError(error.message);
          return;
        }
        setFormError(errorMessage(error));
      },
    });
  };

  const channelOptions = channels.data ?? [];

  return (
    <RadixDialog.Root open={open} onOpenChange={(next) => (next ? undefined : close())}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="fixed inset-0 z-50 bg-overlay" />
        <RadixDialog.Content
          className="fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col border-l border-border bg-surface text-fg"
          data-testid="new-job-sheet"
        >
          <div className="flex h-11 shrink-0 items-center justify-between gap-2 border-b border-border px-4">
            <RadixDialog.Title className="text-base font-semibold">Новый ролик</RadixDialog.Title>
            <RadixDialog.Close asChild>
              <Button variant="ghost" size="icon-sm" aria-label="Закрыть">
                <X className="h-4 w-4" aria-hidden="true" />
              </Button>
            </RadixDialog.Close>
          </div>
          <RadixDialog.Description className="sr-only">
            Генерация по теме или клип из источника: канал, темы, вариант баннера, запуск.
          </RadixDialog.Description>

          <form onSubmit={onSubmit} className="flex min-h-0 flex-1 flex-col" noValidate>
            <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4">
              <PillToggle<Kind>
                label="Вид ролика"
                value={kind}
                onChange={(value) => {
                  setKind(value);
                  setErrors({});
                }}
                options={[
                  { value: "clip", label: "Клип из источника" },
                  { value: "generate", label: GENERATE_TRACK_READY ? "Генерация" : "Генерация (не подключена)" },
                ]}
              />
              {kind === "generate" && !GENERATE_TRACK_READY ? (
                <div data-testid="generate-unavailable">
                  <Callout variant="warning" title="Генерация в этой сборке не подключена">
                    Стадии ИИ-генерации ещё не установлены, запущенный ролик сразу упадёт. Ролик сохранится черновиком —
                    запустите его, когда трек подключат.
                  </Callout>
                </div>
              ) : null}

              <FormField label="Канал" htmlFor="nj-channel" error={errors.channel}>
                <Select
                  id="nj-channel"
                  value={selectedChannelId}
                  onChange={(event) => setChannelId(event.target.value)}
                  aria-invalid={!!errors.channel}
                  disabled={channels.isPending}
                >
                  {channels.isPending ? <option value="">Загрузка каналов…</option> : null}
                  {!channels.isPending && channelOptions.length === 0 ? <option value="">Каналов нет</option> : null}
                  {channelOptions.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.title ?? item.id} · {PLATFORM_LABELS[item.platform]} · {item.niche_id}
                      {item.status === "warming" ? " · прогрев" : ""}
                    </option>
                  ))}
                </Select>
              </FormField>
              {channels.isError ? (
                <Callout variant="warning" title="Каналы не загрузились">
                  {errorMessage(channels.error)}
                </Callout>
              ) : null}

              {kind === "generate" ? (
                <FormField
                  label="Темы — по одной в строке"
                  htmlFor="nj-topics"
                  error={errors.topics}
                  hint={
                    topics.length === 0
                      ? "Пусто — тему выберет ниша канала"
                      : `${topics.length} ${topics.length === 1 ? "тема" : "тем"} → ${topics.length} ${topics.length === 1 ? "ролик" : "роликов"} (до ${MAX_TOPICS})`
                  }
                >
                  <Textarea
                    id="nj-topics"
                    rows={5}
                    value={topicsText}
                    onChange={(event) => setTopicsText(event.target.value)}
                    placeholder={"отношения\nденьги в сентябре\nкарта дня"}
                    aria-invalid={!!errors.topics}
                  />
                </FormField>
              ) : (
                <div className="flex flex-col gap-3">
                  <Callout variant="info">
                    Обычно клипы создаются из{" "}
                    <Link href={routeHref("/clipping")} className="text-accent hover:underline" onClick={() => close()}>
                      студии нарезки
                    </Link>
                    : там видно текст и волну. Здесь — ручной диапазон известного источника (права уже оформлены).
                  </Callout>
                  <FormField label="Источник, №" htmlFor="nj-source" error={errors.source}>
                    <Input
                      id="nj-source"
                      inputMode="numeric"
                      value={sourceId}
                      onChange={(event) => setSourceId(event.target.value)}
                      aria-invalid={!!errors.source}
                      className="font-mono"
                    />
                  </FormField>
                  <div className="grid grid-cols-2 gap-3">
                    <FormField label="Начало" htmlFor="nj-from" hint="секунды или мм:сс">
                      <Input
                        id="nj-from"
                        value={clipStart}
                        onChange={(event) => setClipStart(event.target.value)}
                        aria-invalid={!!errors.range}
                        className="font-mono"
                      />
                    </FormField>
                    <FormField label="Конец" htmlFor="nj-to" error={errors.range}>
                      <Input
                        id="nj-to"
                        value={clipEnd}
                        onChange={(event) => setClipEnd(event.target.value)}
                        aria-invalid={!!errors.range}
                        className="font-mono"
                      />
                    </FormField>
                  </div>
                </div>
              )}

              <div className="grid grid-cols-2 gap-3">
                <FormField label="Кампания" htmlFor="nj-campaign" hint="Авто — по таргетингу кампаний">
                  <Select
                    id="nj-campaign"
                    value={campaignId === null ? "" : String(campaignId)}
                    onChange={(event) => {
                      setCampaignId(event.target.value ? Number(event.target.value) : null);
                      setVariantId(null);
                    }}
                  >
                    <option value="">Авто</option>
                    {(campaigns.data ?? []).map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.name} · {item.product_id}
                      </option>
                    ))}
                  </Select>
                </FormField>
                <FormField
                  label="Вариант баннера"
                  htmlFor="nj-variant"
                  hint={campaignId ? "Авто — по весам" : undefined}
                >
                  <Select
                    id="nj-variant"
                    value={variantId === null ? "" : String(variantId)}
                    onChange={(event) => setVariantId(event.target.value ? Number(event.target.value) : null)}
                    disabled={!campaignId}
                  >
                    <option value="">Авто</option>
                    {(variants.data ?? []).map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.code} · {item.name}
                      </option>
                    ))}
                  </Select>
                </FormField>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <FormField label="Приоритет" htmlFor="nj-priority" error={errors.priority} hint="меньше — раньше">
                  <Input
                    id="nj-priority"
                    type="number"
                    min={0}
                    max={1000}
                    value={priority}
                    onChange={(event) => setPriority(event.target.value)}
                    aria-invalid={!!errors.priority}
                    className="font-mono"
                    disabled={batch}
                  />
                </FormField>
                <label className="mt-5 inline-flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={startNow}
                    disabled={generateBlocked}
                    onChange={(event) => setStart(event.target.checked)}
                  />
                  Запустить сразу
                </label>
              </div>

              {kind === "generate" ? (
                <details className="rounded-md border border-border p-3" open={!!errors.options}>
                  <summary className="cursor-pointer text-sm font-medium">Дополнительно</summary>
                  <div className="mt-3 flex flex-col gap-3">
                    {batch ? (
                      <p className="text-xs text-warning">Для пачки тем используются настройки ниши и канала.</p>
                    ) : null}
                    <div className="grid grid-cols-3 gap-2">
                      <FormField label="Сцен" htmlFor="nj-scenes">
                        <Input
                          id="nj-scenes"
                          inputMode="numeric"
                          value={sceneCount}
                          onChange={(event) => setSceneCount(event.target.value)}
                          disabled={batch}
                          placeholder="ниша"
                        />
                      </FormField>
                      <FormField label="Длит. от, с" htmlFor="nj-dmin">
                        <Input
                          id="nj-dmin"
                          inputMode="decimal"
                          value={durMin}
                          onChange={(event) => setDurMin(event.target.value)}
                          disabled={batch}
                          placeholder="15"
                        />
                      </FormField>
                      <FormField label="до, с" htmlFor="nj-dmax">
                        <Input
                          id="nj-dmax"
                          inputMode="decimal"
                          value={durMax}
                          onChange={(event) => setDurMax(event.target.value)}
                          disabled={batch}
                          placeholder="25"
                        />
                      </FormField>
                    </div>
                    {errors.options ? (
                      <p role="alert" className="text-xs text-negative">
                        {errors.options}
                      </p>
                    ) : null}
                    <div className="grid gap-2">
                      {(
                        [
                          ["llm", "LLM", llm, setLlm],
                          ["tts", "Озвучка", tts, setTts],
                          ["image", "Картинки", image, setImage],
                        ] as const
                      ).map(([providerKind, label, value, setValue]) => (
                        <FormField key={providerKind} label={label} htmlFor={`nj-${providerKind}`}>
                          <Select
                            id={`nj-${providerKind}`}
                            value={value}
                            onChange={(event) => setValue(event.target.value)}
                            disabled={batch}
                          >
                            <option value="">По умолчанию</option>
                            {byKind(providerKind).map((item) => (
                              <option key={item.key} value={item.key}>
                                {item.label}
                                {item.is_default ? " · по умолчанию" : ""}
                              </option>
                            ))}
                          </Select>
                        </FormField>
                      ))}
                    </div>
                  </div>
                </details>
              ) : null}

              <div className="rounded-md border border-border bg-surface-2 p-3 text-sm" data-testid="new-job-estimate">
                <p>
                  Будет создано: <span className="font-mono font-semibold">{count}</span>{" "}
                  {count === 1 ? "ролик" : "роликов"}
                  {selectedChannel ? (
                    <>
                      {" "}
                      · {selectedChannel.title ?? selectedChannel.id} ({PLATFORM_LABELS[selectedChannel.platform]})
                    </>
                  ) : null}
                  {startNow ? " · сразу в очередь" : " · черновиками"}
                </p>
                {budget.data ? (
                  <p className="mt-1 text-xs text-fg-muted">
                    Бюджет дня: {formatUsd(usage.used)} из {formatUsd(budget.data.daily_limit_usd)}
                    {usage.level === "warning" ? " — близко к лимиту" : ""}
                  </p>
                ) : null}
                {budget.data?.paused ? (
                  <p className="mt-1 text-xs text-negative">
                    Платные шаги на паузе{budget.data.pause_reason ? `: ${budget.data.pause_reason}` : ""} — сервер не
                    примет новый ролик, пока пауза не снята.
                  </p>
                ) : null}
              </div>

              {formError ? (
                <p
                  role="alert"
                  className="rounded-md border border-negative/40 bg-negative/10 px-3 py-2 text-sm text-fg"
                  data-testid="new-job-error"
                >
                  {formError}
                </p>
              ) : null}
            </div>

            <div className="flex shrink-0 items-center justify-end gap-2 border-t border-border p-3">
              <Button variant="ghost" onClick={() => close()}>
                Отмена
              </Button>
              <Button type="submit" disabled={create.isPending || !selectedChannelId} data-testid="new-job-submit">
                {kind === "clip" ? (
                  <Scissors className="h-4 w-4" aria-hidden="true" />
                ) : count > 1 ? (
                  <Plus className="h-4 w-4" aria-hidden="true" />
                ) : (
                  <Clapperboard className="h-4 w-4" aria-hidden="true" />
                )}
                {create.isPending ? "Создаю…" : count > 1 ? `Создать ${count}` : "Создать"}
              </Button>
            </div>
          </form>
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}
