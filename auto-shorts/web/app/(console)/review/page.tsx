import type { Metadata } from "next";

import { PageSuspense } from "@/components/common/page-suspense";
import { ReviewScreen } from "@/components/review/review-screen";

export const metadata: Metadata = { title: "Приёмка" };

/** Приёмка (§7.3): `?job=`, `?platform=youtube|vk`. Состояние в URL → Suspense для статического экспорта. */
export default function ReviewPage() {
  return (
    <PageSuspense>
      <ReviewScreen />
    </PageSuspense>
  );
}
