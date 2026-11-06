import { Circle, CircleCheck } from "lucide-react";
import Link from "next/link";

import { SectionCard } from "@/components/ui";
import type { OverviewOnboarding } from "@/lib/queries/overview";

import { ONBOARDING_STEPS } from "./metric-meta";

/** Чек-лист первого запуска: показывается, пока сервер не вернул `onboarding.done`. */
export function OnboardingChecklist({ onboarding }: { onboarding: OverviewOnboarding }) {
  const steps = ONBOARDING_STEPS.map((step) => ({ ...step, ok: step.done(onboarding) }));
  const doneCount = steps.filter((step) => step.ok).length;
  return (
    <SectionCard
      title="Первый запуск"
      actions={
        <span className="tabular font-mono text-2xs text-fg-muted">
          {doneCount} из {steps.length}
        </span>
      }
    >
      <div data-testid="overview-onboarding" className="flex flex-col gap-3">
        <p className="text-xs text-fg-muted">
          База ещё пустая, поэтому ниже в основном нули — это нормально. Закройте шаги по порядку, и Обзор
          начнёт наполняться.
        </p>
        <ol className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {steps.map((step) => (
            <li key={step.key} className="flex items-start gap-2 text-sm">
              {step.ok ? (
                <CircleCheck className="mt-0.5 h-4 w-4 shrink-0 text-positive" aria-hidden="true" />
              ) : (
                <Circle className="mt-0.5 h-4 w-4 shrink-0 text-fg-muted" aria-hidden="true" />
              )}
              <span className="flex min-w-0 flex-col">
                {step.ok ? (
                  <span className="text-fg">
                    {step.label}
                    <span className="sr-only">: готово</span>
                  </span>
                ) : (
                  <Link href={step.href} className="text-accent hover:underline">
                    {step.label}
                  </Link>
                )}
                <span className="text-xs text-fg-muted">{step.hint(onboarding)}</span>
              </span>
            </li>
          ))}
        </ol>
      </div>
    </SectionCard>
  );
}
