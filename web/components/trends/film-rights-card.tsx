"use client";

import { RefreshCw } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button, FormField, SectionCard, Select, StatusBadge } from "@/components/ui";
import { errorMessage } from "@/lib/api/errors";
import { cn } from "@/lib/cn";
import { formatDateTime, formatNumber } from "@/lib/format";
import { type TrendsFilmRights, useTrendsFilmActions } from "@/lib/queries/trends";

import { HOLDER_POLICY_RU, MANUAL_RIGHTS_OPTIONS, RIGHTS_LEVEL_META, rightsLevel, riskTone } from "./labels";

/** Шкала риска 0–100 с засечками порогов 40 (с оговорками) и 65 (не проходит). */
export function RiskBar({ risk, className }: { risk: number | null | undefined; className?: string }) {
  const value = risk === null || risk === undefined ? 0 : Math.max(0, Math.min(100, risk));
  return (
    <span
      className={cn("relative block h-2 w-full overflow-hidden rounded-full bg-surface-2", className)}
      role="meter"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={value}
      aria-label="Риск по правам"
      title="риск 0–100: от 40 с оговорками, от 65 не проходит"
    >
      <span className={cn("absolute inset-y-0 left-0 rounded-full", riskTone(risk))} style={{ width: `${value}%` }} />
      {[40, 65].map((mark) => (
        <span
          key={mark}
          aria-hidden="true"
          className="absolute inset-y-0 w-px bg-fg-muted/60"
          style={{ left: `${mark}%` }}
        />
      ))}
    </span>
  );
}

function pickString(obj: Record<string, unknown> | null | undefined, key: string): string | null {
  const value = obj?.[key];
  return typeof value === "string" && value.trim() ? value : null;
}

function pickNumber(obj: Record<string, unknown> | null | undefined, key: string): number | null {
  const value = obj?.[key];
  return typeof value === "number" ? value : null;
}

/**
 * «Права»: уровень, риск со шкалой, причины, правило длины клипа, правообладатель и его политика, улики
 * (чужие нарезки, сносы, мнение модели). Оператору — «перепроверить» (модель + страницы чужих нарезок +
 * исходники, до минуты) и решение руками, которое сильнее автоматической оценки.
 */
