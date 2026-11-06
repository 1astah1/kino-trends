import { ArrowRight, Hourglass, Smartphone } from "lucide-react";
import Link from "next/link";

import { Badge, Card } from "@/components/ui";
import { findRoute, MILESTONE_HINT, NAV_ROUTES, routeHref } from "@/lib/nav";

import { PageHeader } from "./page-header";

/**
 * Заглушка «Скоро» для экрана, который оживёт в следующем этапе (§10.1 п. 2: `lib/nav.ts`
 * заполняется всеми маршрутами v1 в M1). Показывает, что будет на экране, откуда он возьмёт данные
 * и что уже работает. Никаких фальшивых данных.
 */
export function ComingSoon({ path }: { path: string }) {
  const route = findRoute(path);
  if (!route) return null;
  const Icon = route.icon;
  const ready = NAV_ROUTES.filter((item) => item.status !== "soon");
  const children = NAV_ROUTES.filter((item) => item.parent === route.path);

  return (
    <div className="flex flex-col gap-5 p-4 md:p-6">
      <PageHeader
        eyebrow={MILESTONE_HINT[route.milestone]}
        title={route.title}
        description={route.description}
        actions={
          <Badge variant="machine" outline>
            <Hourglass className="h-3 w-3" aria-hidden="true" />
            Скоро · {route.milestone}
          </Badge>
        }
      />

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <Card className="safe-frame flex min-h-64 flex-col items-center justify-center gap-3 border-dashed p-8 text-center">
          <div className="flex h-11 w-11 items-center justify-center rounded-md border border-border bg-surface-2">
            <Icon className="h-5 w-5 text-accent" aria-hidden="true" />
          </div>
          <p className="text-base font-medium text-fg">Экран появится в этапе {route.milestone}</p>
          <p className="max-w-md text-sm text-fg-muted">
            Маршрут и место в меню уже зарезервированы. Когда бэкенд этапа будет готов, здесь откроется рабочий экран
            без изменений в навигации.
          </p>
          {route.mobile ? (
            <p className="flex items-center gap-1.5 text-xs text-fg-muted">
              <Smartphone className="h-3.5 w-3.5" aria-hidden="true" />
              Будет полноценно работать на телефоне
            </p>
          ) : null}
        </Card>

        <div className="flex flex-col gap-4">
          <Card className="p-3">
            <h2 className="mb-2 font-mono text-2xs tracking-wider text-fg-muted uppercase">Данные</h2>
            <ul className="flex flex-col gap-1">
              {route.endpoints.map((endpoint) => (
                <li key={endpoint} className="truncate font-mono text-xs text-fg" title={endpoint}>
                  <span className="text-fg-muted">/api/v1</span>
                  {endpoint}
                </li>
              ))}
            </ul>
            {route.urlState.length ? (
              <>
                <h2 className="mt-3 mb-1 font-mono text-2xs tracking-wider text-fg-muted uppercase">Состояние в URL</h2>
                <p className="font-mono text-xs text-fg">{route.urlState.map((key) => `?${key}=`).join("  ")}</p>
              </>
            ) : null}
          </Card>

          {children.length ? (
            <Card className="p-3">
              <h2 className="mb-2 font-mono text-2xs tracking-wider text-fg-muted uppercase">Вложенные экраны</h2>
              <ul className="flex flex-col gap-1">
                {children.map((child) => (
                  <li key={child.path}>
                    <Link href={routeHref(child.path)} className="text-sm text-accent hover:underline">
                      {child.title}
                    </Link>
                  </li>
                ))}
              </ul>
            </Card>
          ) : null}

          <Card className="p-3">
            <h2 className="mb-2 font-mono text-2xs tracking-wider text-fg-muted uppercase">Уже работает</h2>
            <ul className="flex flex-col gap-1">
              {ready.map((item) => (
                <li key={item.path}>
                  <Link
                    href={routeHref(item.path)}
                    className="inline-flex items-center gap-1.5 text-sm text-accent hover:underline"
                  >
                    <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
                    {item.title}
                  </Link>
                </li>
              ))}
              <li>
                <Link
                  href={routeHref("/settings", { tab: "system" })}
                  className="inline-flex items-center gap-1.5 text-sm text-accent hover:underline"
                >
                  <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
                  Настройки → Система
                </Link>
              </li>
            </ul>
          </Card>
        </div>
      </div>
    </div>
  );
}
