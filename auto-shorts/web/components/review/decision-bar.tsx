"use client";

import { Check, CircleAlert, Info, RotateCcw, ShieldAlert, Undo2, X } from "lucide-react";
import type { ReactNode } from "react";

import { JOB_STAGE_LABELS, QC_CHECK_LABELS } from "@/components/production/job-meta";
import { Button, Input } from "@/components/ui";
import type { Schemas } from "@/lib/api/client";
import { cn } from "@/lib/cn";
import type { RejectReasonItem } from "@/lib/queries/review";

import type { ReviewMode } from "./review-logic";

type JobStage = Schemas["JobStage"];
type QcCheck = Schemas["QcCheck"];

export interface ReviewAlert {
  kind: "conflict" | "error" | "info";
  text: string;
}

function Kbd({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <kbd
      className={cn(
        "rounded-sm border border-current/30 px-1 font-mono text-2xs leading-4 font-semibold opacity-80",
        className,
      )}
    >
      {children}
    </kbd>
  );
}

const ALERT_STYLE: Record<ReviewAlert["kind"], { box: string; icon: typeof Info; iconClass: string }> = {
  conflict: { box: "border-warning/50 bg-warning/10", icon: CircleAlert, iconClass: "text-warning" },
  error: { box: "border-negative/50 bg-negative/10", icon: CircleAlert, iconClass: "text-negative" },
  info: { box: "border-accent/40 bg-accent/10", icon: Info, iconClass: "text-accent" },
};

export interface DecisionBarProps {
  mode: ReviewMode;
  /** Есть ролик, по которому можно решать (pending_review | qc_failed) и право решать. */
  canDecide: boolean;
  canOperate: boolean;
  qcFailed: boolean;
  failedChecks: readonly QcCheck[];
  /**
   * Нарезка с чужими правами и проваленным отчётом об оригинальности: причины провала. Пакет такому
   * ролику не соберётся и после «Принять вопреки QC» (OWNER-DECISIONS п. 5) — диалог обязан это сказать.
   */
  originalityBlock?: readonly string[] | null;
  pending: boolean;
  reasons: readonly RejectReasonItem[];
  stages: readonly JobStage[];
  defaultReworkStage: JobStage | null;
  autoRestart: boolean;
  comment: string;
  alert: ReviewAlert | null;
  onDismissAlert: () => void;
  onCommentChange: (value: string) => void;
  onAutoRestartChange: (value: boolean) => void;
  onApprove: () => void;
  onConfirmOverride: () => void;
  onOpenReject: () => void;
  onReject: (reason: Schemas["RejectReason"]) => void;
  onOpenRework: () => void;
  onRework: (stage: JobStage) => void;
  onCancelMode: () => void;
  onUndo: () => void;
}

/**
 * Панель решения (§7.3 «DecisionBar(ReasonGrid, UndoToast)»): A — одобрить, R — на доработку
 * (стадия цифрой или Enter), X — отклонить (причина цифрой), U — отменить последнее решение.
 * На телефоне — крупные кнопки внизу экрана.
 */
