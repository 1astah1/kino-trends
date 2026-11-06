import { formatNumber, formatPct, formatUsd } from "@/lib/format";
import type { FunnelBranches, FunnelStep } from "@/lib/queries/overview";

import { FUNNEL_STEP_LABELS } from "./metric-meta";

function rateText(step: FunnelStep, index: number): string | null {
  if (index === 0 || step.rate_from_prev === null) return null;
  const pct = step.rate_from_prev * 100;
  return formatPct(pct, pct < 10 ? 1 : 0);
}

/**
 * Одна ветка воронки. Полоса — доля от предыдущего шага (у первого — 100%), поэтому слабый
 * переход виден сразу; абсолютное значение, ‰ от просмотров и цена шага — текстом.
 * Последовательная рампа одного оттенка: прозрачность слота 1 убывает по шагам.
 */
function FunnelBranch({ title, steps }: { title: string; steps: FunnelStep[] }) {
  const last = Math.max(1, steps.length - 1);
  return (
    <div className="flex flex-col gap-2">
      <h3 className="font-mono text-2xs tracking-wider text-fg-muted uppercase">{title}</h3>
      <ol className="flex flex-col gap-2">
        {steps.map((step, index) => {
          const rate = index === 0 ? 1 : step.rate_from_prev;
          const width = rate === null ? 0 : Math.min(100, Math.max(rate > 0 ? 1.5 : 0, rate * 100));
          const opacity = 1 - (index / last) * 0.6;
          const pct = rateText(step, index);
          return (
            <li key={step.code} className="flex flex-col gap-1">
              <div className="flex items-baseline justify-between gap-2 text-xs">
                <span className="truncate text-fg">{FUNNEL_STEP_LABELS[step.code] ?? step.code}</span>
                <span className="flex shrink-0 items-baseline gap-2">
                  {index > 0 && step.per_1k_views !== null ? (
                    <span className="tabular font-mono text-2xs text-fg-muted">
                      {formatNumber(step.per_1k_views, step.per_1k_views < 10 ? 1 : 0)}‰
                    </span>
                  ) : null}
                  {pct ? <span className="tabular font-mono text-2xs text-fg-muted">{pct}</span> : null}
                  {index > 0 && step.cost_per_usd !== null && step.cost_per_usd > 0 ? (
                    <span className="tabular font-mono text-2xs text-fg-muted">{formatUsd(step.cost_per_usd, 2)}</span>
                  ) : null}
                  <span className="tabular font-mono font-semibold text-fg">{formatNumber(step.value)}</span>
                </span>
              </div>
              <div className="h-1.5 overflow-hidden rounded-full bg-surface-2" aria-hidden="true">
                <span
                  className="block h-full rounded-full"
                  style={{ width: `${width}%`, background: "var(--color-chart-1)", opacity }}
                />
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

/** Мини-воронка двух веток: бот (Telegram) и домен (лендинг → сайт). */
export function FunnelBars({ funnel }: { funnel: FunnelBranches }) {
  return (
    <div className="flex flex-col gap-3" data-testid="overview-funnel">
      <div className="grid gap-4 md:grid-cols-2">
        <FunnelBranch title="Ветка бота" steps={funnel.bot} />
        <FunnelBranch title="Ветка домена" steps={funnel.domain} />
      </div>
      <p className="text-2xs text-fg-muted">
        Полоса и % — доля от предыдущего шага; ‰ — на 1000 просмотров; $ — расход периода на один шаг.
      </p>
    </div>
  );
}
