"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Check } from "lucide-react";

import { useCurrentUser } from "@/components/auth/auth-context";
import { Button } from "@/components/ui";
import { canOperate } from "@/lib/auth";
import { cn } from "@/lib/cn";
import { formatDateTime } from "@/lib/format";
import { type Alert, useAckAlert } from "@/lib/queries/alerts";
import { overviewKeys } from "@/lib/queries/overview";
import { ALERT_SEVERITY_META } from "@/lib/status-meta";

/** Открытые алерты из сводки (не больше 5, сначала критичные); «Принять» — оператору и владельцу. */
export function AlertsList({ alerts, open }: { alerts: Alert[]; open: number }) {
  const user = useCurrentUser();
  const queryClient = useQueryClient();
  const ack = useAckAlert();
  const operator = canOperate(user.role);

  if (alerts.length === 0) {
    return <p className="text-xs text-fg-muted">Всё спокойно: открытых алертов нет.</p>;
  }
  return (
    <ul className="-mx-1 flex flex-col gap-1" data-testid="overview-alerts">
      {alerts.map((alert) => {
        const meta = ALERT_SEVERITY_META[alert.severity];
        const SeverityIcon = meta.icon;
        return (
          <li key={alert.id} className="flex items-start gap-2 rounded-md px-1 py-1.5 hover:bg-surface-2">
            <span className={cn("mt-0.5 rounded-sm p-0.5", meta.bg)}>
              <SeverityIcon className={cn("h-3.5 w-3.5", meta.text)} aria-hidden="true" />
              <span className="sr-only">{meta.label}</span>
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm leading-5 text-fg">{alert.title}</p>
              <p className="font-mono text-2xs text-fg-muted">
                {alert.code} · {formatDateTime(alert.opened_at, { short: true })}
              </p>
            </div>
            {operator ? (
              <Button
                variant="ghost"
                size="xs"
                onClick={() =>
                  ack.mutate(alert.id, {
                    onSuccess: () => void queryClient.invalidateQueries({ queryKey: overviewKeys.all }),
                  })
                }
                disabled={ack.isPending && ack.variables === alert.id}
                aria-label={`Принять алерт: ${alert.title}`}
              >
                <Check className="h-3.5 w-3.5" aria-hidden="true" />
                Принять
              </Button>
            ) : null}
          </li>
        );
      })}
      {open > alerts.length ? (
        <li className="px-1 pt-1 text-2xs text-fg-muted">Ещё {open - alerts.length} — в колокольчике в шапке.</li>
      ) : null}
    </ul>
  );
}
