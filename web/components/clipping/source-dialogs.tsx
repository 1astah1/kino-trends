"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useId, useState } from "react";
import { toast } from "sonner";

import { Badge, Button, Callout, Dialog, DetailGrid, KeyValue, Skeleton, StatusBadge } from "@/components/ui";
import type { Schemas } from "@/lib/api/client";
import { ConflictError, errorMessage, ValidationError } from "@/lib/api/errors";
import { formatDateTime, formatDay } from "@/lib/format";
import { deferReasonLabel } from "@/lib/status-meta";
import { routeHref } from "@/lib/nav";
import {
  type Source,
  useDeleteSource,
  usePutRights,
  useReprocessSource,
  useSourceRights,
} from "@/lib/queries/clipping";

import { ALLOWLIST_LABELS, RIGHTS_TYPE_LABELS, SOURCE_STEP_LABELS, STEP_STATUS_META } from "./labels";
import { RightsForm } from "./rights-form";
import { type RightsField, rightsBody, rightsFormFrom, rightsServerErrors, validateRights } from "./rights-rules";

type ReprocessStep = Schemas["SourceReprocessStep"];

/** Длинные значения прав — во всю ширину и без обрезки. */
const WIDE = "sm:col-span-2 [&>dd]:whitespace-normal [&>dd]:break-words";

/** Шаги конвейера источника с прогрессом, кусками ASR и местом в тяжёлой очереди. */
export function PipelineSteps({ source }: { source: Source }) {
  return (
    <ol className="flex flex-col gap-1.5" data-testid="source-pipeline">
      {source.pipeline.map((step) => {
        const pct = step.progress === null || step.progress === undefined ? null : Math.round(step.progress);
        return (
          <li key={step.step} className="flex flex-wrap items-center gap-2 text-xs">
            <StatusBadge status={step.status} map={STEP_STATUS_META} />
            <span className="text-fg">{SOURCE_STEP_LABELS[step.step]}</span>
            {step.status === "running" && pct !== null ? (
              <span className="flex items-center gap-1">
                <span className="h-1 w-24 overflow-hidden rounded-full bg-surface-2" aria-hidden="true">
                  <span className="block h-full bg-machine" style={{ width: `${pct}%` }} />
                </span>
                <span className="font-mono text-2xs text-fg-muted tabular">{pct}%</span>
              </span>
            ) : null}
            {step.chunks_total ? (
              <span className="font-mono text-2xs text-fg-muted tabular">
                куски {step.chunks_done ?? 0}/{step.chunks_total}
              </span>
            ) : null}
            {step.queue_position ? (
              <span className="font-mono text-2xs text-fg-muted">№{step.queue_position} в тяжёлой очереди</span>
            ) : null}
            {step.defer_reason ? (
              <Badge variant="warning">отложено: {deferReasonLabel(step.defer_reason)}</Badge>
            ) : null}
            {step.error ? <span className="text-negative">{step.error}</span> : null}
          </li>
        );
      })}
    </ol>
  );
}

/** Права источника: просмотр и правка (PUT, аудит). Правила те же, что при создании. */
export function RightsDialog({ source, canEdit, onClose }: { source: Source; canEdit: boolean; onClose: () => void }) {
  const formId = useId();
  const rights = useSourceRights(source.id);
  const put = usePutRights(source.id);
  const [editing, setEditing] = useState(false);
  const [values, setValues] = useState(() => rightsFormFrom(source.rights));
  const [errors, setErrors] = useState<Partial<Record<RightsField, string>>>({});
  const [general, setGeneral] = useState<string | null>(null);
  const data = rights.data ?? source.rights;

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const errs = validateRights(values);
    setErrors(errs);
    setGeneral(null);
    if (Object.keys(errs).length) return;
    try {
      await put.mutateAsync(rightsBody(values));
      toast.success("Права обновлены");
      setEditing(false);
    } catch (error) {
      if (error instanceof ValidationError) {
        const mapped = rightsServerErrors(error, null);
        setErrors(mapped.fields);
        setGeneral(mapped.general ?? error.message);
      } else {
        setGeneral(errorMessage(error));
      }
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title="Права на источник"
      description="Чужие права: ролик проходит гейт авторского вклада ≥ 30%, атрибуция уходит в описание."
      className="max-w-2xl"
      footer={
        editing ? (
          <>
            <Button variant="ghost" onClick={() => setEditing(false)}>
              Отмена
            </Button>
            <Button type="submit" form={formId} disabled={put.isPending}>
              {put.isPending ? "Сохраняем…" : "Сохранить права"}
            </Button>
          </>
        ) : canEdit ? (
          <Button
            variant="outline"
            onClick={() => {
              setValues(rightsFormFrom(data));
              setEditing(true);
            }}
          >
            Изменить
          </Button>
        ) : null
      }
    >
      {editing ? (
        <form id={formId} onSubmit={onSubmit} noValidate>
          <RightsForm
            values={values}
            onChange={setValues}
            errors={errors}
            generalError={general}
            idPrefix={`${formId}-r`}
            allowProof={false}
          />
        </form>
      ) : rights.isPending && !data ? (
        <Skeleton className="h-40 w-full" />
      ) : (
        <div className="flex flex-col gap-3" data-testid="rights-view">
          <DetailGrid className="sm:grid-cols-2">
            <KeyValue label="Основание" value={RIGHTS_TYPE_LABELS[data.rights_type]} />
            <KeyValue label="Правообладатель" value={data.owner_name} />
            <KeyValue label="Атрибуция" value={data.attribution_text ?? "—"} className={WIDE} />
            <KeyValue
              label="Лицензия"
              value={[data.license, data.license_url].filter(Boolean).join(" · ") || "—"}
              className={WIDE}
            />
            <KeyValue label="Действуют до" value={data.valid_until ? formatDay(data.valid_until) : "бессрочно"} />
            <KeyValue label="Content ID" value={ALLOWLIST_LABELS[data.allowlist_status]} />
            <KeyValue label="Подтверждение" value={data.proof_note} className={WIDE} />
            <KeyValue label="Записано" value={formatDateTime(data.updated_at ?? data.recorded_at)} />
          </DetailGrid>
          {data.gate_applies ? (
            <Callout variant="warning" title="Гейт авторского вклада">
              Для этого источника ролик проходит QC, только если доля авторского вклада не меньше 30%.
            </Callout>
          ) : null}
          {data.warnings.length ? (
            <ul className="flex flex-col gap-1 text-xs text-fg-muted">
              {data.warnings.map((warning) => (
                <li key={warning.code}>• {warning.label}</li>
              ))}
            </ul>
          ) : null}
        </div>
      )}
    </Dialog>
  );
}

