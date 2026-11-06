"use client";

import { parseAsStringLiteral, useQueryState } from "nuqs";

import { PageHeader } from "@/components/common/page-header";
import { tabPanelId, Tabs } from "@/components/ui";
import { useNow } from "@/lib/hooks/use-now";
import { useAnalyticsFreshness } from "@/lib/queries/analytics";

import { BreakdownTab } from "./breakdown-tab";
import { CohortsTab } from "./cohorts-tab";
import { CostsTab } from "./costs-tab";
import { AnalyticsFilterBar } from "./filter-bar";
import { FunnelTab } from "./funnel-tab";
import { FreshnessLine } from "./shared";
import { SummaryTab } from "./summary-tab";
import { useAnalyticsFilters } from "./use-analytics-filters";
import { VideosTab } from "./videos-tab";

const TABS = ["summary", "videos", "breakdown", "funnel", "cohorts", "costs"] as const;
type TabId = (typeof TABS)[number];
const TAB_LABELS: Record<TabId, string> = {
  summary: "Итоги",
  videos: "Ролики",
  breakdown: "Разрезы",
  funnel: "Воронка",
  cohorts: "Когорты",
  costs: "Расходы",
};
const ID_PREFIX = "analytics";

/**
 * Аналитика (§7.3 `/analytics?tab=&date_from=&date_to=&platform=&channel_id=&niche_id=&product_id=&basis=&mode=`):
 * строка фильтров в URL, шесть вкладок, CSV текущего отчёта, плашка свежести данных.
 */
export function AnalyticsScreen() {
  const [tab, setTab] = useQueryState(
    "tab",
    parseAsStringLiteral(TABS).withDefault("summary").withOptions({ history: "replace" }),
  );
  // Параметры вкладок делят имена (sort, offset, q): при смене вкладки их сбрасываем.
  const [, setSort] = useQueryState("sort", { history: "replace" });
  const [, setOffset] = useQueryState("offset", { history: "replace" });
  const [, setQ] = useQueryState("q", { history: "replace" });
  const state = useAnalyticsFilters();
  const freshness = useAnalyticsFreshness();
  const now = useNow(30_000);

  const changeTab = (value: string) => {
    void setTab(value as TabId);
    void setSort(null);
    void setOffset(null);
    void setQ(null);
  };

  return (
    <div className="flex flex-col gap-4 p-4 md:p-6" data-testid="analytics-screen">
      <PageHeader
        eyebrow="Анализ"
        title="Аналитика"
        description="Итоги против прошлого периода, лидерборд роликов, разрезы по медиане, воронка двух веток, когорты и честная цена опубликованного ролика. Все фильтры живут в адресе — ссылку можно переслать."
      />
      <AnalyticsFilterBar state={state} />
      <Tabs
        idPrefix={ID_PREFIX}
        label="Разделы аналитики"
        value={tab}
        onChange={changeTab}
        items={TABS.map((value) => ({ value, label: TAB_LABELS[value] }))}
      />
      <section
        id={tabPanelId(ID_PREFIX, tab)}
        role="tabpanel"
        aria-labelledby={`${ID_PREFIX}-tab-${tab}`}
        className="flex flex-col gap-4"
      >
        {tab === "summary" ? (
          <SummaryTab filters={state.filters} />
        ) : tab === "videos" ? (
          <VideosTab filters={state.filters} />
        ) : tab === "breakdown" ? (
          <BreakdownTab filters={state.filters} />
        ) : tab === "funnel" ? (
          <FunnelTab filters={state.filters} />
        ) : tab === "cohorts" ? (
          <CohortsTab filters={state.filters} />
        ) : (
          <CostsTab filters={state.filters} />
        )}
      </section>
      <FreshnessLine freshness={freshness.data} now={now} />
    </div>
  );
}
