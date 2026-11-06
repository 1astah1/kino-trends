"use client";

import { Pencil, Plus, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Badge, Button, DetailGrid, FormField, Input, KeyValue, Select } from "@/components/ui";
import type { Schemas } from "@/lib/api/client";
import { errorMessage, isApiError } from "@/lib/api/errors";
import { BUSINESS_TIMEZONE } from "@/lib/format";
import { type YoutubeConnection, useUpdateChannel } from "@/lib/queries/channels";

type PublishMode = Schemas["PublishMode"];
type VideoPrivacy = Schemas["VideoPrivacy"];

const MODE_LABELS: Record<PublishMode, string> = {
  manual: "вручную",
  auto: "авто, по слотам",
};

const PRIVACY_LABELS: Record<VideoPrivacy, string> = {
  public: "публичный",
  unlisted: "по ссылке",
  private: "скрытый",
};

/** Категории YouTube (`snippet.categoryId`), которые подходят Shorts ниш; по умолчанию сервера — 22. */
const CATEGORY_LABELS: Record<string, string> = {
  "1": "Фильмы и анимация",
  "24": "Развлечения",
  "23": "Юмор",
  "22": "Люди и блоги",
};

const LANGUAGE_LABELS: Record<string, string> = { ru: "русский", en: "английский" };

const MAX_SLOTS = 24;
const SLOT_RE = /^([01][0-9]|2[0-3]):[0-5][0-9]$/;

function categoryLabel(id: string): string {
  return CATEGORY_LABELS[id] ? `${id} · ${CATEGORY_LABELS[id]}` : id;
}

function languageLabel(code: string | null): string {
  return code ? (LANGUAGE_LABELS[code] ?? code) : "не указан";
}

/** План автозалива канала (`publishing_plan`): просмотр всем, правка — operator. */
export function PlanSection({
  connection,
  channelId,
  operator,
}: {
  connection: YoutubeConnection;
  channelId: string;
  operator: boolean;
}) {
  const [editing, setEditing] = useState(false);
  return (
    <div className="flex flex-col gap-1.5" data-testid="youtube-plan">
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-2xs font-semibold tracking-wider text-fg-muted uppercase">План</span>
        {operator && !editing ? (
          <Button size="xs" variant="ghost" onClick={() => setEditing(true)} data-testid="plan-edit">
            <Pencil className="h-3 w-3" aria-hidden="true" />
            Изменить
          </Button>
        ) : null}
      </div>
      {editing ? (
        <PlanForm connection={connection} channelId={channelId} onDone={() => setEditing(false)} />
      ) : (
        <PlanView connection={connection} />
      )}
    </div>
  );
}

function PlanView({ connection: c }: { connection: YoutubeConnection }) {
  return (
    <>
      <DetailGrid>
        <KeyValue label="Режим" value={MODE_LABELS[c.mode] ?? c.mode} />
        <KeyValue label="Приватность" value={PRIVACY_LABELS[c.privacy] ?? c.privacy} />
        <KeyValue label="Роликов в день" value={<span className="tabular font-mono">{c.videos_per_day ?? "—"}</span>} />
        <KeyValue label="Пояс" value={<span className="font-mono text-xs">{c.timezone}</span>} />
        <KeyValue label="Категория" value={categoryLabel(c.category_id)} />
        <KeyValue label="Язык" value={languageLabel(c.language)} />
        <KeyValue
          label="Слоты"
          className="col-span-2 sm:col-span-4"
          value={
            c.slots.length ? (
              <span className="flex flex-wrap gap-1">
                {c.slots.map((slot) => (
                  <Badge key={slot} variant="neutral" outline className="tabular font-mono">
                    {slot}
                  </Badge>
                ))}
              </span>
            ) : (
              <span className="text-fg-muted">не заданы</span>
            )
          }
        />
      </DetailGrid>
      {c.mode === "auto" && !c.connected ? (
        <p className="text-2xs text-warning">Режим «авто», но канал не подключён — пакеты сами не зальются.</p>
      ) : null}
    </>
  );
}

function isTimezone(value: string): boolean {
  try {
    new Intl.DateTimeFormat("en", { timeZone: value });
    return true;
  } catch {
    return false;
  }
}