export function FilmRightsCard({
  titleId,
  rights,
  operator,
}: {
  titleId: string;
  rights: TrendsFilmRights | null | undefined;
  operator: boolean;
}) {
  const actions = useTrendsFilmActions(titleId);
  const [saving, setSaving] = useState(false);
  const level = rightsLevel(rights?.level);
  const signals = (rights?.signals ?? null) as Record<string, unknown> | null;
  const manual = (pickString(signals, "manual_level") ?? "") as "" | "ok" | "caution" | "block";
  const manualNote = pickString(signals, "manual_note");
  const llm = (signals?.llm ?? null) as Record<string, unknown> | null;
  const claimants = Array.isArray(signals?.claimants) ? (signals?.claimants as unknown[]) : [];
  const rated = pickNumber(signals, "rated");
  const removed = pickNumber(signals, "removed_strict");
  const reasons = rights?.reasons ?? [];
  const notes = rights?.notes ?? [];

  async function onRefresh() {
    try {
      const updated = await actions.refreshRights.mutateAsync();
      toast.success("Права перепроверены", {
        description: ((updated.notes ?? []).join(" · ") || updated.level_ru) ?? undefined,
      });
    } catch (error) {
      toast.error("Перепроверка не удалась", { description: errorMessage(error) });
    }
  }

  async function onManual(next: string) {
    setSaving(true);
    try {
      await actions.setRights.mutateAsync({ level: next === "" ? null : (next as "ok" | "caution" | "block") });
      toast.success("Решение сохранено", { description: next ? `права: ${next}` : "вернулась автоматическая оценка" });
    } catch (error) {
      toast.error("Решение не сохранено", { description: errorMessage(error) });
    } finally {
      setSaving(false);
    }
  }

  return (
    <SectionCard
      title="Права"
      bodyClassName="gap-3"
      actions={
        operator ? (
          <Button
            variant="ghost"
            size="xs"
            onClick={() => void onRefresh()}
            disabled={actions.refreshRights.isPending}
            title="Спросить модель о правообладателе, перечитать страницы чужих нарезок, проверить исходники"
            data-testid="trends-film-rights-refresh"
          >
            <RefreshCw
              className={cn("h-3 w-3", actions.refreshRights.isPending && "animate-spin")}
              aria-hidden="true"
            />
            {actions.refreshRights.isPending ? "Проверяю…" : "Перепроверить"}
          </Button>
        ) : undefined
      }
    >
      {!rights ? (
        <p className="text-sm text-fg-muted">Права ещё не проверялись.</p>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={level} map={RIGHTS_LEVEL_META} />
            {rights.passes === false ? <span className="text-xs text-negative">в календарь не ставится</span> : null}
            {manual ? (
              <span className="text-xs text-human">решение оператора{manualNote ? `: ${manualNote}` : ""}</span>
            ) : null}
          </div>
          <div className="flex items-center gap-3">
            <RiskBar risk={rights.risk} className="flex-1" />
            <span className="tabular shrink-0 font-mono text-sm text-fg">
              риск {rights.risk !== null && rights.risk !== undefined ? formatNumber(rights.risk) : "—"}
            </span>
          </div>
          {reasons.length ? (
            <ul className="flex flex-col gap-1 text-sm text-fg">
              {reasons.map((reason) => (
                <li key={reason} className="flex gap-2">
                  <span aria-hidden="true" className="mt-2 h-1 w-1 shrink-0 rounded-full bg-fg-muted" />
                  <span>{reason}</span>
                </li>
              ))}
            </ul>
          ) : null}
          {rights.rule ? <p className="text-xs font-medium text-warning">{rights.rule}</p> : null}
          <dl className="grid grid-cols-[minmax(0,9rem)_1fr] gap-x-3 gap-y-1.5 text-sm">
            {rights.holder ? (
              <div className="contents">
                <dt className="text-fg-muted">правообладатель</dt>
                <dd className="text-fg">
                  {rights.holder}
                  {rights.holder_policy ? (
                    <span className="text-fg-muted">
                      {" "}
                      · {rights.holder_policy_ru ?? HOLDER_POLICY_RU[rights.holder_policy] ?? rights.holder_policy}
                    </span>
                  ) : null}
                </dd>
              </div>
            ) : null}
            {rights.clip_max_s ? (
              <div className="contents">
                <dt className="text-fg-muted">клип</dt>
                <dd className="text-fg">до {rights.clip_max_s} с</dd>
              </div>
            ) : null}
            {rated !== null ? (
              <div className="contents">
                <dt className="text-fg-muted">чужие нарезки</dt>
                <dd className="text-fg">
                  перепроверено {formatNumber(rated)}
                  {removed !== null ? ` · снесено по правам ${formatNumber(removed)}` : ""}
                  {claimants.length
                    ? ` · жаловались: ${claimants.map((c) => (Array.isArray(c) ? c[0] : String(c))).join(", ")}`
                    : ""}
                </dd>
              </div>
            ) : null}
            {llm ? (
              <div className="contents">
                <dt className="text-fg-muted">мнение модели</dt>
                <dd className="text-fg">
                  {[
                    pickString(llm, "holder"),
                    HOLDER_POLICY_RU[pickString(llm, "holder_policy") ?? ""] ?? pickString(llm, "holder_policy"),
                    llm.public_domain ? "общественное достояние" : null,
                    llm.official_free_ru ? "официально выложен бесплатно" : null,
                    llm.music_risk ? "музыкальный риск" : null,
                    pickNumber(llm, "confidence") !== null
                      ? `уверенность ${Math.round((pickNumber(llm, "confidence") ?? 0) * 100)} %`
                      : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                  {pickString(llm, "note") ? <span className="text-fg-muted"> · {pickString(llm, "note")}</span> : null}
                </dd>
              </div>
            ) : null}
            {rights.checked_at ? (
              <div className="contents">
                <dt className="text-fg-muted">проверено</dt>
                <dd className="text-fg">{formatDateTime(rights.checked_at, { short: true })}</dd>
              </div>
            ) : null}
          </dl>
          {notes.length ? <p className="text-xs text-fg-muted">{notes.join(" · ")}</p> : null}
        </>
      )}
      {operator ? (
        <div className="border-t border-border pt-3">
          <FormField label="Решение оператора" htmlFor="trends-film-manual-rights" hint="Сильнее автоматической оценки">
            <Select
              id="trends-film-manual-rights"
              value={manual}
              onChange={(event) => void onManual(event.target.value)}
              disabled={saving}
              className="w-44"
              data-testid="trends-film-rights-level"
            >
              {MANUAL_RIGHTS_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          </FormField>
        </div>
      ) : null}
    </SectionCard>
  );
}
