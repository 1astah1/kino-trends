import type { Metadata } from "next";

import { PageSuspense } from "@/components/common/page-suspense";
import { VideoDetailScreen } from "@/components/trends/video-detail-screen";

export const metadata: Metadata = { title: "Разбор ролика" };

export default function TrendsVideoPage() {
  return (
    <PageSuspense>
      <VideoDetailScreen />
    </PageSuspense>
  );
}