/** Следующий слот по умолчанию: через 3 часа после последнего (не позже 23:00), иначе полдень. */
function nextSlot(slots: string[]): string {
  const last = [...slots]
    .filter((slot) => SLOT_RE.test(slot))
    .sort()
    .at(-1);
  if (!last) return "12:00";
  const hour = Math.min(23, Number(last.slice(0, 2)) + 3);
  return `${String(hour).padStart(2, "0")}:${last.slice(3)}`;
}

/**
 * Правка плана: PATCH канала с `publishing_plan`. Сервер сливает план с сохранённым (services/channels.py),
 * но отправляем объект целиком — что видно в форме, то и сохранено. Пустой пояс — бизнес-пояс сервера.
 */
function PlanForm({
  connection: c,
  channelId,
  onDone,
}: {
  connection: YoutubeConnection;
  channelId: string;
  onDone: () => void;
}) {
  const update = useUpdateChannel(channelId);
  const [mode, setMode] = useState<PublishMode>(c.mode);
  const [privacy, setPrivacy] = useState<VideoPrivacy>(c.privacy);
  const [perDay, setPerDay] = useState(c.videos_per_day === null ? "" : String(c.videos_per_day));
  const [timezone, setTimezone] = useState(c.timezone);
  const [category, setCategory] = useState(c.category_id);
  const [language, setLanguage] = useState(c.language ?? "");
  const [slots, setSlots] = useState<string[]>(c.slots);
  const [serverErrors, setServerErrors] = useState<Record<string, string>>({});

  const tz = timezone.trim();
  const perDayNumber = perDay.trim() === "" ? null : Number(perDay);
  const errors: Record<string, string | undefined> = {
    slots: slots.some((slot) => !SLOT_RE.test(slot))
      ? "Слот — время ЧЧ:ММ"
      : new Set(slots).size !== slots.length
        ? "Слоты повторяются"
        : undefined,
    timezone: tz && !isTimezone(tz) ? "Неизвестный пояс: нужен IANA, например Europe/Moscow" : undefined,
    videos_per_day:
      perDayNumber !== null && (!Number.isInteger(perDayNumber) || perDayNumber < 0 || perDayNumber > 50)
        ? "Целое от 0 до 50"
        : undefined,
  };
  const fieldError = (field: string) =>
    errors[field] ??
    Object.entries(serverErrors).find(
      ([key]) => key === `publishing_plan.${field}` || key.startsWith(`publishing_plan.${field}.`),
    )?.[1];
  const invalid = Object.values(errors).some(Boolean);
  const categoryOptions = CATEGORY_LABELS[category]
    ? Object.keys(CATEGORY_LABELS)
    : [...Object.keys(CATEGORY_LABELS), category];
  const languageOptions =
    !language || LANGUAGE_LABELS[language] ? Object.keys(LANGUAGE_LABELS) : [...Object.keys(LANGUAGE_LABELS), language];

  const save = async () => {
    if (invalid) return;
    setServerErrors({});
    try {
      await update.mutateAsync({
        publishing_plan: {
          mode,
          slots: [...slots].sort(),
          timezone: tz || null,
          privacy,
          videos_per_day: perDayNumber,
          category_id: category,
          language: language || null,
        },
      });
      toast.success("План автозалива сохранён");
      onDone();
    } catch (error) {
      if (isApiError(error) && error.status === 422) setServerErrors(error.fieldErrors);
      toast.error("План не сохранён", { description: errorMessage(error) });
    }
  };

  return (
    <div className="flex flex-col gap-3 rounded-md border border-border p-2" data-testid="plan-form">
      <div className="grid gap-3 sm:grid-cols-3">
        <FormField
          label="Режим"
          htmlFor="plan-mode"
          hint={mode === "auto" ? "Пакеты заливаются сами по слотам" : "Пакет ждёт ручной заливки"}
        >
          <Select id="plan-mode" value={mode} onChange={(event) => setMode(event.target.value as PublishMode)}>
            {(Object.keys(MODE_LABELS) as PublishMode[]).map((value) => (
              <option key={value} value={value}>
                {MODE_LABELS[value]}
              </option>
            ))}
          </Select>
        </FormField>
        <FormField label="Приватность" htmlFor="plan-privacy">
          <Select
            id="plan-privacy"
            value={privacy}
            onChange={(event) => setPrivacy(event.target.value as VideoPrivacy)}
          >
            {(Object.keys(PRIVACY_LABELS) as VideoPrivacy[]).map((value) => (
              <option key={value} value={value}>
                {PRIVACY_LABELS[value]}
              </option>
            ))}
          </Select>
        </FormField>
        <FormField
          label="Роликов в день"
          htmlFor="plan-per-day"
          error={fieldError("videos_per_day")}
          hint="Пусто — без лимита"
        >
          <Input
            id="plan-per-day"
            type="number"
            inputMode="numeric"
            min={0}
            max={50}
            value={perDay}
            aria-invalid={!!fieldError("videos_per_day")}
            onChange={(event) => setPerDay(event.target.value)}
          />
        </FormField>
        <FormField
          label="Часовой пояс"
          htmlFor="plan-timezone"
          error={fieldError("timezone")}
          hint={`Пусто — ${BUSINESS_TIMEZONE}`}
        >
          <Input
            id="plan-timezone"
            className="font-mono text-xs"
            placeholder={BUSINESS_TIMEZONE}
            spellCheck={false}
            value={timezone}
            aria-invalid={!!fieldError("timezone")}
            onChange={(event) => setTimezone(event.target.value)}
          />
        </FormField>
        <FormField label="Категория YouTube" htmlFor="plan-category" error={fieldError("category_id")}>
          <Select id="plan-category" value={category} onChange={(event) => setCategory(event.target.value)}>
            {categoryOptions.map((id) => (
              <option key={id} value={id}>
                {categoryLabel(id)}
              </option>
            ))}
          </Select>
        </FormField>
        <FormField label="Язык ролика" htmlFor="plan-language" error={fieldError("language")}>
          <Select id="plan-language" value={language} onChange={(event) => setLanguage(event.target.value)}>
            <option value="">не указывать</option>
            {languageOptions.map((code) => (
              <option key={code} value={code}>
                {languageLabel(code)}
              </option>
            ))}
          </Select>
        </FormField>
      </div>

      <FormField
        label={`Слоты (${slots.length}/${MAX_SLOTS}), местное время пояса`}
        error={fieldError("slots")}
        hint={slots.length ? undefined : "Пусто — сервер разложит слоты равномерно с 09:00 до 21:00"}
      >
        <div className="flex flex-wrap items-center gap-1.5" data-testid="plan-slots">
          {slots.map((slot, index) => (
            <span key={index} className="flex items-center gap-0.5">
              <Input
                type="time"
                aria-label={`Слот ${index + 1}`}
                className="h-7 w-28 font-mono text-xs"
                value={slot}
                aria-invalid={!SLOT_RE.test(slot)}
                onChange={(event) =>
                  setSlots((current) => current.map((value, i) => (i === index ? event.target.value : value)))
                }
              />
              <Button
                size="icon-sm"
                variant="ghost"
                aria-label={`Убрать слот ${slot || index + 1}`}
                onClick={() => setSlots((current) => current.filter((_, i) => i !== index))}
              >
                <X className="h-3.5 w-3.5" aria-hidden="true" />
              </Button>
            </span>
          ))}
          <Button
            size="sm"
            variant="outline"
            disabled={slots.length >= MAX_SLOTS}
            onClick={() => setSlots((current) => [...current, nextSlot(current)])}
          >
            <Plus className="h-3.5 w-3.5" aria-hidden="true" />
            Слот
          </Button>
        </div>
      </FormField>

      <div className="flex flex-wrap items-center gap-2 border-t border-border pt-2">
        <Button size="sm" onClick={() => void save()} disabled={invalid || update.isPending} data-testid="plan-save">
          Сохранить план
        </Button>
        <Button size="sm" variant="ghost" onClick={onDone} disabled={update.isPending}>
          Отмена
        </Button>
      </div>
    </div>
  );
}
