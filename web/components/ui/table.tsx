"use client";

// Источник: retentionos/packages/ui/components/table.tsx (скопировано 26.09.2026).
// «Аппаратная»: строки 32 px (compact — 28 px), шапка на --surface-2, числа tabular-nums.
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import type { HTMLAttributes, ReactNode, TdHTMLAttributes, ThHTMLAttributes } from "react";

import { cn } from "@/lib/cn";

import { EmptyState } from "./empty-state";
import { SkeletonTable } from "./skeleton";

export function Table({
  className,
  minWidth = 640,
  ...props
}: HTMLAttributes<HTMLTableElement> & { minWidth?: number }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className={cn("tabular w-full text-left text-sm", className)} style={{ minWidth }} {...props} />
    </div>
  );
}

export function TableHead({ className, ...props }: HTMLAttributes<HTMLTableSectionElement>) {
  return <thead className={cn("border-b border-border bg-surface-2 text-xs text-fg-muted", className)} {...props} />;
}

export function TableBody({ className, ...props }: HTMLAttributes<HTMLTableSectionElement>) {
  return <tbody className={cn("divide-y divide-border bg-surface", className)} {...props} />;
}

export function TableRow({
  className,
  clickable,
  ...props
}: HTMLAttributes<HTMLTableRowElement> & { clickable?: boolean }) {
  return (
    <tr
      className={cn("h-8", clickable && "cursor-pointer transition-colors duration-150 hover:bg-surface-2", className)}
      {...props}
    />
  );
}

export function Th({ className, ...props }: ThHTMLAttributes<HTMLTableCellElement>) {
  return <th scope="col" className={cn("h-8 px-3 font-medium whitespace-nowrap", className)} {...props} />;
}

export function SortableTh({
  className,
  active,
  direction,
  onSort,
  children,
  ...props
}: ThHTMLAttributes<HTMLTableCellElement> & {
  active?: boolean;
  direction?: "asc" | "desc";
  onSort?: () => void;
}) {
  const Icon = active ? (direction === "asc" ? ArrowUp : ArrowDown) : ArrowUpDown;
  return (
    <th
      scope="col"
      aria-sort={active ? (direction === "asc" ? "ascending" : "descending") : "none"}
      className={cn("h-8 px-3 font-medium whitespace-nowrap", className)}
      {...props}
    >
      <button
        type="button"
        onClick={onSort}
        className={cn("inline-flex items-center gap-1 hover:text-fg", active && "text-fg")}
      >
        {children}
        <Icon className="h-3 w-3" aria-hidden="true" />
      </button>
    </th>
  );
}

export function Td({ className, ...props }: TdHTMLAttributes<HTMLTableCellElement>) {
  return <td className={cn("px-3 py-1", className)} {...props} />;
}

export interface DataTableColumn<T> {
  key: string;
  header: ReactNode;
  render: (row: T) => ReactNode;
  sortable?: boolean;
  className?: string;
}

export interface DataTableProps<T> {
  columns: Array<DataTableColumn<T>>;
  rows: T[];
  rowKey: (row: T) => string;
  onRowClick?: (row: T) => void;
  sortKey?: string;
  sortDirection?: "asc" | "desc";
  onSort?: (key: string) => void;
  isLoading?: boolean;
  isError?: boolean;
  errorContent?: ReactNode;
  emptyTitle?: string;
  emptyDescription?: string;
  skeletonRows?: number;
  minWidth?: number;
  caption?: string;
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  onRowClick,
  sortKey,
  sortDirection,
  onSort,
  isLoading,
  isError,
  errorContent,
  emptyTitle = "Пусто",
  emptyDescription,
  skeletonRows = 6,
  minWidth,
  caption,
}: DataTableProps<T>) {
  if (isLoading) return <SkeletonTable rows={skeletonRows} />;
  if (isError) return <>{errorContent}</>;
  if (rows.length === 0) return <EmptyState title={emptyTitle} description={emptyDescription} compact />;

  return (
    <Table minWidth={minWidth}>
      {caption ? <caption className="sr-only">{caption}</caption> : null}
      <TableHead>
        <tr>
          {columns.map((col) =>
            col.sortable ? (
              <SortableTh
                key={col.key}
                className={col.className}
                active={sortKey === col.key}
                direction={sortKey === col.key ? sortDirection : undefined}
                onSort={() => onSort?.(col.key)}
              >
                {col.header}
              </SortableTh>
            ) : (
              <Th key={col.key} className={col.className}>
                {col.header}
              </Th>
            ),
          )}
        </tr>
      </TableHead>
      <TableBody>
        {rows.map((row) => (
          <TableRow key={rowKey(row)} clickable={!!onRowClick} onClick={onRowClick ? () => onRowClick(row) : undefined}>
            {columns.map((col) => (
              <Td key={col.key} className={col.className}>
                {col.render(row)}
              </Td>
            ))}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
