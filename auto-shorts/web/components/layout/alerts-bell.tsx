"use client";

import { Bell, BellRing, Check } from "lucide-react";

import { Button } from "@/components/ui";
import { canOperate } from "@/lib/auth";
import { cn } from "@/lib/cn";
import { formatDateTime } from "@/lib/format";
import { useAckAlert, useOpenAlerts } from "@/lib/queries/alerts";
import { ALERT_SEVERITY_META } from "@/lib/status-meta";

import { useCurrentUser } from "../auth/auth-context";
import { HeaderPopover, headerButtonClass } from "./popover";

/** Открытые алерты (§7.3): счётчик, список, «Принять» для оператора и владельца. */
export function AlertsBell() {
  const user = useCurrentUser();
  const alerts = useOpenAlerts();
  const ack = useAckAlert();
  const items = alerts.data ?? [];
  const worst = items.reduce((max, item) => Math.max(max, ALERT_SEVERITY_META[item.severity]?.rank ?? 0), -1);
  const tone = worst >= 2 ? "bg-negative text-bg" : worst === 1 ? "bg-warning text-bg" : "bg-accent text-accent-fg";
  const Icon = items.length ? BellRing : Bell;

  return (
    <HeaderPopover
      label="Алерты"
      className="w-96"
      trigger={
        <button
          type="button"
          className={cn(headerButtonClass, "relative")}
          aria-label={items.length ? `Алерты: ${items.length} открыто` : "Алерты: нет открытых"}
          data-testid="alerts-bell"
        >
          <Icon className={cn("h-4 w-4", worst >= 2 && "text-negative")} aria-hidden="true" />
          {items.length ? (
            <span className={cn("tabular rounded-sm px-1 font-mono text-2xs leading-4 font-semibold", tone)}>
              {items.length > 99 ? "99+" : items.length}
            </span>
          ) : null}
        </button>
      }
    >
      <div className="flex flex-col gap-2">
        <p className="text-sm font-medium">Открытые алерты</p>
        {alerts.isError ? <p className="text-xs text-fg-muted">Нет ответа /alerts</p> : null}
        {!alerts.isError && items.length === 0 ? (
          <p className="text-xs text-fg-muted">
            {alerts.isPending ? "Загрузка…" : "Всё спокойно: открытых алертов нет."}
          </p>
        ) : null}
        <ul className="-mx-1 flex max-h-80 flex-col gap-1 overflow-y-auto">
          {items.map((alert) => {
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
                {canOperate(user.role) ? (
                  <Button
                    variant="ghost"
                    size="xs"
                    onClick={() => ack.mutate(alert.id)}
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
        </ul>
      </div>
    </HeaderPopover>
  );
}
