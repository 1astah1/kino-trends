import type { Metadata } from "next";

import { PageSuspense } from "@/components/common/page-suspense";
import { TrendsScreen } from "@/components/trends/trends-screen";

export const metadata: Metadata = { title: "Трендоскаут" };

export default function TrendsPage() {
  return (
    <PageSuspense>
      <TrendsScreen />
    </PageSuspense>
  );
}
