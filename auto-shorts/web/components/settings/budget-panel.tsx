"use client";

import { CirclePause, CirclePlay, Wallet } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { useCurrentUser } from "@/components/auth/auth-context";
import { SectionError } from "@/components/common/section-error";
import {
  Badge,
  Button,
  Callout,
  FormField,
  Input,
  SectionCard,
  SkeletonStatGrid,
  StatTile,
  StatTileGrid,
} from "@/components/ui";
import { errorMessage } from "@/lib/api/errors";
import { isAdmin } from "@/lib/auth";
import { cn } from "@/lib/cn";
import { formatDay, formatPct, formatUsd } from "@/lib/format";
import { budgetUsage, useBudget } from "@/lib/queries/budget";
import {
  usePauseBudget,
  useResumeBudget,
  useSettingsList,
  useUpdateBudget,
  useUpdateSettings,
} from "@/lib/queries/settings";

/** Настройки → Бюджет (§7.3): дневной лимит, порог предупреждения, ручная пауза платных шагов, курс USD→RUB. */
export function BudgetPanel() {
  const user = useCurrentUser();
  const admin = isAdmin(user.role);
  const budget = useBudget();
  const b = budget.data;
  const usage = budgetUsage(b);

  return (
    <div className="flex flex-col gap-4" data-testid="budget-panel">
      <div>
        <h2 className="text-base font-semibold">Бюджет дня</h2>
        <p className="text-xs text-fg-muted">
          Платные шаги (LLM, озвучка, картинки) резервируют деньги до вызова. При исчерпании лимита или ручной паузе
          тяжёлые задачи откладываются с причиной «Бюджет исчерпан».
        </p>
      </div>
      {budget.isError ? (
        <SectionError error={budget.error} onRetry={() => void budget.refetch()} />
      ) : !b ? (
        <SkeletonStatGrid count={4} />
      ) : (
        <>
          {b.paused ? (
            <Callout variant="warning" title="Платные шаги на паузе">
              {b.pause_reason || "Пауза без причины"}
            </Callout>
          ) : null}
          <StatTileGrid>
            <StatTile label={`Потрачено · ${formatDay(b.day)}`} value={formatUsd(b.spent_today_usd, 4)} icon={Wallet} />
            <StatTile label="Зарезервировано" value={formatUsd(b.reserved_usd, 4)} />
            <StatTile label="Лимит дня" value={formatUsd(b.daily_limit_usd)} />
            <StatTile
              label="Использовано"
              value={formatPct(usage.pct, 1)}
              badge={
                usage.level === "ok" ? null : (
                  <Badge variant={usage.level === "warning" ? "warning" : "negative"}>
                    {usage.level === "warning" ? "порог" : usage.level === "paused" ? "пауза" : "лимит"}
                  </Badge>
                )
              }
            />
          </StatTileGrid>
          <div className="relative h-2 overflow-hidden rounded-full bg-surface-2" aria-hidden="true">
            <span
              className={cn(
                "absolute inset-y-0 left-0 rounded-full",
                usage.level === "ok" ? "bg-positive" : usage.level === "warning" ? "bg-warning" : "bg-negative",
              )}
              style={{ width: `${Math.min(100, usage.pct)}%` }}
            />
            <span
              className="absolute inset-y-0 w-px bg-fg/70"
              style={{ left: `${Math.min(100, b.alert_threshold_pct)}%` }}
            />
          </div>
          <div className="grid gap-4 lg:grid-cols-3">
            <LimitsCard
              key={`${b.daily_limit_usd}-${b.alert_threshold_pct}`}
              limit={b.daily_limit_usd}
              threshold={b.alert_threshold_pct}
              admin={admin}
            />
            <PauseCard paused={b.paused} admin={admin} />
            <FxCard admin={admin} />
          </div>
        </>
      )}
    </div>
  );
}

