import type { Metadata } from "next";

import { ChannelsScreen } from "@/components/channels/channels-screen";
import { PageSuspense } from "@/components/common/page-suspense";

export const metadata: Metadata = { title: "Каналы" };

export default function ChannelsPage() {
  return (
    <PageSuspense>
      <ChannelsScreen />
    </PageSuspense>
  );
}
