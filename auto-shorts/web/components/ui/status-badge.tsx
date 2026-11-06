// Источник: retentionos/packages/ui/components/status-badge.tsx (скопировано 26.09.2026).
// «Статус всегда цветом и иконкой с подписью» (§7.4): у каждой записи карты есть иконка.
import type { LucideIcon } from "lucide-react";

import { Badge, type BadgeProps } from "./badge";

export interface StatusMeta {
  label: string;
  variant: BadgeProps["variant"];
  icon: LucideIcon;
  outline?: boolean;
}

export type StatusMetaMap<T extends string> = Record<T, StatusMeta>;

export function StatusBadge<T extends string>({
  status,
  map,
  fallbackLabel,
  className,
}: {
  status: T;
  map: StatusMetaMap<T>;
  fallbackLabel?: string;
  className?: string;
}) {
  const meta = map[status] as StatusMeta | undefined;
  const Icon = meta?.icon;
  return (
    <Badge variant={meta?.variant ?? "neutral"} outline={meta?.outline} className={className}>
      {Icon ? <Icon className="h-3 w-3" aria-hidden="true" /> : null}
      {meta?.label ?? fallbackLabel ?? status}
    </Badge>
  );
}
