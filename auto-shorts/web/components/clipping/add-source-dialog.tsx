"use client";

import { FileVideoCamera, HardDrive, Pause, Play, RotateCcw, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { type FormEvent, useId, useState } from "react";
import { toast } from "sonner";

import { Button, Callout, Dialog, FormField, Input, PillToggle, Select } from "@/components/ui";
import { ConflictError, errorMessage, InsufficientStorageError, ValidationError } from "@/lib/api/errors";
import { cn } from "@/lib/cn";
import { formatGb } from "@/lib/format";
import { routeHref } from "@/lib/nav";
import { useCreateSourceFromUpload, useCreateSourceFromUrl, useNicheOptions } from "@/lib/queries/clipping";
import { useSystemStatus } from "@/lib/queries/system";

import { formatBytes } from "./clip-math";
import { SELECTOR_LABELS } from "./labels";
import { RightsForm } from "./rights-form";
import {
  EMPTY_RIGHTS,
  type RightsField,
  type RightsFormValues,
  rightsBody,
  rightsServerErrors,
  validateRights,
  withScheme,
} from "./rights-rules";
import { TUS_MAX_BYTES, uploadEta, uploadPct, useTusUpload } from "./use-tus-upload";

type Mode = "file" | "url";
type Selector = "auto" | "llm" | "heuristic";

const URL_RE = /^https?:\/\/[^\s/]+\.\S{2,2000}$/i;
/** Прокси, звук и служебные файлы сверх оригинала (оценка §6.3.10: 0,5–0,7 ГБ на час по URL). */
const URL_GB_PER_HOUR = 0.7;

function titleFromFile(name: string): string {
  const base = name
    .replace(/\.[^.]+$/, "")
    .replace(/[_]+/g, " ")
    .trim();
  return (base || name).slice(0, 300);
}

interface Options {
  selector: Selector;
  target: string;
  maxCandidates: string;
  tighten: boolean;
}

const DEFAULT_OPTIONS: Options = { selector: "auto", target: "30", maxCandidates: "12", tighten: false };

/**
 * AddSourceDialog (§7.5: TusUploader, UrlForm, RightsForm, DiskEstimate). Файл грузится сразу после
 * выбора (tus, 16 МБ чанки, продолжение после обрыва), пока оператор заполняет права; источник
 * создаётся, когда загрузка закончена и права прошли проверку. Ссылка — только своё или лицензированное.
 */
export function AddSourceDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const router = useRouter();
  const formId = useId();
  const [mode, setMode] = useState<Mode>("file");
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [url, setUrl] = useState("");
  const [nicheId, setNicheId] = useState("");
  const [language, setLanguage] = useState("ru");
  const [options, setOptions] = useState<Options>(DEFAULT_OPTIONS);
  const [rights, setRights] = useState<RightsFormValues>(EMPTY_RIGHTS);
  const [rightsErrors, setRightsErrors] = useState<Partial<Record<RightsField, string>>>({});
  const [rightsGeneral, setRightsGeneral] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);

  const upload = useTusUpload("source");
  const niches = useNicheOptions({ enabled: open });
  const system = useSystemStatus({ slowWhenLive: 60_000 });
  const fromUpload = useCreateSourceFromUpload();
  const fromUrl = useCreateSourceFromUrl();
  const pending = fromUpload.isPending || fromUrl.isPending;

  const reset = () => {
    upload.reset();
    setFile(null);
    setTitle("");
    setUrl("");
    setRights(EMPTY_RIGHTS);
    setRightsErrors({});
    setRightsGeneral(null);
    setFieldErrors({});
    setFormError(null);
    setSubmitted(false);
    setOptions(DEFAULT_OPTIONS);
  };

  const close = () => {
    // Незаконченная загрузка ставится на паузу: тот же файл потом продолжится с того же места.
    if (upload.state.phase === "uploading") upload.pause();
    onClose();
  };

  const onRightsChange = (next: RightsFormValues) => {
    setRights(next);
    if (submitted) setRightsErrors(validateRights(next));
    setRightsGeneral(null);
  };

  const pickFile = (picked: File | null) => {
    setFieldErrors({});
    setFormError(null);
    setFile(picked);
    if (!picked) {
      upload.reset();
      return;
    }
    if (!title.trim()) setTitle(titleFromFile(picked.name));
    void upload.start(picked);
  };

  const sourceOptions = () => {
    const target = Number(options.target.replace(",", "."));
    const max = Number(options.maxCandidates);
    const targetOk = Number.isFinite(target) && target >= 15 && target <= 58;
    return {
      selector: options.selector,
      target_clip_sec: targetOk ? target : 30,
      min_clip_sec: Math.min(15, targetOk ? target : 30),
      max_clip_sec: 58,
      max_candidates: Number.isInteger(max) && max >= 1 && max <= 30 ? max : 12,
      target_count: 6,
      detail_pass: true,
      tighten_pauses: options.tighten,
    };
  };

  const applyServerError = (error: unknown) => {
    if (error instanceof ValidationError) {
      const mapped = rightsServerErrors(error, "rights");
      setRightsErrors(mapped.fields);
      setRightsGeneral(mapped.general);
      const own: Record<string, string> = {};
      for (const [key, message] of Object.entries(mapped.other)) own[key === "upload_id" ? "file" : key] = message;
      // unsupported_url | source_rejected (probe_failed, too_long, no_video, no_audio) — без поля.
      if (error.code === "unsupported_url") own.url = error.message;
      setFieldErrors(own);
      const nothingMapped = !Object.keys(own).length && !mapped.general && !Object.keys(mapped.fields).length;
      if (nothingMapped || (error.code !== "validation_error" && error.code !== "unsupported_url")) {
        setFormError(error.message);
      }
      return;
    }
    if (error instanceof ConflictError) {
      setFormError(
        error.code === "upload_consumed"
          ? "Этот файл уже стал источником. Выберите файл заново, чтобы создать ещё один."
          : error.code === "upload_not_complete"
            ? "Загрузка ещё не закончена"
            : error.message,
      );
      return;
    }
    if (error instanceof InsufficientStorageError) {
      setFormError(`Нет места на диске: ${error.message}`);
      return;
    }
    setFormError(errorMessage(error));
  };

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitted(true);
    setFormError(null);
    const errs = validateRights(rights);
    setRightsErrors(errs);
    setRightsGeneral(null);
    const own: Record<string, string> = {};
    if (mode === "file") {
      if (!file) own.file = "Выберите видеофайл";
      else if (upload.state.phase !== "complete" || !upload.state.uploadId) own.file = "Дождитесь конца загрузки";
      if (!title.trim()) own.title = "Название обязательно";
    } else if (!URL_RE.test(withScheme(url))) {
      own.url = "Не похоже на ссылку на видео";
    }
    setFieldErrors(own);
    if (Object.keys(errs).length || Object.keys(own).length) return;
    const common = {
      rights: rightsBody(rights),
      niche_id: nicheId || null,
      language,
      options: sourceOptions(),
    };
    try {
      const source =
        mode === "file"
          ? await fromUpload.mutateAsync({ ...common, upload_id: upload.state.uploadId as string, title: title.trim() })
          : await fromUrl.mutateAsync({ ...common, url: withScheme(url), title: title.trim() || null });
      toast.success(`Источник №${source.id} принят в обработку`, {
        description: source.title,
        action: {
          label: "Открыть",
          onClick: () => router.push(routeHref("/clipping/source", { id: String(source.id) })),
        },
      });
      reset();
      onClose();
    } catch (error) {
      applyServerError(error);
    }
  };

  const st = upload.state;
  const disk = system.data?.disk;
  const needGb = mode === "file" && file ? (file.size * 1.12) / 1024 ** 3 : null;
  const diskShort = disk && needGb !== null ? disk.free_gb - needGb < disk.thresholds.hard_gb : false;
  const canSubmit =
    !pending && (mode === "url" || (st.phase === "complete" && !!st.uploadId)) && rights.rights_type !== "";

  return (
    <Dialog
      open={open}
      onClose={close}
      title="Добавить источник"
      description="Своё или лицензированное длинное видео. Без записи прав источник не создаётся."
      className="max-w-3xl"
      footer={
        <>
          <Button variant="ghost" onClick={close}>
            Закрыть
          </Button>
          <Button type="submit" form={formId} disabled={!canSubmit} data-testid="add-source-submit">
            {pending ? "Создаём…" : "Создать источник"}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={onSubmit} className="flex flex-col gap-4" noValidate data-testid="add-source-dialog">
        <PillToggle
          label="Откуда видео"
          value={mode}
          onChange={(next) => {
            setMode(next);
            setFieldErrors({});
            setFormError(null);
          }}
          options={[
            { value: "file", label: "Файл" },
            { value: "url", label: "Ссылка" },
          ]}
          className="self-start"
        />

        {mode === "file" ? (
          <div className="flex flex-col gap-2">
            <label
              htmlFor={`${formId}-file`}
              className={cn(
                "flex cursor-pointer items-center gap-3 rounded-md border border-dashed px-3 py-3 text-sm transition-colors duration-150",
                fieldErrors.file ? "border-negative" : "border-border hover:border-fg-muted/60",
              )}
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => {
                event.preventDefault();
                const dropped = event.dataTransfer.files?.[0];
                if (dropped) pickFile(dropped);
              }}
            >
              <FileVideoCamera className="h-5 w-5 shrink-0 text-fg-muted" aria-hidden="true" />
              <span className="flex min-w-0 flex-col">
                <span className="truncate text-fg">{file ? file.name : "Выберите или перетащите видео"}</span>
                <span className="text-xs text-fg-muted">
                  {file ? formatBytes(file.size) : `mp4, mov, mkv, webm · до ${formatBytes(TUS_MAX_BYTES)}, до 3 ч`}
                </span>
              </span>
              <input
                id={`${formId}-file`}
                type="file"
                accept="video/*,.mkv,.mov,.mp4,.webm"
                className="sr-only"
                data-testid="add-source-file"
                onChange={(event) => {
                  pickFile(event.target.files?.[0] ?? null);
                  event.target.value = "";
                }}
              />
            </label>
            {fieldErrors.file ? (
              <p role="alert" className="text-xs text-negative">
                {fieldErrors.file}
              </p>
            ) : null}
            {st.phase !== "idle" ? (
              <div className="flex flex-col gap-1.5" data-testid="upload-progress">
                <div
                  className="h-1.5 overflow-hidden rounded-full bg-surface-2"
                  role="progressbar"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={uploadPct(st)}
                  aria-label="Загрузка файла"
                >
                  <div
                    className={cn(
                      "h-full transition-[width] duration-150",
                      st.phase === "error" ? "bg-negative" : st.phase === "complete" ? "bg-positive" : "bg-machine",
                    )}
                    style={{ width: `${uploadPct(st)}%` }}
                  />
                </div>
                <div className="flex flex-wrap items-center gap-2 text-xs text-fg-muted">
                  <span className="font-mono tabular">
                    {formatBytes(st.sent)} / {formatBytes(st.total)} · {uploadPct(st)}%
                  </span>
                  {st.phase === "complete" ? <span className="text-positive">Загружено</span> : null}
                  {st.phase === "paused" ? <span className="text-warning">На паузе</span> : null}
                  {st.resumed && st.phase !== "complete" ? <span>продолжаем с прошлого раза</span> : null}
                  {uploadEta(st) ? <span>{uploadEta(st)}</span> : null}
                  <span className="ml-auto flex gap-1">
                    {st.phase === "uploading" ? (
                      <Button variant="outline" size="xs" onClick={upload.pause}>
                        <Pause className="h-3 w-3" aria-hidden="true" />
                        Пауза
                      </Button>
                    ) : null}
                    {st.phase === "paused" ? (
                      <Button variant="outline" size="xs" onClick={upload.resume}>
                        <Play className="h-3 w-3" aria-hidden="true" />
                        Продолжить
                      </Button>
                    ) : null}
                    {st.phase === "error" && file ? (
                      <Button variant="outline" size="xs" onClick={() => void upload.start(file)}>
                        <RotateCcw className="h-3 w-3" aria-hidden="true" />
                        Повторить
                      </Button>
                    ) : null}
                    {st.phase !== "complete" ? (
                      <Button
                        variant="ghost"
                        size="xs"
                        onClick={() => {
                          void upload.cancel();
                          setFile(null);
                        }}
                      >
                        <X className="h-3 w-3" aria-hidden="true" />
                        Отменить
                      </Button>
                    ) : null}
                  </span>
                </div>
                {st.error ? (
                  <p role="alert" className="text-xs text-negative">
                    {st.error}
                  </p>
                ) : null}
              </div>
            ) : null}
            <FormField label="Название *" htmlFor={`${formId}-title`} error={fieldErrors.title}>
              <Input
                id={`${formId}-title`}
                value={title}
                maxLength={300}
                onChange={(event) => setTitle(event.target.value)}
                placeholder="Эфир 12.09 · разбор вопросов"
                aria-invalid={!!fieldErrors.title}
              />
            </FormField>
          </div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-[2fr_1fr]">
            <FormField
              label="Ссылка на видео *"
              htmlFor={`${formId}-url`}
              error={fieldErrors.url}
              hint="YouTube, VK и другие площадки yt-dlp. Сначала качается звук, 1080p — только секциями клипов."
            >
              <Input
                id={`${formId}-url`}
                value={url}
                inputMode="url"
                maxLength={2000}
                onChange={(event) => setUrl(event.target.value)}
                placeholder="youtube.com/watch?v=… или vk.com/video…"
                aria-invalid={!!fieldErrors.url}
                data-testid="add-source-url"
              />
            </FormField>
            <FormField
              label="Название"
              htmlFor={`${formId}-url-title`}
              error={fieldErrors.title}
              hint="Пусто — с площадки"
            >
              <Input
                id={`${formId}-url-title`}
                value={title}
                maxLength={300}
                onChange={(event) => setTitle(event.target.value)}
              />
            </FormField>
          </div>
        )}

        <div className="grid gap-3 sm:grid-cols-3">
          <FormField label="Ниша (словарь запретов)" htmlFor={`${formId}-niche`}>
            <Select id={`${formId}-niche`} value={nicheId} onChange={(event) => setNicheId(event.target.value)}>
              <option value="">Без ниши</option>
              {(niches.data ?? []).map((niche) => (
                <option key={niche.id} value={niche.id}>
                  {niche.label}
                </option>
              ))}
            </Select>
          </FormField>
          <FormField label="Язык речи" htmlFor={`${formId}-lang`}>
            <Select id={`${formId}-lang`} value={language} onChange={(event) => setLanguage(event.target.value)}>
              <option value="ru">Русский</option>
              <option value="en">Английский</option>
            </Select>
          </FormField>
          <FormField label="Кто выбирает моменты" htmlFor={`${formId}-selector`}>
            <Select
              id={`${formId}-selector`}
              value={options.selector}
              onChange={(event) => setOptions({ ...options, selector: event.target.value as Selector })}
            >
              {(Object.keys(SELECTOR_LABELS) as Selector[]).map((key) => (
                <option key={key} value={key}>
                  {SELECTOR_LABELS[key]}
                </option>
              ))}
            </Select>
          </FormField>
        </div>
        <details className="rounded-md border border-border px-3 py-2 text-sm">
          <summary className="cursor-pointer text-xs font-medium text-fg-muted">Параметры отбора</summary>
          <div className="mt-2 grid gap-3 sm:grid-cols-3">
            <FormField label="Цель длины клипа, с" htmlFor={`${formId}-target`} hint="15–58 с">
              <Input
                id={`${formId}-target`}
                inputMode="decimal"
                value={options.target}
                onChange={(event) => setOptions({ ...options, target: event.target.value })}
              />
            </FormField>
            <FormField label="Сколько моментов показать" htmlFor={`${formId}-max`} hint="1–30">
              <Input
                id={`${formId}-max`}
                inputMode="numeric"
                value={options.maxCandidates}
                onChange={(event) => setOptions({ ...options, maxCandidates: event.target.value })}
              />
            </FormField>
            <label className="flex items-center gap-2 self-end pb-1.5 text-sm text-fg">
              <input
                type="checkbox"
                checked={options.tighten}
                onChange={(event) => setOptions({ ...options, tighten: event.target.checked })}
              />
              Джамп-каты по паузам
            </label>
          </div>
        </details>

        <section className="flex flex-col gap-2 border-t border-border pt-3">
          <h3 className="text-sm font-semibold text-fg">Права</h3>
          <RightsForm
            values={rights}
            onChange={onRightsChange}
            errors={rightsErrors}
            generalError={rightsGeneral}
            disabled={pending}
            idPrefix={`${formId}-rights`}
          />
        </section>

        <DiskEstimate
          freeGb={disk?.free_gb ?? null}
          hardGb={disk?.thresholds.hard_gb ?? null}
          needGb={needGb}
          short={diskShort}
          mode={mode}
        />

        {formError ? (
          <Callout variant="negative" title="Источник не создан">
            {formError}
          </Callout>
        ) : null}
      </form>
    </Dialog>
  );
}

