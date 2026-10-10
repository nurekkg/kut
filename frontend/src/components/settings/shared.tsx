"use client";

import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useState, type ReactNode } from "react";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ApiError } from "@/lib/auth/api";
import { OBJECTS_ERRORS } from "@/lib/projects/types";
import { FIELD_ERRORS } from "@/lib/schedule/types";
import { KNOWN_ERRORS } from "@/lib/settings/types";

const KNOWN = new Set<string>(KNOWN_ERRORS);
const OBJECTS_KNOWN = new Set<string>(OBJECTS_ERRORS);
const FIELD_KNOWN = new Set<string>(FIELD_ERRORS);
type KnownError = (typeof KNOWN_ERRORS)[number];
type ObjectsError = (typeof OBJECTS_ERRORS)[number];
type FieldError = (typeof FIELD_ERRORS)[number];

// Текст ошибки API для пользователя: код из ответа, а не сообщение сервера. Общие коды (нет прав,
// неверные данные, повтор запроса) берутся из настроек, правила объектов и дерева - из своего раздела,
// правила ГПР, факта и фото - из раздела работ на объекте.
export function useCodeMessage(): (code: string) => string {
  const t = useTranslations("Settings.errors");
  const objects = useTranslations("ObjectsErrors");
  const field = useTranslations("FieldErrors");
  return (code) => {
    if (OBJECTS_KNOWN.has(code)) return objects(code as ObjectsError);
    if (FIELD_KNOWN.has(code)) return field(code as FieldError);
    return KNOWN.has(code) ? t(code as KnownError) : t("unknown");
  };
}

export function useErrorMessage(): (error: unknown) => string {
  const t = useTranslations("Settings.errors");
  const message = useCodeMessage();
  return (error) => (error instanceof ApiError ? message(error.code) : t("network"));
}

export function useDebounced<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}

type DataTableProps<T> = {
  columns: ColumnDef<T, unknown>[];
  data: T[];
  getRowId: (row: T) => string;
  empty: string;
};

export function DataTable<T>({ columns, data, getRowId, empty }: DataTableProps<T>) {
  const [sorting, setSorting] = useState<SortingState>([]);
  // eslint-disable-next-line react-hooks/incompatible-library -- TanStack Table не поддерживает мемоизацию компилятора
  const table = useReactTable({
    data,
    columns,
    getRowId,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  return (
    <div className="rounded-xl ring-1 ring-foreground/10">
      <Table>
        <TableHeader>
          {table.getHeaderGroups().map((group) => (
            <TableRow key={group.id} className="hover:bg-transparent">
              {group.headers.map((header) => {
                const sorted = header.column.getIsSorted();
                const label = flexRender(header.column.columnDef.header, header.getContext());
                return (
                  <TableHead
                    key={header.id}
                    aria-sort={
                      sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : undefined
                    }
                  >
                    {header.column.getCanSort() ? (
                      <button
                        type="button"
                        className="inline-flex items-center gap-1 rounded-sm outline-none hover:text-foreground/80 focus-visible:ring-3 focus-visible:ring-ring/50"
                        onClick={header.column.getToggleSortingHandler()}
                      >
                        {label}
                        {sorted === "asc" ? (
                          <ArrowUp className="size-3.5" aria-hidden />
                        ) : sorted === "desc" ? (
                          <ArrowDown className="size-3.5" aria-hidden />
                        ) : (
                          <ChevronsUpDown className="size-3.5 text-muted-foreground" aria-hidden />
                        )}
                      </button>
                    ) : (
                      label
                    )}
                  </TableHead>
                );
              })}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {table.getRowModel().rows.length === 0 ? (
            <TableRow className="hover:bg-transparent">
              <TableCell colSpan={columns.length} className="py-8 text-center text-muted-foreground">
                {empty}
              </TableCell>
            </TableRow>
          ) : (
            table.getRowModel().rows.map((row) => (
              <TableRow key={row.id}>
                {row.getVisibleCells().map((cell) => (
                  <TableCell key={cell.id}>
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </TableCell>
                ))}
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
}

export function PanelHeader({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="space-y-1">
        <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
        {description ? <p className="text-sm text-muted-foreground">{description}</p> : null}
      </div>
      {action}
    </div>
  );
}

export function QueryState({
  loading,
  error,
  onRetry,
  children,
}: {
  loading: boolean;
  error: unknown;
  onRetry: () => void;
  children: ReactNode;
}) {
  const t = useTranslations("Settings.common");
  const message = useErrorMessage();
  if (loading) {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        {t("loading")}
      </p>
    );
  }
  if (error) {
    return (
      <div role="alert" className="space-y-2 text-sm">
        <p>{message(error)}</p>
        <button type="button" className="text-primary underline-offset-4 hover:underline" onClick={onRetry}>
          {t("retry")}
        </button>
      </div>
    );
  }
  return <>{children}</>;
}

export function FormMessage({ id, error }: { id?: string; error: unknown }) {
  const message = useErrorMessage();
  return (
    <div id={id} role="alert" aria-live="assertive">
      {error ? <p className="text-sm text-destructive">{message(error)}</p> : null}
    </div>
  );
}
