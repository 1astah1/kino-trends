import type { Metadata } from "next";

import { AnalyticsScreen } from "@/components/analytics/analytics-screen";
import { PageSuspense } from "@/components/common/page-suspense";

export const metadata: Metadata = { title: "Аналитика" };

export default function AnalyticsPage() {
  return (
    <PageSuspense>
      <AnalyticsScreen />
    </PageSuspense>
  );
}