function DiskEstimate({
  freeGb,
  hardGb,
  needGb,
  short,
  mode,
}: {
  freeGb: number | null;
  hardGb: number | null;
  needGb: number | null;
  short: boolean;
  mode: Mode;
}) {
  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border px-3 py-2 text-xs",
        short ? "border-negative/50 bg-negative/10 text-negative" : "border-border text-fg-muted",
      )}
      data-testid="disk-estimate"
    >
      <HardDrive className="h-3.5 w-3.5" aria-hidden="true" />
      <span>
        Свободно: <span className="font-mono text-fg tabular">{formatGb(freeGb)}</span>
        {hardGb !== null ? <span> · жёсткий порог {formatGb(hardGb)}</span> : null}
      </span>
      {mode === "file" ? (
        needGb !== null ? (
          <span>
            Понадобится ≈ <span className="font-mono text-fg tabular">{formatGb(needGb)}</span> (оригинал + прокси и
            звук)
          </span>
        ) : (
          <span>Оценка появится после выбора файла</span>
        )
      ) : (
        <span>По ссылке: ≈ {URL_GB_PER_HOUR.toLocaleString("ru-RU")} ГБ на час источника (звук и прокси 540p)</span>
      )}
      {short ? <span className="font-medium">Места может не хватить: сервер откажет с 507</span> : null}
    </div>
  );
}
