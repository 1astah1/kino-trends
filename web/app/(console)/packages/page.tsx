import type { Metadata } from "next";

import { PageSuspense } from "@/components/common/page-suspense";
import { PackagesScreen } from "@/components/packages/packages-screen";

export const metadata: Metadata = { title: "Пакеты к заливке" };

/** Пакеты (§7.3): `?status=live|published|problems|links`, `?channel=`, `?package=`. Состояние в URL → Suspense. */
export default function PackagesPage() {
  return (
    <PageSuspense>
      <PackagesScreen />
    </PageSuspense>
  );
}
