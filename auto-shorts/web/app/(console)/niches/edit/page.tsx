import type { Metadata } from "next";

import { PageSuspense } from "@/components/common/page-suspense";
import { NicheEditorScreen } from "@/components/niches/niche-editor-screen";

export const metadata: Metadata = { title: "Редактор ниши" };

export default function NicheEditPage() {
  return (
    <PageSuspense>
      <NicheEditorScreen />
    </PageSuspense>
  );
}
