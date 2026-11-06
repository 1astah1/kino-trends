import type { Metadata } from "next";

import { VideoCardScreen } from "@/components/analytics/video-card-screen";
import { PageSuspense } from "@/components/common/page-suspense";

export const metadata: Metadata = { title: "Карточка ролика" };

export default function VideoCardPage() {
  return (
    <PageSuspense>
      <VideoCardScreen />
    </PageSuspense>
  );
}
