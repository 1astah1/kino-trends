"use client";

import { CircleCheck, LoaderCircle, OctagonAlert, RefreshCw } from "lucide-react";

import { Badge, Button } from "@/components/ui";
import { errorMessage } from "@/lib/api/errors";
import { cn } from "@/lib/cn";
import type { BannerValidateResponse, BannerWarning } from "@/lib/queries/banners";
import type { SafeZoneCatalog } from "@/lib/safe-zones";

import type { EditorDraft } from "./draft";
import { type DraftIssue, describeValidationError } from "./draft-issues";
import { PanelSection } from "./fields";
import { layerKindLabel, SEVERITY_META, validationCodeLabel } from "./labels";

function profileLabel(catalog: SafeZoneCatalog | undefined, id: string | null | undefined): string | null {
  if (!id) return null;
  return catalog?.items.find((item) => item.id === id)?.label ?? id;
}

/**
 * Живой список замечаний серверной проверки (источник истины: те же правила, что при рендере).
 * Клик по замечанию выбирает слой на холсте. Пока в черновике есть поля вне пределов схемы
 * (`localIssues`), на сервер он не уходит — список показывает, что поправить.
 */
export function ValidationList({
  result,
  isFetching,
  isError,
  error,
  disabled,
  catalog,
  layerKinds,
  localIssues = [],
  draft,
  onSelectLayer,
  onRetry,
}: {
  result: BannerValidateResponse | undefined;
  isFetching: boolean;
  isError: boolean;
  error: unknown;
  disabled: boolean;
  catalog: SafeZoneCatalog | undefined;
  layerKinds: Record<string, string>;
  localIssues?: DraftIssue[];
  draft?: EditorDraft | null;
  onSelectLayer: (id: string) => void;
  onRetry: () => void;
}) {
  const warnings = [...(result?.warnings ?? [])].sort(
    (a, b) => (SEVERITY_META[a.severity]?.rank ?? 9) - (SEVERITY_META[b.severity]?.rank ?? 9),
  );
  const errors = warnings.filter((w) => w.severity === "error").length;
  const warns = warnings.filter((w) => w.severity === "warn").length;

  return (
    <PanelSection
      title="Проверка"
      testId="banner-validation"
      actions={
        <span className="flex items-center gap-1.5" aria-live="polite">
          {isFetching ? (
            <span className="inline-flex items-center gap-1 text-2xs text-machine" data-testid="validation-pending">
              <LoaderCircle className="h-3 w-3 animate-spin" aria-hidden="true" />
              проверяю
            </span>
          ) : null}
          {localIssues.length && !disabled ? (
            <Badge variant="negative">полей: {localIssues.length}</Badge>
          ) : result && !isFetching ? (
            errors ? (
              <Badge variant="negative">ошибок: {errors}</Badge>
            ) : warns ? (
              <Badge variant="warning">замечаний: {warns}</Badge>
            ) : (
              <Badge variant="positive">
                <CircleCheck className="h-3 w-3" aria-hidden="true" />
                чисто
              </Badge>
            )
          ) : null}
        </span>
      }
    >
      {disabled ? (
        <p className="text-xs text-fg-muted">Проверку запускают оператор и владелец.</p>
      ) : localIssues.length ? (
        <div className="flex flex-col gap-1" data-testid="draft-issues">
          <p className="text-2xs text-fg-muted">Поля вне допустимых значений — исправьте, и проверка продолжится:</p>
          <ul className="flex flex-col gap-1">
            {localIssues.map((issue) => (
              <DraftIssueItem key={issue.key} issue={issue} onSelectLayer={onSelectLayer} />
            ))}
          </ul>
        </div>
      ) : isError ? (
        <div className="flex flex-col gap-1.5 rounded-md border border-negative/40 bg-negative/10 p-2 text-xs text-negative">
          <span>Проверка не прошла: {describeValidationError(error, draft) ?? errorMessage(error)}</span>
          <Button variant="outline" size="xs" className="self-start" onClick={onRetry}>
            <RefreshCw className="h-3 w-3" aria-hidden="true" />
            Повторить
          </Button>
        </div>
      ) : !result ? (
        <p className="text-xs text-fg-muted">Проверяю…</p>
      ) : warnings.length === 0 ? (
        <p className="flex items-center gap-1.5 text-xs text-positive" data-testid="validation-clean">
          <CircleCheck className="h-3.5 w-3.5" aria-hidden="true" />
          Замечаний нет по профилям: {result.profile_ids.map((id) => profileLabel(catalog, id)).join(", ")}
        </p>
      ) : (
        <ul className="flex flex-col gap-1" data-testid="banner-warnings">
          {warnings.map((warning, index) => (
            <WarningItem
              key={`${warning.code}-${warning.layer_id ?? ""}-${warning.profile_id ?? ""}-${index}`}
              warning={warning}
              profile={profileLabel(catalog, warning.profile_id)}
              layerKind={warning.layer_id ? layerKinds[warning.layer_id] : undefined}
              onSelectLayer={onSelectLayer}
            />
          ))}
        </ul>
      )}
      {result && (result.fallback_used || result.missing_creatives.length) ? (
        <p className="text-2xs text-fg-muted">
          {result.fallback_used ? "Сейчас в кадре запасная плашка. " : ""}
          {result.missing_creatives.length ? `Не найдено креативов: ${result.missing_creatives.length}.` : ""}
        </p>
      ) : null}
    </PanelSection>
  );
}

