import Link from "next/link";

import { cn } from "@/lib/cn";

/** Знак консоли: уголки title-safe + «кадр» 9:16. Без растровых файлов и внешних ресурсов. */
export function BrandMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={cn("h-5 w-5", className)} aria-hidden="true">
      <path d="M6 11V6h5M26 21v5h-5" fill="none" stroke="var(--accent)" strokeWidth="2.5" />
      <rect x="12" y="9" width="8" height="14" rx="1" fill="var(--human)" />
    </svg>
  );
}

export function Brand({ collapsed = false, bordered = true }: { collapsed?: boolean; bordered?: boolean }) {
  return (
    <Link
      href="/"
      className={cn("flex h-11 shrink-0 items-center gap-2 px-3", bordered && "border-b border-border")}
      aria-label="Auto Shorts — на главную"
    >
      <BrandMark />
      {collapsed ? null : (
        <span className="font-mono text-xs font-semibold tracking-[0.18em] text-fg uppercase">Auto Shorts</span>
      )}
    </Link>
  );
}
