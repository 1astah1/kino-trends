import type { Metadata } from "next";

import { ClipStudio } from "@/components/clipping/clip-studio";
import { PageSuspense } from "@/components/common/page-suspense";

export const metadata: Metadata = { title: "Студия нарезки" };

/** Студия нарезки (§7.3): `?id=&clip=&t=`. Только десктоп; wavesurfer и tus грузятся только здесь. */
export default function ClipStudioPage() {
  return (
    <PageSuspense>
      <ClipStudio />
    </PageSuspense>
  );
}
