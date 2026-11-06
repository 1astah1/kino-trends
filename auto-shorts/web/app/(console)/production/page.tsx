import type { Metadata } from "next";

import { PageSuspense } from "@/components/common/page-suspense";
import { ProductionScreen } from "@/components/production/production-screen";

export const metadata: Metadata = { title: "Конвейер" };

/** Конвейер (§7.3): `?stage=&status=&kind=&channel=&q=&job=&tab=&page=&sort=`. Состояние в URL → Suspense. */
export default function ProductionPage() {
  return (
    <PageSuspense>
      <ProductionScreen />
    </PageSuspense>
  );
}
