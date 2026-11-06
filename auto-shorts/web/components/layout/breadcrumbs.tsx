"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { breadcrumbsFor } from "@/lib/nav";

export function Breadcrumbs() {
  const pathname = usePathname();
  const crumbs = breadcrumbsFor(pathname);
  if (crumbs.length === 0) return <span className="text-sm font-semibold text-fg">Auto Shorts</span>;
  return (
    <nav aria-label="Крошки" className="min-w-0">
      <ol className="flex min-w-0 items-center gap-1.5 text-sm">
        {crumbs.map((crumb, index) => {
          const last = index === crumbs.length - 1;
          return (
            <li key={`${crumb.label}-${index}`} className={last ? "min-w-0 truncate" : "hidden shrink-0 sm:block"}>
              {index > 0 ? (
                <span className="mr-1.5 hidden text-fg-muted/50 sm:inline" aria-hidden="true">
                  /
                </span>
              ) : null}
              {last ? (
                <span className="font-semibold text-fg" aria-current="page">
                  {crumb.label}
                </span>
              ) : crumb.href ? (
                <Link href={crumb.href} className="text-fg-muted hover:text-fg">
                  {crumb.label}
                </Link>
              ) : (
                <span className="text-fg-muted">{crumb.label}</span>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
