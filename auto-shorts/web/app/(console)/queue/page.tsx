import type { Metadata } from "next";

import { PageSuspense } from "@/components/common/page-suspense";
import { QueueScreen } from "@/components/queue/queue-screen";

export const metadata: Metadata = { title: "Очередь" };

/** Очередь (`?tab=now|queue|failed|history|control`): фон auto-shorts и Kino-trends с управлением. Состояние в URL → Suspense. */
export default function QueuePage() {
  return (
    <PageSuspense>
      <QueueScreen />
    </PageSuspense>
  );
}
