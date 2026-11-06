import type { Metadata } from "next";

import { SourcesScreen } from "@/components/clipping/sources-screen";
import { PageSuspense } from "@/components/common/page-suspense";

export const metadata: Metadata = { title: "Источники нарезки" };

/** Источники нарезки (§7.3): `?status=&q=&page=`. Состояние в URL → Suspense для статического экспорта. */
export default function ClippingPage() {
  return (
    <PageSuspense>
      <SourcesScreen />
    </PageSuspense>
  );
}
