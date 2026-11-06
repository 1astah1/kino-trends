"use client";

import { ChevronRight, CircleCheck } from "lucide-react";
import Link from "next/link";

import { Badge, EmptyState } from "@/components/ui";
import { formatNumber } from "@/lib/format";
import type { OverviewTodo } from "@/lib/queries/overview";

import { TODO_ITEMS } from "./metric-meta";

/** «Сделать сейчас»: счётчики, которые ждут оператора; строка ведёт на экран с нужным фильтром. */
export function TodoList({ todo }: { todo: OverviewTodo }) {
  const items = TODO_ITEMS.filter((item) => (todo[item.key] ?? 0) > 0);
  if (items.length === 0) {
    return (
      <EmptyState
        compact
        icon={CircleCheck}
        title="Ничего не ждёт"
        description="Приёмка, пакеты, публикации и метрики в порядке."
      />
    );
  }
  return (
    <ul className="-mx-1 flex flex-col divide-y divide-border" data-testid="overview-todo">
      {items.map((item) => (
        <li key={item.key}>
          <Link
            href={item.href}
            className="flex items-center gap-2 rounded-md px-1 py-2 text-sm text-fg transition-colors duration-150 hover:bg-surface-2"
            data-testid={`todo-${item.key}`}
          >
            <Badge variant={item.tone} className="tabular min-w-7 justify-center font-mono">
              {formatNumber(todo[item.key])}
            </Badge>
            <span className="min-w-0 flex-1 truncate">{item.label}</span>
            <ChevronRight className="h-3.5 w-3.5 shrink-0 text-fg-muted" aria-hidden="true" />
          </Link>
        </li>
      ))}
    </ul>
  );
}
