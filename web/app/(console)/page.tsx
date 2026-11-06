import type { Metadata } from "next";

import { PageSuspense } from "@/components/common/page-suspense";
import { OverviewScreen } from "@/components/overview/overview-screen";

export const metadata: Metadata = { title: "Обзор" };

export default function OverviewPage() {
  return (
    <PageSuspense>
      <OverviewScreen />
    </PageSuspense>
  );
}