const REPROCESS_STEPS: ReadonlyArray<{ value: ReprocessStep; label: string; hint: string }> = [
  { value: "prepare", label: "Подготовка", hint: "прокси, волна, раскадровка, проверка логотипов (тяжёлая)" },
  { value: "transcribe", label: "Распознавание", hint: "транскрипт заново (тяжёлая или облако)" },
  { value: "select", label: "Отбор моментов", hint: "эвристика или LLM по текущему транскрипту" },
  { value: "scenes", label: "Склейки", hint: "PySceneDetect по прокси (тяжёлая)" },
];

export function ReprocessDialog({ source, onClose }: { source: Source; onClose: () => void }) {
  const formId = useId();
  const [steps, setSteps] = useState<ReprocessStep[]>([]);
  const [error, setError] = useState<string | null>(null);
  const mutation = useReprocessSource(source.id);

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!steps.length) {
      setError("Выберите хотя бы один шаг");
      return;
    }
    try {
      const accepted = await mutation.mutateAsync({
        steps: REPROCESS_STEPS.map((s) => s.value).filter((s) => steps.includes(s)),
      });
      toast.success(`Поставлено в очередь (задача №${accepted.task_id})`);
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title="Повторить шаги обработки"
      description="Тяжёлые шаги идут через один слот и щадят живой сайт; кандидаты с роликами не пропадут."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Отмена
          </Button>
          <Button type="submit" form={formId} disabled={mutation.isPending}>
            Запустить
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={onSubmit} className="flex flex-col gap-2">
        {REPROCESS_STEPS.map((step) => (
          <label key={step.value} className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              className="mt-1"
              checked={steps.includes(step.value)}
              onChange={(event) =>
                setSteps((prev) =>
                  event.target.checked ? [...prev, step.value] : prev.filter((s) => s !== step.value),
                )
              }
            />
            <span className="flex flex-col">
              <span className="text-fg">{step.label}</span>
              <span className="text-xs text-fg-muted">{step.hint}</span>
            </span>
          </label>
        ))}
        {error ? (
          <p role="alert" className="text-xs text-negative">
            {error}
          </p>
        ) : null}
      </form>
    </Dialog>
  );
}

export function DeleteSourceDialog({ source, onClose }: { source: Source; onClose: () => void }) {
  const router = useRouter();
  const [purge, setPurge] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mutation = useDeleteSource();

  const onDelete = async () => {
    try {
      await mutation.mutateAsync({ id: source.id, purgeFiles: purge });
      toast.success(`Источник №${source.id} удалён`);
      onClose();
      router.push(routeHref("/clipping"));
    } catch (err) {
      setError(
        err instanceof ConflictError && err.code === "source_in_use"
          ? "По источнику идут ролики: дождитесь их или отмените в Конвейере"
          : errorMessage(err),
      );
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title="Удалить источник?"
      description={source.title}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Отмена
          </Button>
          <Button variant="destructive" onClick={() => void onDelete()} disabled={mutation.isPending}>
            Удалить
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-2 text-sm">
        <p className="text-fg-muted">
          Источник исчезнет из списка, задачи обработки отменятся. Готовые ролики и пакеты останутся.
        </p>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={purge} onChange={(event) => setPurge(event.target.checked)} />
          Сразу удалить файлы (оригинал, прокси, звук)
        </label>
        {error ? (
          <p role="alert" className="text-xs text-negative">
            {error}
          </p>
        ) : null}
      </div>
    </Dialog>
  );
}