function LimitsCard({ limit, threshold, admin }: { limit: number; threshold: number; admin: boolean }) {
  const update = useUpdateBudget();
  const [limitText, setLimitText] = useState(String(limit));
  const [thresholdText, setThresholdText] = useState(String(threshold));
  const limitValue = Number(limitText.replace(",", "."));
  const thresholdValue = Number(thresholdText);
  const limitError = !Number.isFinite(limitValue) || limitValue < 0 ? "Число ≥ 0" : null;
  const thresholdError =
    !Number.isInteger(thresholdValue) || thresholdValue < 1 || thresholdValue > 100 ? "Целое 1–100" : null;
  const dirty = limitValue !== limit || thresholdValue !== threshold;

  const save = async () => {
    try {
      await update.mutateAsync({ daily_limit_usd: limitValue, alert_threshold_pct: thresholdValue });
      toast.success("Лимиты бюджета сохранены");
    } catch (error) {
      toast.error("Не сохранено", { description: errorMessage(error) });
    }
  };

  return (
    <SectionCard title="Лимиты">
      <FormField label="Лимит в день, $" htmlFor="budget-limit" error={limitError ?? undefined}>
        <Input
          id="budget-limit"
          inputMode="decimal"
          className="font-mono"
          value={limitText}
          disabled={!admin}
          aria-invalid={!!limitError}
          onChange={(e) => setLimitText(e.target.value)}
        />
      </FormField>
      <FormField
        label="Предупреждать с, %"
        htmlFor="budget-threshold"
        error={thresholdError ?? undefined}
        hint="Шапка станет янтарной, придёт алерт"
      >
        <Input
          id="budget-threshold"
          inputMode="numeric"
          className="font-mono"
          value={thresholdText}
          disabled={!admin}
          aria-invalid={!!thresholdError}
          onChange={(e) => setThresholdText(e.target.value)}
        />
      </FormField>
      {admin ? (
        <Button
          size="sm"
          className="self-start"
          disabled={!dirty || !!limitError || !!thresholdError || update.isPending}
          onClick={() => void save()}
        >
          Сохранить
        </Button>
      ) : (
        <p className="text-2xs text-fg-muted">Меняет владелец.</p>
      )}
    </SectionCard>
  );
}

function PauseCard({ paused, admin }: { paused: boolean; admin: boolean }) {
  const pause = usePauseBudget();
  const resume = useResumeBudget();
  const [reason, setReason] = useState("");
  return (
    <SectionCard title="Пауза платных шагов">
      {paused ? (
        <>
          <p className="text-sm text-fg-muted">Новые платные вызовы не стартуют; бесплатные шаги и рендер работают.</p>
          {admin ? (
            <Button
              size="sm"
              className="self-start"
              disabled={resume.isPending}
              onClick={() => resume.mutate(undefined, { onSuccess: () => toast.success("Пауза снята") })}
            >
              <CirclePlay className="h-3.5 w-3.5" aria-hidden="true" />
              Снять паузу
            </Button>
          ) : null}
        </>
      ) : admin ? (
        <>
          <FormField label="Причина" htmlFor="budget-pause-reason" hint="Увидят все в шапке">
            <Input
              id="budget-pause-reason"
              value={reason}
              maxLength={200}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Например: проверяю счёт fal"
            />
          </FormField>
          <Button
            size="sm"
            variant="destructive"
            className="self-start"
            disabled={!reason.trim() || pause.isPending}
            onClick={() =>
              pause.mutate(reason.trim(), {
                onSuccess: () => {
                  setReason("");
                  toast.success("Платные шаги на паузе");
                },
              })
            }
          >
            <CirclePause className="h-3.5 w-3.5" aria-hidden="true" />
            Поставить на паузу
          </Button>
        </>
      ) : (
        <p className="text-sm text-fg-muted">Платные шаги работают. Паузу ставит владелец.</p>
      )}
    </SectionCard>
  );
}

function FxCard({ admin }: { admin: boolean }) {
  const user = useCurrentUser();
  const settings = useSettingsList({ enabled: user.role !== "viewer" });
  const update = useUpdateSettings();
  const item = settings.data?.find((s) => s.key === "fx_usd_rub");
  const current = typeof item?.value === "number" ? item.value : null;
  const [text, setText] = useState<string | null>(null);
  const value = text ?? (current === null ? "" : String(current));
  const parsed = Number(value.replace(",", "."));
  const invalid = !(parsed > 0);

  const save = async () => {
    try {
      await update.mutateAsync({ fx_usd_rub: parsed });
      setText(null);
      toast.success("Курс сохранён");
    } catch (error) {
      toast.error("Курс не сохранён", { description: errorMessage(error) });
    }
  };

  return (
    <SectionCard title="Курс для отчётов">
      {user.role === "viewer" ? (
        <p className="text-sm text-fg-muted">Настройки видят оператор и владелец.</p>
      ) : settings.isError ? (
        <SectionError error={settings.error} onRetry={() => void settings.refetch()} />
      ) : (
        <>
          <FormField
            label="USD → RUB"
            htmlFor="fx-usd-rub"
            hint={item ? `По умолчанию ${String(item.default)}` : undefined}
            error={text !== null && invalid ? "Число > 0" : undefined}
          >
            <Input
              id="fx-usd-rub"
              inputMode="decimal"
              className="font-mono"
              value={value}
              disabled={!admin || !item}
              onChange={(e) => setText(e.target.value)}
            />
          </FormField>
          {admin ? (
            <Button
              size="sm"
              className="self-start"
              disabled={text === null || invalid || update.isPending}
              onClick={() => void save()}
            >
              Сохранить
            </Button>
          ) : null}
        </>
      )}
    </SectionCard>
  );
}
