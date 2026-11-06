"use client";

import { Hourglass, Lock } from "lucide-react";
import { parseAsStringLiteral, useQueryState } from "nuqs";

import { useCurrentUser } from "@/components/auth/auth-context";
import { PageHeader } from "@/components/common/page-header";
import { Badge, EmptyState, tabPanelId, Tabs } from "@/components/ui";
import { isAdmin } from "@/lib/auth";
import { DEFAULT_SETTINGS_TAB, MILESTONE_HINT, SETTINGS_TABS, type SettingsTabId } from "@/lib/nav";

import { AttributionPanel } from "./attribution-panel";
import { AuditPanel } from "./audit-panel";
import { BudgetPanel } from "./budget-panel";
import { NotificationsPanel } from "./notifications-panel";
import { ProvidersPanel } from "./providers-panel";
import { SystemPanel } from "./system-panel";
import { UsersPanel } from "./users-panel";
import { WorkerPanel } from "./worker-panel";

const TAB_IDS = SETTINGS_TABS.map((tab) => tab.id) as SettingsTabId[];
const ID_PREFIX = "settings";
/** Вкладки, у которых уже есть рабочий экран (M1: пользователи и система; M2: провайдеры и бюджет). */
const IMPLEMENTED = new Set<SettingsTabId>([
  "users",
  "system",
  "providers",
  "budget",
  "worker",
  "attribution",
  "notifications",
  "audit",
]);

/** Настройки (§7.3, `?tab=`). Работают «Провайдеры», «Бюджет», «Пользователи» и «Система»; остальные — заглушки. */
export function SettingsScreen() {
  const user = useCurrentUser();
  const [tab, setTab] = useQueryState(
    "tab",
    parseAsStringLiteral(TAB_IDS).withDefault(DEFAULT_SETTINGS_TAB).withOptions({ history: "replace" }),
  );
  const current = SETTINGS_TABS.find((item) => item.id === tab) ?? SETTINGS_TABS[SETTINGS_TABS.length - 1];

  return (
    <div className="flex flex-col gap-4 p-4 md:p-6">
      <PageHeader
        title="Настройки"
        description="Провайдеры, бюджет, воркер, атрибуция, уведомления, пользователи, аудит и состояние системы."
      />
      <Tabs
        idPrefix={ID_PREFIX}
        label="Разделы настроек"
        value={current.id}
        onChange={(value) => void setTab(value as SettingsTabId)}
        items={SETTINGS_TABS.map((item) => ({
          value: item.id,
          label: item.label,
          badge:
            item.status === "soon" && !IMPLEMENTED.has(item.id) ? (
              <span className="font-mono text-2xs text-fg-muted/70 uppercase" aria-label="скоро">
                {item.milestone}
              </span>
            ) : undefined,
        }))}
      />
      <section
        id={tabPanelId(ID_PREFIX, current.id)}
        role="tabpanel"
        aria-labelledby={`${ID_PREFIX}-tab-${current.id}`}
      >
        {current.adminOnly && !isAdmin(user.role) ? (
          <EmptyState
            icon={Lock}
            title="Раздел доступен владельцу"
            description="Пользователи, секреты и провайдеры меняет только роль «Владелец»."
          />
        ) : current.id === "users" ? (
          <UsersPanel />
        ) : current.id === "providers" ? (
          <ProvidersPanel />
        ) : current.id === "budget" ? (
          <BudgetPanel />
        ) : current.id === "system" ? (
          <SystemPanel />
        ) : current.id === "worker" ? (
          <WorkerPanel />
        ) : current.id === "attribution" ? (
          <AttributionPanel />
        ) : current.id === "notifications" ? (
          <NotificationsPanel />
        ) : current.id === "audit" ? (
          <AuditPanel />
        ) : (
          <EmptyState
            icon={Hourglass}
            title={`${current.label}: скоро`}
            description={
              <>
                {current.description}
                <br />
                <Badge variant="machine" outline className="mt-2">
                  {MILESTONE_HINT[current.milestone]}
                </Badge>
              </>
            }
          />
        )}
      </section>
    </div>
  );
}
