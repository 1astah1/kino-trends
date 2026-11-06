import type { Metadata } from "next";

import { BannerEditorScreen } from "@/components/banner-editor/banner-editor-screen";
import { PageSuspense } from "@/components/common/page-suspense";

export const metadata: Metadata = { title: "Редактор баннера" };

export default function BannerEditorPage() {
  return (
    <PageSuspense>
      <BannerEditorScreen />
    </PageSuspense>
  );
}
