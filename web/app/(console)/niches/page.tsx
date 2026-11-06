import type { Metadata } from "next";

import { PageSuspense } from "@/components/common/page-suspense";
import { NichesScreen } from "@/components/niches/niches-screen";

export const metadata: Metadata = { title: "Ниши" };

export default function NichesPage() {
  return (
    <PageSuspense>
      <NichesScreen />
    </PageSuspense>
  );
}
