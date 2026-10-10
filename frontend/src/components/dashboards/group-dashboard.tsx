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
import Link from "next/link";
import { useState } from "react";

import { DashboardStatus } from "@/components/dashboards/dashboard-status";
import { DataField } from "@/components/dashboards/data-field";
import { EmptyNote } from "@/components/dashboards/section";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  DASH,
  formatDay,
  formatDays,
  formatDeviation,
  formatMoney,
  formatPercent,
} from "@/lib/dashboards/format";
import { useGroupDashboard } from "@/lib/dashboards/queries";
import type { GroupRow } from "@/lib/dashboards/types";
import { isStage, isStatus } from "@/lib/projects/types";
import { useMediaQuery } from "@/lib/use-media-query";

const LINK_CLASS =
  "rounded-sm underline-offset-4 outline-none hover:underline focus-visible:ring-3 focus-visible:ring-ring/50";

// Таблица с сортировкой на широком экране, карточка на каждый объект - на телефоне.
export function GroupDashboard() {
  const t = useTranslations("Dashboard");
  const projects = useTranslations("Projects");
  const query = useGroupDashboard();
  const wide = useMediaQuery("(min-width: 768px)");
  const [sorting, setSorting] = useState<SortingState>([]);
  const data = query.data;
  const rows = data?.rows ?? [];
  const showBudget = rows.some((row) => row.budget !== null);

  const stageOf = (row: GroupRow) =>
    isStage(row.lifecycle_stage) ? projects(`stage.${row.lifecycle_stage}`) : DASH;
  const statusOf = (row: GroupRow) =>
    isStatus(row.status) ? projects(`status.${row.status}`) : row.status;

  const columns: ColumnDef<GroupRow, unknown>[] = [
    {
      id: "name",
      accessorKey: "name",
      header: t("group.object"),
      cell: ({ row }) => (
        <Link href={`/projects/${row.original.project_id}`} className={LINK_CLASS}>
          {row.original.name}
        </Link>
      ),
    },
    {
      id: "stage",
      accessorFn: stageOf,
      header: t("group.stage"),
      cell: ({ row }) => stageOf(row.original),
    },
    {
      id: "status",
      accessorFn: statusOf,
      header: t("group.status"),
      cell: ({ row }) => statusOf(row.original),
    },
    {
      id: "fact",
      accessorFn: (row) => (row.progress ? Number(row.progress.percent) : undefined),
      header: t("group.fact"),
      cell: ({ row }) => formatPercent(row.original.progress?.percent ?? null),
    },
    {
      id: "plan",
      accessorFn: (row) => (row.progress ? Number(row.progress.planned_percent) : undefined),
      header: t("group.plan"),
      cell: ({ row }) => formatPercent(row.original.progress?.planned_percent ?? null),
    },
    {
      id: "deviation",
      accessorFn: (row) => (row.progress ? Number(row.progress.deviation) : undefined),
      header: t("group.deviation"),
      cell: ({ row }) => formatDeviation(row.original.progress?.deviation ?? null),
    },
    {
      id: "lag",
      accessorFn: (row) => (row.progress ? Number(row.progress.delay_days) : undefined),
      header: t("group.lag"),
      cell: ({ row }) => formatDays(row.original.progress?.delay_days ?? null),
    },
    { id: "problems", accessorKey: "problems", header: t("group.problems") },
    { id: "approval", accessorKey: "awaiting_approval", header: t("group.approval") },
    ...(showBudget
      ? [
          {
            id: "budget",
            accessorFn: (row: GroupRow) => (row.budget ? Number(row.budget.planned_budget) : undefined),
            header: t("group.budget"),
            cell: ({ row }: { row: { original: GroupRow } }) =>
              row.original.budget
                ? formatMoney(row.original.budget.planned_budget, row.original.budget.currency)
                : DASH,
          },
        ]
      : []),
  ];

  // eslint-disable-next-line react-hooks/incompatible-library -- TanStack Table не поддерживает мемоизацию компилятора
  const table = useReactTable({
    data: rows,
    columns,
    getRowId: (row) => row.project_id,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  return (
    <div data-testid={data ? "group-dashboard" : undefined} className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">{t("group.title")}</h1>
        {data ? (
          <p className="text-sm text-muted-foreground">
            {t("asOf", { date: formatDay(data.as_of) })} · {t("group.rule", { days: data.thresholds.lag_days })}
          </p>
        ) : null}
      </header>

      {!data ? (
        <DashboardStatus
          loading={query.isPending}
          error={query.error}
          onRetry={() => void query.refetch()}
        />
      ) : rows.length === 0 ? (
        <EmptyNote>{t("group.empty")}</EmptyNote>
      ) : wide ? (
        <Table>
          <TableHeader>
            {table.getHeaderGroups().map((group) => (
              <TableRow key={group.id} className="hover:bg-transparent">
                {group.headers.map((header) => {
                  const sorted = header.column.getIsSorted();
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
                          {flexRender(header.column.columnDef.header, header.getContext())}
                          {sorted === "asc" ? (
                            <ArrowUp className="size-3.5" aria-hidden />
                          ) : sorted === "desc" ? (
                            <ArrowDown className="size-3.5" aria-hidden />
                          ) : (
                            <ChevronsUpDown className="size-3.5 text-muted-foreground" aria-hidden />
                          )}
                        </button>
                      ) : (
                        flexRender(header.column.columnDef.header, header.getContext())
                      )}
                    </TableHead>
                  );
                })}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {table.getRowModel().rows.map((row) => (
              <TableRow
                key={row.id}
                data-testid="group-row"
                data-project-id={row.original.project_id}
              >
                {row.getVisibleCells().map((cell) => (
                  <TableCell key={cell.id}>
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : (
        <ul className="space-y-3">
          {table.getRowModel().rows.map(({ original: item }) => (
            <li
              key={item.project_id}
              data-testid="group-row"
              data-project-id={item.project_id}
              className="rounded-xl bg-card p-4 ring-1 ring-foreground/10"
            >
              <h2 className="text-base font-medium break-words">
                <Link href={`/projects/${item.project_id}`} className={LINK_CLASS}>
                  {item.name}
                </Link>
              </h2>
              <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
                <DataField label={t("group.stage")}>{stageOf(item)}</DataField>
                <DataField label={t("group.status")}>{statusOf(item)}</DataField>
                <DataField label={t("group.fact")}>
                  {formatPercent(item.progress?.percent ?? null)}
                </DataField>
                <DataField label={t("group.plan")}>
                  {formatPercent(item.progress?.planned_percent ?? null)}
                </DataField>
                <DataField label={t("group.deviation")}>
                  {formatDeviation(item.progress?.deviation ?? null)}
                </DataField>
                <DataField label={t("group.lag")}>
                  {formatDays(item.progress?.delay_days ?? null)}
                </DataField>
                <DataField label={t("group.problems")}>{item.problems}</DataField>
                <DataField label={t("group.approval")}>{item.awaiting_approval}</DataField>
                {showBudget ? (
                  <DataField label={t("group.budget")}>
                    {item.budget ? formatMoney(item.budget.planned_budget, item.budget.currency) : DASH}
                  </DataField>
                ) : null}
              </dl>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