export function DecisionBar(props: DecisionBarProps) {
  const {
    mode,
    canDecide,
    canOperate,
    qcFailed,
    pending,
    reasons,
    stages,
    defaultReworkStage,
    autoRestart,
    comment,
    alert,
  } = props;
  const disabled = !canDecide || pending;

  return (
    <div className="flex flex-col gap-2" data-testid="decision-bar">
      {alert ? (
        <div
          role="alert"
          data-testid="review-alert"
          data-kind={alert.kind}
          className={cn("flex items-start gap-2 rounded-md border px-3 py-2 text-sm", ALERT_STYLE[alert.kind].box)}
        >
          {(() => {
            const Icon = ALERT_STYLE[alert.kind].icon;
            return (
              <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", ALERT_STYLE[alert.kind].iconClass)} aria-hidden="true" />
            );
          })()}
          <p className="min-w-0 flex-1 text-fg">{alert.text}</p>
          <button
            type="button"
            onClick={props.onDismissAlert}
            className="shrink-0 text-fg-muted hover:text-fg"
            aria-label="Скрыть сообщение"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
      ) : null}

      {mode === "reject" ? (
        <div
          className="flex flex-col gap-2 rounded-md border border-negative/40 bg-negative/5 p-2"
          role="group"
          aria-label="Причина отклонения"
          data-testid="reject-reasons"
        >
          <p className="text-xs text-fg-muted">
            Причина отклонения: цифра или клик. <Kbd>Esc</Kbd> — назад.
          </p>
          <div className="grid grid-cols-1 gap-1 sm:grid-cols-2">
            {reasons.map((reason) => (
              <button
                key={reason.code}
                type="button"
                disabled={pending}
                onClick={() => props.onReject(reason.code)}
                data-reason={reason.code}
                className="flex h-9 items-center gap-2 rounded-md border border-border bg-surface px-2 text-left text-sm hover:border-negative/60 hover:bg-negative/10 disabled:opacity-50 sm:h-8"
              >
                <span className="w-4 shrink-0 text-center font-mono text-xs text-negative">{reason.hotkey ?? "·"}</span>
                <span className="truncate">{reason.label}</span>
              </button>
            ))}
          </div>
        </div>
      ) : null}

      {mode === "rework" ? (
        <div
          className="flex flex-col gap-2 rounded-md border border-warning/40 bg-warning/5 p-2"
          role="group"
          aria-label="Стадия доработки"
          data-testid="rework-stages"
        >
          <p className="text-xs text-fg-muted">
            С какой стадии переделать: цифра или клик; <Kbd>Enter</Kbd> — с «
            {defaultReworkStage ? JOB_STAGE_LABELS[defaultReworkStage] : "—"}». <Kbd>Esc</Kbd> — назад.
          </p>
          <div className="flex flex-wrap gap-1">
            {stages.map((stage, index) => (
              <button
                key={stage}
                type="button"
                disabled={pending}
                onClick={() => props.onRework(stage)}
                data-stage={stage}
                className={cn(
                  "flex h-9 items-center gap-1.5 rounded-md border px-2 text-sm hover:bg-warning/10 disabled:opacity-50 sm:h-8",
                  stage === defaultReworkStage ? "border-warning/70 bg-warning/10" : "border-border bg-surface",
                )}
              >
                <span className="font-mono text-xs text-warning">{index + 1}</span>
                {JOB_STAGE_LABELS[stage]}
              </button>
            ))}
          </div>
          <label className="inline-flex items-center gap-2 text-xs text-fg-muted">
            <input
              type="checkbox"
              checked={autoRestart}
              onChange={(event) => props.onAutoRestartChange(event.target.checked)}
              className="h-3.5 w-3.5 accent-[var(--accent)]"
            />
            Перезапустить сразу (иначе ролик ждёт правок в статусе «на доработке»)
          </label>
        </div>
      ) : null}

      {mode === "override" ? (
        <div
          className="flex flex-col gap-2 rounded-md border border-negative/50 bg-negative/10 p-2"
          role="group"
          aria-label="Принять вопреки QC"
          data-testid="override-confirm"
        >
          <p className="text-sm text-fg">
            Ролик не прошёл QC
            {props.failedChecks.length || props.originalityBlock?.length
              ? `: ${[
                  ...props.failedChecks.map((code) => QC_CHECK_LABELS[code] ?? code),
                  ...(props.originalityBlock ?? []),
                ].join(", ")}`
              : ""}
            . Принять вопреки QC? Решение попадёт в аудит.
          </p>
          {props.originalityBlock?.length ? (
            <p className="text-xs text-negative" data-testid="override-package-blocked">
              Отчёт об оригинальности провален, а права на источник чужие: пакет «К заливке» не соберётся и после
              принятия. Лучше «Доработка» — допишите авторский слой.
            </p>
          ) : null}
          <div className="flex gap-2">
            <Button variant="destructive" size="sm" disabled={pending} onClick={props.onConfirmOverride}>
              <ShieldAlert className="h-3.5 w-3.5" aria-hidden="true" />
              Принять вопреки QC <Kbd>A</Kbd>
            </Button>
            <Button variant="ghost" size="sm" onClick={props.onCancelMode}>
              Отмена <Kbd>Esc</Kbd>
            </Button>
          </div>
        </div>
      ) : null}

      <div className="grid grid-cols-4 gap-1.5">
        <Button
          variant="default"
          className="h-11 flex-col gap-0 sm:h-9 sm:flex-row sm:gap-1.5"
          disabled={disabled}
          onClick={props.onApprove}
          aria-keyshortcuts="A"
          data-testid="decision-approve"
          title={qcFailed ? "Не прошёл QC: принять может только владелец" : "Одобрить"}
        >
          <Check className="h-4 w-4" aria-hidden="true" />
          <span>{qcFailed ? "Вопреки QC" : "Одобрить"}</span>
          <Kbd className="hidden sm:inline">A</Kbd>
        </Button>
        <Button
          variant="outline"
          className={cn(
            "h-11 flex-col gap-0 sm:h-9 sm:flex-row sm:gap-1.5",
            mode === "rework" && "border-warning bg-warning/10",
          )}
          disabled={disabled}
          onClick={props.onOpenRework}
          aria-keyshortcuts="R"
          aria-expanded={mode === "rework"}
          data-testid="decision-rework"
        >
          <RotateCcw className="h-4 w-4" aria-hidden="true" />
          <span>Доработка</span>
          <Kbd className="hidden sm:inline">R</Kbd>
        </Button>
        <Button
          variant="destructive"
          className={cn("h-11 flex-col gap-0 sm:h-9 sm:flex-row sm:gap-1.5", mode === "reject" && "bg-negative/25")}
          disabled={disabled}
          onClick={props.onOpenReject}
          aria-keyshortcuts="X"
          aria-expanded={mode === "reject"}
          data-testid="decision-reject"
        >
          <X className="h-4 w-4" aria-hidden="true" />
          <span>Отклонить</span>
          <Kbd className="hidden sm:inline">X</Kbd>
        </Button>
        <Button
          variant="ghost"
          className="h-11 flex-col gap-0 border border-border sm:h-9 sm:flex-row sm:gap-1.5"
          disabled={!canOperate || pending}
          onClick={props.onUndo}
          aria-keyshortcuts="U"
          data-testid="decision-undo"
        >
          <Undo2 className="h-4 w-4" aria-hidden="true" />
          <span>Отменить</span>
          <Kbd className="hidden sm:inline">U</Kbd>
        </Button>
      </div>

      <Input
        value={comment}
        onChange={(event) => props.onCommentChange(event.target.value)}
        placeholder="Комментарий к решению (необязательно)"
        maxLength={2000}
        disabled={!canOperate}
        aria-label="Комментарий к решению"
        className="h-9 sm:h-8"
      />
      {!canOperate ? (
        <p className="text-xs text-fg-muted">Роль «Наблюдатель»: только просмотр, решения недоступны.</p>
      ) : null}
    </div>
  );
}
