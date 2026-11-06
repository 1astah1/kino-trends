import type { Metadata } from "next";

import { PageSuspense } from "@/components/common/page-suspense";
import { FilmDetailScreen } from "@/components/trends/film-detail-screen";

export const metadata: Metadata = { title: "Разбор произведения" };

export default function TrendsFilmPage() {
  return (
    <PageSuspense>
      <FilmDetailScreen />
    </PageSuspense>
  );
}
