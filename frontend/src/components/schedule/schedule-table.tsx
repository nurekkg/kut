"use client";

import {
  flexRender,
  getCoreRowModel,
  getExpandedRowModel,
  getGroupedRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type ExpandedState,
  type SortingState,
  type Table,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ChevronDown, ChevronRight, ChevronsUpDown } from "lucide-react";
import { useTranslations } from "next-intl";
import { useRef, useState } from "react";

import { GROUP_COLUMN, RIGHT_ALIGNED } from "@/components/schedule/schedule-columns";
import { ProgressBar } from "@/components/schedule/progress-bar";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "@/lib/schedule/layout";
import type { NodeInfo, ScheduleRow } from "@/lib/schedule/rows";
import { useRowWindow } from "@/lib/use-row-window";
import { cn } from "@/lib/utils";

const GROUPING = [GROUP_COLUMN];

// Таблица ГПР: TanStack Table держит группировку по узлу, раскрытие и сортировку, а строки в DOM
// рисуются окном (use-row-window). Сортировка двигает и группы: по агрегату колонки.
export function useScheduleTable(
  rows: ScheduleRow[],
  columns: ColumnDef<ScheduleRow, unknown>[],
): Table<ScheduleRow> {
  const [sorting, setSorting] = useState<SortingState>([]);
  const [expanded, setExpanded] = useState<ExpandedState>(true);
  // eslint-disable-next-line react-hooks/incompatible-library -- TanStack Table не поддерживает мемоизацию компилятора
  return useReactTable({
    data: rows,
    columns,
    getRowId: (row) => row.id,
    state: { sorting, expanded, grouping: GROUPING },
    onSortingChange: setSorting,
    onExpandedChange: setExpanded,
    groupedColumnMode: "remove",
    autoResetExpanded: false,
    getCoreRowModel: getCoreRowModel(),
    getGroupedRowModel: getGroupedRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });
}

type Props = {
  table: Table<ScheduleRow>;
  nodes: Map<string, NodeInfo>;
  empty: string;
  className?: string;
};

export function ScheduleTable({ table, nodes, empty, className }: Props) {
  const t = useTranslations("Schedule");
  const scroller = useRef<HTMLDivElement>(null);
  const rows = table.getRowModel().rows;
  const headers = table.getHeaderGroups()[0]?.headers ?? [];
  const width = headers.reduce((total, header) => total + header.getSize(), 0);
  const { start, end } = useRowWindow(scroller, rows.length, ROW_HEIGHT, {
    offset: TABLE_HEADER_HEIGHT,
  });

  return (
    <div
      ref={scroller}
      data-testid="schedule-scroller"
      className={cn(
        "relative h-[min(68dvh,46rem)] min-h-80 overflow-auto rounded-xl border",
        className,
      )}
    >
      <table
        aria-rowcount={rows.length + 1}
        className="table-fixed border-separate border-spacing-0 text-sm"
        style={{ width }}
      >
        <colgroup>
          {headers.map((header) => (
            <col key={header.id} style={{ width: header.getSize() }} />
          ))}
        </colgroup>
        <thead>
          <tr aria-rowindex={1} style={{ height: TABLE_HEADER_HEIGHT }}>
            {headers.map((header) => {
              const sorted = header.column.getIsSorted();
              const label = flexRender(header.column.columnDef.header, header.getContext());
              const right = RIGHT_ALIGNED.has(header.column.id);
              return (
                <th
                  key={header.id}
                  scope="col"
                  aria-sort={
                    sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : undefined
                  }
                  className={cn(
                    "sticky top-0 z-10 border-b bg-background px-2 text-left text-xs font-medium whitespace-nowrap text-muted-foreground",
                    right && "text-right",
                  )}
                >
                  {header.column.getCanSort() ? (
                    <button
                      type="button"
                      className={cn(
                        "inline-flex items-center gap-1 rounded-sm outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50",
                        right && "flex-row-reverse",
                      )}
                      onClick={header.column.getToggleSortingHandler()}
                    >
                      {label}
                      {sorted === "asc" ? (
                        <ArrowUp className="size-3.5" aria-hidden />
                      ) : sorted === "desc" ? (
                        <ArrowDown className="size-3.5" aria-hidden />
                      ) : (
                        <ChevronsUpDown className="size-3.5 opacity-60" aria-hidden />
                      )}
                    </button>
                  ) : (
                    label
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {start > 0 ? (
            <tr aria-hidden style={{ height: start * ROW_HEIGHT }}>
              <td colSpan={headers.length} className="p-0" />
            </tr>
          ) : null}
          {rows.slice(start, end).map((row, offset) => {
            const position = start + offset + 2;
            if (row.getIsGrouped()) {
              const node = nodes.get(String(row.groupingValue));
              return (
                <tr
                  key={row.id}
                  aria-rowindex={position}
                  data-testid="schedule-group"
                  className="bg-muted/50"
                  style={{ height: ROW_HEIGHT }}
                >
                  <td colSpan={headers.length} className="border-b p-0">
                    <button
                      type="button"
                      onClick={row.getToggleExpandedHandler()}
                      aria-expanded={row.getIsExpanded()}
                      className="flex w-full min-w-0 items-center text-left text-sm font-medium outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
                      style={{ height: ROW_HEIGHT - 1 }}
                    >
                      <span className="sticky left-0 flex min-w-0 max-w-[min(100vw-2rem,56rem)] items-center gap-2 px-2">
                        {row.getIsExpanded() ? (
                          <ChevronDown className="size-4 shrink-0" aria-hidden />
                        ) : (
                          <ChevronRight className="size-4 shrink-0" aria-hidden />
                        )}
                        <span className="truncate">{node?.label ?? t("unknownNode")}</span>
                        <span className="shrink-0 text-xs font-normal text-muted-foreground">
                          {t("worksCount", { count: row.subRows.length })}
                        </span>
                        <ProgressBar
                          percent={row.getValue<number>("percent")}
                          label={node?.label}
                          className="ml-2 shrink-0"
                        />
                      </span>
                    </button>
                  </td>
                </tr>
              );
            }
            return (
              <tr
                key={row.id}
                aria-rowindex={position}
                data-testid="schedule-row"
                className="hover:bg-muted/40"
                style={{ height: ROW_HEIGHT }}
              >
                {row.getVisibleCells().map((cell) => (
                  <td
                    key={cell.id}
                    className={cn(
                      "overflow-hidden border-b px-2 whitespace-nowrap",
                      RIGHT_ALIGNED.has(cell.column.id) && "text-right",
                    )}
                  >
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            );
          })}
          {end < rows.length ? (
            <tr aria-hidden style={{ height: (rows.length - end) * ROW_HEIGHT }}>
              <td colSpan={headers.length} className="p-0" />
            </tr>
          ) : null}
          {rows.length === 0 ? (
            <tr>
              <td colSpan={headers.length} className="py-10 text-center text-muted-foreground">
                {empty}
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </div>
  );
}
