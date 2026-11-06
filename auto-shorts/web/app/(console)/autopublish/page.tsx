import type { Metadata } from "next";

import { AutopublishScreen } from "@/components/autopublish/autopublish-screen";
import { PageSuspense } from "@/components/common/page-suspense";

export const metadata: Metadata = { title: "Запланировано" };

/** Очередь автозалива (0019): `?state=all|active|failed|done|cancelled`, `?channel=`. Состояние в URL → Suspense. */
export default function AutopublishPage() {
  return (
    <PageSuspense>
      <AutopublishScreen />
    </PageSuspense>
  );
}
