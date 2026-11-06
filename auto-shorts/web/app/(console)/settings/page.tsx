import type { Metadata } from "next";

import { PageSuspense } from "@/components/common/page-suspense";
import { SettingsScreen } from "@/components/settings/settings-screen";

export const metadata: Metadata = { title: "Настройки" };

export default function SettingsPage() {
  return (
    <PageSuspense>
      <SettingsScreen />
    </PageSuspense>
  );
}
