"use client";

import { CircleCheck, CircleX, Download, ExternalLink, ImageOff, RefreshCw } from "lucide-react";
import Link from "next/link";
import { type ReactNode, useState } from "react";
import { toast } from "sonner";

import { useCurrentUser } from "@/components/auth/auth-context";
import { OriginalityReportCard } from "@/components/clipping/originality-report";
import { SectionError } from "@/components/common/section-error";
import { DECISION_LABELS, formatTimecode, JOB_STATUS_LABELS } from "@/components/review/review-logic";
import {
  Badge,
  Button,
  Dialog,
  EmptyState,
  FormField,
  Skeleton,
  Table,
  TableBody,
  TableHead,
  Td,
  Textarea,
  Th,
} from "@/components/ui";
import type { Schemas } from "@/lib/api/client";
import { errorMessage } from "@/lib/api/errors";
import { downloadHref, mediaSrc } from "@/lib/api/media";
import { canOperate } from "@/lib/auth";
import { formatDateTime, formatNumber, formatPct, formatUsd } from "@/lib/format";
import { routeHref } from "@/lib/nav";
import {
  type Job,
  type JobTaskItem,
  type Scene,
  useJobHistory,
  useJobScenes,
  useRegenerateScene,
} from "@/lib/queries/jobs";

import { JOB_STAGE_LABELS, jobStageLabel, QC_CHECK_LABELS, TASK_STATUS_META } from "./job-meta";

type JobStage = Schemas["JobStage"];

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs text-fg-muted">{label}</dt>
      <dd className="text-sm break-words whitespace-pre-line text-fg">{children}</dd>
    </div>
  );
}

// ---------------------------------------------------------------- сценарий

