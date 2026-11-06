import { Skeleton } from "@/components/ui";

/** Скелетон оболочки на время проверки сессии: те же размеры, что у AppShell (без прыжков). */
export function ShellSkeleton() {
  return (
    <div className="flex h-dvh" aria-busy="true" aria-label="Проверка сессии">
      <div className="hidden w-[216px] shrink-0 flex-col gap-2 border-r border-border bg-surface p-3 md:flex">
        <Skeleton className="h-5 w-28" />
        {Array.from({ length: 9 }).map((_, i) => (
          <Skeleton key={i} className="h-6 w-full" />
        ))}
      </div>
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex h-11 items-center gap-3 border-b border-border px-4">
          <Skeleton className="h-4 w-40" />
          <div className="flex-1" />
          <Skeleton className="h-5 w-32" />
          <Skeleton className="h-5 w-24" />
        </div>
        <div className="flex flex-col gap-3 p-6">
          <Skeleton className="h-6 w-56" />
          <Skeleton className="h-40 w-full" />
        </div>
      </div>
    </div>
  );
}
