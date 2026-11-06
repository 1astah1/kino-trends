"use client";

// Источник: retentionos/packages/ui/components/pagination.tsx (скопировано 26.09.2026).
// Подписи по-русски; ответ списков API — {items, total, limit, offset} (§5.1).
import { Button } from "./button";

export function Pagination({
  total,
  limit,
  offset,
  onOffsetChange,
}: {
  total: number;
  limit: number;
  offset: number;
  onOffsetChange: (offset: number) => void;
}) {
  const page = Math.floor(offset / limit) + 1;
  const totalPages = Math.max(1, Math.ceil(total / limit));
  return (
    <div className="tabular flex items-center justify-between text-xs text-fg-muted">
      <span>{total === 0 ? "Нет записей" : `${offset + 1}–${Math.min(offset + limit, total)} из ${total}`}</span>
      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={offset === 0}
          onClick={() => onOffsetChange(Math.max(0, offset - limit))}
        >
          Назад
        </Button>
        <span>
          Стр. {page} из {totalPages}
        </span>
        <Button
          variant="outline"
          size="sm"
          disabled={offset + limit >= total}
          onClick={() => onOffsetChange(offset + limit)}
        >
          Дальше
        </Button>
      </div>
    </div>
  );
}