export function ScriptTab({ job }: { job: Job }) {
  const script = job.script;
  return (
    <div className="flex flex-col gap-4">
      <dl className="grid gap-3 sm:grid-cols-2">
        <Field label="Тема">{job.topic ?? "из ниши"}</Field>
        <Field label="Ниша">
          <span className="font-mono">
            {job.niche_id}
            {job.niche_version ? ` · v${job.niche_version}` : ""}
          </span>
        </Field>
        <Field label="Продукт и вариант баннера">
          {job.product_id ?? "—"}
          {job.banner_variant
            ? ` · ${job.banner_variant.code} «${job.banner_variant.name}»`
            : " · назначится на рендере"}
        </Field>
        <Field label="Приоритет">
          <span className="font-mono">{job.priority}</span>
          {job.scheduled_for ? ` · запланирован на ${formatDateTime(job.scheduled_for)}` : ""}
        </Field>
        {job.source ? (
          <Field label="Источник">
            {job.source.title} ·{" "}
            <span className="font-mono">
              {formatTimecode(job.source.start_sec)}–{formatTimecode(job.source.end_sec)}
            </span>
          </Field>
        ) : null}
        {job.parent_job_id ? (
          <Field label="Исходный ролик">
            <Link
              href={routeHref("/production", { job: String(job.parent_job_id) })}
              className="text-accent hover:underline"
            >
              #{job.parent_job_id}
            </Link>
          </Field>
        ) : null}
        {job.author_share !== null ? (
          <Field label="Доля авторского слоя">{formatPct(job.author_share * 100, 0)}</Field>
        ) : null}
        {job.kind === "clip" && job.author_script ? (
          <Field label="Авторский текст">
            {job.author_script.label ?? job.author_script.mode ?? "—"}
            {job.author_script.needs_operator_edit ? (
              <Badge variant="warning" className="ml-1">
                шаблон — перепишите своими словами
              </Badge>
            ) : null}
            {job.author_script.blocked ? (
              <Badge variant="negative" className="ml-1">
                заблокирован линтером
              </Badge>
            ) : null}
          </Field>
        ) : null}
        {job.kind === "clip" && job.source ? (
          <Field label="Студия">
            <Link
              href={routeHref("/clipping/source", {
                id: String(job.source.source_id),
                clip: job.source.clip_candidate_id !== null ? String(job.source.clip_candidate_id) : null,
              })}
              className="text-accent hover:underline"
              data-testid="open-in-studio"
            >
              Открыть в студии
            </Link>
          </Field>
        ) : null}
      </dl>
      {script ? (
        <dl className="flex flex-col gap-3 border-t border-border pt-3">
          {script.hook_text ? <Field label="Хук">{script.hook_text}</Field> : null}
          {script.body_text ? <Field label="Основной текст">{script.body_text}</Field> : null}
          {script.cta_text ? <Field label="CTA в кадре">{script.cta_text}</Field> : null}
          {script.voice_cta_text ? <Field label="CTA голосом">{script.voice_cta_text}</Field> : null}
          {script.voiceover_text ? <Field label="Текст озвучки">{script.voiceover_text}</Field> : null}
        </dl>
      ) : (
        <p className="text-sm text-fg-muted">Сценария ещё нет: он появится после стадии «Сценарий».</p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- сцены

const SCENE_REGEN_STATUSES = new Set(["pending_review", "qc_failed", "rework", "failed"]);

export function ScenesTab({ job }: { job: Job }) {
  const user = useCurrentUser();
  const scenes = useJobScenes(job.id);
  const [editing, setEditing] = useState<Scene | null>(null);
  if (scenes.isPending) return <Skeleton className="h-48 w-full" />;
  if (scenes.isError) return <SectionError error={scenes.error} onRetry={() => void scenes.refetch()} />;
  if (scenes.data.length === 0) {
    return <EmptyState compact title="Сцен нет" description="Картинки сцен появятся после стадии «Визуал»." />;
  }
  const canRegen = canOperate(user.role) && SCENE_REGEN_STATUSES.has(job.status);
  return (
    <>
      <ul className="grid grid-cols-2 gap-2 sm:grid-cols-3" data-testid="job-scenes">
        {scenes.data.map((scene) => {
          const image = mediaSrc(scene.image_url);
          return (
            <li key={scene.idx} className="flex flex-col gap-1 rounded-md border border-border p-1.5">
              <div className="relative aspect-[9/16] w-full overflow-hidden bg-video">
                {image ? (
                  // eslint-disable-next-line @next/next/no-img-element -- статический экспорт, картинки без оптимизатора
                  <img
                    src={image}
                    alt={`Сцена ${scene.idx + 1}`}
                    loading="lazy"
                    className="h-full w-full object-cover"
                  />
                ) : (
                  <ImageOff className="absolute inset-0 m-auto h-5 w-5 text-white/50" aria-hidden="true" />
                )}
              </div>
              <div className="flex items-center justify-between font-mono text-2xs text-fg-muted">
                <span>#{scene.idx + 1}</span>
                <span>{scene.duration_sec !== null ? `${formatNumber(scene.duration_sec, 1)} с` : ""}</span>
                <span title="seed">{scene.seed ?? ""}</span>
              </div>
              {scene.prompt ? (
                <p className="line-clamp-3 text-xs text-fg-muted" title={scene.prompt}>
                  {scene.prompt}
                </p>
              ) : null}
              {canRegen ? (
                <Button size="xs" variant="outline" onClick={() => setEditing(scene)}>
                  <RefreshCw className="h-3 w-3" aria-hidden="true" />
                  Перегенерировать
                </Button>
              ) : null}
            </li>
          );
        })}
      </ul>
      {editing ? <RegenerateDialog job={job} scene={editing} onClose={() => setEditing(null)} /> : null}
    </>
  );
}

function RegenerateDialog({ job, scene, onClose }: { job: Job; scene: Scene; onClose: () => void }) {
  const regenerate = useRegenerateScene();
  const [prompt, setPrompt] = useState(scene.prompt ?? "");
  const [error, setError] = useState<string | null>(null);
  return (
    <Dialog
      open
      onClose={onClose}
      title={`Сцена ${scene.idx + 1}: перегенерировать`}
      description="Платно: одна картинка через провайдера изображений, затем рендер и QC заново. Ролик уйдёт из приёмки до готовности."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Отмена
          </Button>
          <Button
            disabled={regenerate.isPending}
            onClick={() => {
              setError(null);
              regenerate.mutate(
                { jobId: job.id, sceneIdx: scene.idx, prompt: prompt !== (scene.prompt ?? "") ? prompt : null },
                {
                  onSuccess: (accepted) => {
                    toast.success(`Сцена ${scene.idx + 1} в работе (задача #${accepted.task_id})`);
                    onClose();
                  },
                  onError: (err) => setError(errorMessage(err)),
                },
              );
            }}
          >
            Перегенерировать
          </Button>
        </>
      }
    >
      <FormField label="Промпт (англ.)" htmlFor="scene-prompt" hint="Без изменений — тот же промпт, новый seed">
        <Textarea
          id="scene-prompt"
          rows={5}
          maxLength={2000}
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
        />
      </FormField>
      {error ? (
        <p role="alert" className="mt-2 text-sm text-negative">
          {error}
        </p>
      ) : null}
    </Dialog>
  );
}

// ---------------------------------------------------------------- QC

/**
 * Причины провала QC. Провал только отчёта об оригинальности (заглушка или шаблон вместо текста,
 * повтор реплик, права) не имеет своего кода QcCheck — берём названия провалившихся проверок отчёта.
 */
function qcFailReasons(job: Job): string {
  const qc = job.qc;
  const codes = (qc?.failed_checks ?? []).map((code) => QC_CHECK_LABELS[code] ?? code);
  const report = job.originality;
  if (report && report.verdict === "fail" && report.summary.gate_applies) {
    const titles = report.checks.filter((check) => check.status === "fail").map((check) => check.title);
    for (const title of titles) if (!codes.includes(title)) codes.push(title);
  }
  return codes.length ? codes.join(", ") : "причина — в логах стадии «QC»";
}

export function QcTab({ job }: { job: Job }) {
  const qc = job.qc;
  const report = job.kind === "clip" ? (job.originality ?? null) : null;
  if (!qc && !report) return <p className="text-sm text-fg-muted">Отчёта QC ещё нет: он появится после стадии «QC».</p>;
  if (!qc && report) return <OriginalityReportCard report={report} />;
  if (!qc) return null;
  return (
    <div className="flex flex-col gap-3">
      <p className={qc.passed ? "text-sm text-positive" : "text-sm text-negative"}>
        {qc.passed ? "QC пройден" : `QC не пройден: ${qcFailReasons(job)}`}
      </p>
      {report ? <OriginalityReportCard report={report} /> : null}
      {qc.checks.length ? (
        <Table minWidth={420}>
          <TableHead>
            <tr>
              <Th>Проверка</Th>
              <Th>Итог</Th>
              <Th className="text-right">Измерено</Th>
              <Th className="text-right">Порог</Th>
            </tr>
          </TableHead>
          <TableBody>
            {qc.checks.map((check) => (
              <tr key={check.code} className="h-8">
                <Td>{QC_CHECK_LABELS[check.code] ?? check.code}</Td>
                <Td>
                  {check.ok ? (
                    <span className="inline-flex items-center gap-1 text-positive">
                      <CircleCheck className="h-3.5 w-3.5" aria-hidden="true" />
                      ок
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 text-negative">
                      <CircleX className="h-3.5 w-3.5" aria-hidden="true" />
                      провал
                    </span>
                  )}
                </Td>
                <Td className="text-right font-mono text-xs">{check.measured ?? "—"}</Td>
                <Td className="text-right font-mono text-xs text-fg-muted">{check.threshold ?? "—"}</Td>
              </tr>
            ))}
          </TableBody>
        </Table>
      ) : null}
      {qc.warnings.length ? (
        <ul className="flex flex-col gap-1">
          {qc.warnings.map((warning, index) => (
            <li key={`${warning.code}-${index}`} className="text-sm">
              <Badge variant="warning">{QC_CHECK_LABELS[warning.code] ?? warning.code}</Badge>{" "}
              <span className="text-fg-muted">{warning.detail}</span>
              {warning.job_id ? (
                <Link
                  href={routeHref("/production", { job: String(warning.job_id) })}
                  className="ml-1 text-xs text-accent hover:underline"
                >
                  #{warning.job_id}
                </Link>
              ) : null}
              {warning.similarity !== null && warning.similarity !== undefined ? (
                <span className="ml-1 font-mono text-xs">{formatPct(warning.similarity * 100, 0)}</span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------- стоимость

export function CostTab({ job, tasks }: { job: Job; tasks: readonly JobTaskItem[] | undefined }) {
  const paid = (tasks ?? []).filter((task) => task.cost_usd > 0);
  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm">
        Себестоимость ролика: <span className="font-mono font-semibold tabular">{formatUsd(job.cost_usd, 4)}</span>
      </p>
      <Table minWidth={420}>
        <TableHead>
          <tr>
            <Th>Стадия</Th>
            <Th className="text-right">Попыток</Th>
            <Th className="text-right">Время</Th>
            <Th className="text-right">Стоимость</Th>
          </tr>
        </TableHead>
        <TableBody>
          {job.stages.map((stage) => (
            <tr key={stage.stage} className="h-8">
              <Td>{JOB_STAGE_LABELS[stage.stage as JobStage] ?? stage.stage}</Td>
              <Td className="text-right font-mono text-xs tabular">{stage.attempts}</Td>
              <Td className="text-right font-mono text-xs tabular">
                {stage.duration_ms !== null ? `${formatNumber(stage.duration_ms / 1000, 1)} с` : "—"}
              </Td>
              <Td className="text-right font-mono text-xs tabular">{formatUsd(stage.cost_usd, 4)}</Td>
            </tr>
          ))}
        </TableBody>
      </Table>
      {paid.length ? (
        <details className="text-sm">
          <summary className="cursor-pointer text-fg-muted">Платные задачи ({paid.length})</summary>
          <ul className="mt-2 flex flex-col gap-1 font-mono text-xs">
            {paid.map((task) => (
              <li key={task.task_id} className="flex justify-between gap-2">
                <span>
                  #{task.task_id} · {jobStageLabel(task.stage)} · {TASK_STATUS_META[task.status].label.toLowerCase()}
                </span>
                <span className="tabular">{formatUsd(task.cost_usd, 4)}</span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      <p className="text-xs text-fg-muted">
        Платный вызов пишется в журнал сразу после ответа провайдера: деньги учитываются, даже если стадия потом упала.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------- файлы

export function FilesTab({ job }: { job: Job }) {
  const files: Array<{ key: string; label: string; url: string | null }> = [
    { key: "preview", label: "Превью (лёгкое)", url: job.media.preview_url },
    { key: "final", label: "Финальный файл", url: job.media.final_url },
    { key: "poster", label: "Постер", url: job.media.poster_url },
  ];
  const any = files.some((file) => mediaSrc(file.url));
  if (!any)
    return <p className="text-sm text-fg-muted">Файлов ещё нет: превью, финал и постер появятся после рендера.</p>;
  return (
    <ul className="flex flex-col divide-y divide-border rounded-md border border-border" data-testid="job-files">
      {files.map((file) => {
        const src = mediaSrc(file.url);
        const download = downloadHref(file.url);
        return (
          <li key={file.key} className="flex h-10 items-center justify-between gap-2 px-3 text-sm">
            <span className={src ? "text-fg" : "text-fg-muted"}>{file.label}</span>
            {src ? (
              <span className="flex items-center gap-3 text-xs">
                <a
                  href={src}
                  target="_blank"
                  rel="noopener"
                  className="inline-flex items-center gap-1 text-accent hover:underline"
                >
                  <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                  открыть
                </a>
                {download ? (
                  <a href={download} className="inline-flex items-center gap-1 text-accent hover:underline">
                    <Download className="h-3.5 w-3.5" aria-hidden="true" />
                    скачать
                  </a>
                ) : null}
              </span>
            ) : (
              <span className="text-xs text-fg-muted">нет</span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

// ---------------------------------------------------------------- история

const HISTORY_KIND_LABELS = { status: "Статус", review: "Решение", task: "Задача", edit: "Правка" } as const;
const VIA_LABELS = { web: "веб", telegram: "Telegram", api: "API" } as const;

function historyValue(value: string | null): string {
  if (!value) return "—";
  if (value in JOB_STATUS_LABELS) return JOB_STATUS_LABELS[value as keyof typeof JOB_STATUS_LABELS];
  if (value in JOB_STAGE_LABELS) return JOB_STAGE_LABELS[value as JobStage];
  if (value in DECISION_LABELS) return DECISION_LABELS[value as keyof typeof DECISION_LABELS];
  return value;
}

export function HistoryTab({ job }: { job: Job }) {
  const history = useJobHistory(job.id);
  if (history.isPending) return <Skeleton className="h-40 w-full" />;
  if (history.isError) return <SectionError error={history.error} onRetry={() => void history.refetch()} />;
  if (history.data.length === 0) return <p className="text-sm text-fg-muted">Истории ещё нет.</p>;
  return (
    <ol className="flex flex-col gap-2" data-testid="job-history">
      {[...history.data].reverse().map((item, index) => (
        <li key={`${item.at}-${index}`} className="flex gap-3 text-sm">
          <span className="w-28 shrink-0 font-mono text-2xs text-fg-muted tabular">
            {formatDateTime(item.at, { short: true })}
          </span>
          <div className="min-w-0">
            <span className="text-xs text-fg-muted">{HISTORY_KIND_LABELS[item.kind]}: </span>
            <span className="text-fg">
              {item.from ? `${historyValue(item.from)} → ` : ""}
              {historyValue(item.to)}
            </span>
            {item.by || item.via ? (
              <span className="text-xs text-fg-muted">
                {" "}
                · {item.by ?? "система"}
                {item.via ? ` (${VIA_LABELS[item.via]})` : ""}
              </span>
            ) : null}
            {item.comment ? <p className="text-xs break-words text-fg-muted">«{item.comment}»</p> : null}
          </div>
        </li>
      ))}
    </ol>
  );
}