function DraftIssueItem({ issue, onSelectLayer }: { issue: DraftIssue; onSelectLayer: (id: string) => void }) {
  const content = (
    <>
      <OctagonAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-negative" aria-hidden="true" />
      <span className="min-w-0 flex-1 text-xs text-fg">{issue.message}</span>
    </>
  );
  return (
    <li data-testid="draft-issue" data-key={issue.key}>
      {issue.layerId ? (
        <button
          type="button"
          className="flex w-full items-start gap-2 rounded-md bg-negative/10 p-1.5 text-left transition-colors duration-150 hover:bg-surface-2"
          onClick={() => onSelectLayer(issue.layerId!)}
        >
          {content}
        </button>
      ) : (
        <div className="flex items-start gap-2 rounded-md bg-negative/10 p-1.5">{content}</div>
      )}
    </li>
  );
}

function WarningItem({
  warning,
  profile,
  layerKind,
  onSelectLayer,
}: {
  warning: BannerWarning;
  profile: string | null;
  layerKind: string | undefined;
  onSelectLayer: (id: string) => void;
}) {
  const meta = SEVERITY_META[warning.severity] ?? SEVERITY_META.info;
  const Icon = meta.icon;
  const clickable = !!warning.layer_id;
  const content = (
    <>
      <Icon className={cn("mt-0.5 h-3.5 w-3.5 shrink-0", meta.text)} aria-hidden="true" />
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="text-xs font-medium text-fg">{validationCodeLabel(warning.code)}</span>
        {warning.message ? <span className="text-2xs text-fg-muted">{warning.message}</span> : null}
        <span className="flex flex-wrap gap-1 text-2xs text-fg-muted">
          <span className="sr-only">{meta.label}. </span>
          {layerKind ? <span>{layerKindLabel(layerKind)}</span> : null}
          {profile ? <span>· {profile}</span> : null}
          <span className="font-mono opacity-70">· {warning.code}</span>
        </span>
      </span>
    </>
  );
  return (
    <li data-code={warning.code} data-severity={warning.severity} data-testid="banner-warning">
      {clickable ? (
        <button
          type="button"
          className={cn(
            "flex w-full items-start gap-2 rounded-md p-1.5 text-left transition-colors duration-150 hover:bg-surface-2",
            meta.bg,
          )}
          onClick={() => onSelectLayer(warning.layer_id!)}
        >
          {content}
        </button>
      ) : (
        <div className={cn("flex items-start gap-2 rounded-md p-1.5", meta.bg)}>{content}</div>
      )}
    </li>
  );
}
