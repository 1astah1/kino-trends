import { type ReactNode, Suspense } from "react";

import { Skeleton, SkeletonTable } from "@/components/ui";

/** Каждая страница с `useSearchParams` обёрнута в Suspense со скелетоном (§7.1, статический экспорт). */
export function PageSuspense({ children, fallback }: { children: ReactNode; fallback?: ReactNode }) {
  return <Suspense fallback={fallback ?? <PageSkeleton />}>{children}</Suspense>;
}

export function PageSkeleton() {
  return (
    <div className="flex flex-col gap-4 p-4 md:p-6" aria-busy="true" aria-label="Загрузка">
      <Skeleton className="h-6 w-48" />
      <Skeleton className="h-8 w-full max-w-xl" />
      <SkeletonTable rows={6} />
    </div>
  );
}
