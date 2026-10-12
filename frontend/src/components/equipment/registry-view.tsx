"use client";

import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
} from "@tanstack/react-table";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useMemo, useRef, useState } from "react";

import { UnitStatusBadge } from "@/components/equipment/badges";
import { useDebounced } from "@/components/settings/shared";
import { buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { useEquipmentAccess } from "@/lib/equipment/access";
import { useEquipmentTypes, useUnits, useVisibleProjects } from "@/lib/equipment/queries";
import { UNIT_STATUSES, type UnitListItem } from "@/lib/equipment/types";
import { useRowWindow } from "@/lib/use-row-window";
import { cn } from "@/lib/utils";

const ROW_HEIGHT = 44;
const HEADER_HEIGHT = 40;
const OWNERSHIP = ["own", "rented"] as const;

// Реестр техники: строки видны по RLS (прораб - своих объектов, машинист - своих единиц). Денег в
// реестре нет: тарифы и договоры живут в карточке и разделе договоров, за правом read:rates.
export function RegistryView() {
  const t = useTranslations("Equipment.registry");
  const common = useTranslations("Equipment.common");
  const statusText = useTranslations("Equipment.status");
  const access = useEquipmentAccess();
  const [search, setSearch] = useState("");
  const query = useDebounced(search.trim(), 300);
  const [typeId, setTypeId] = useState("");
  const [ownership, setOwnership] = useState("");
  const [status, setStatus] = useState("");
  const [projectId, setProjectId] = useState("");
  const types = useEquipmentTypes();
  const projects = useVisibleProjects(access.canObjects);
  const units = useUnits({
    typeId: typeId || undefined,
    ownership: ownership || undefined,
    q: query || undefined,
    status: status || undefined,
    projectId: projectId || undefined,
  });
  const rows = useMemo<UnitListItem[]>(() => units.data ?? [], [units.data]);

  const columns = useMemo<ColumnDef<UnitListItem>[]>(
    () => [
      {
        id: "number",
        header: () => t("columns.number"),
        cell: ({ row }) => (
          <Link
            href={`/equipment/${row.original.id}`}
            className="rounded-sm font-medium underline-offset-4 outline-none hover:underline focus-visible:ring-3 focus-visible:ring-ring/50"
          >
            {row.original.registry_number}
          </Link>
        ),
      },
      { id: "type", header: () => t("columns.type"), cell: ({ row }) => row.original.type_code },
      {
        id: "ownership",
        header: () => t("columns.ownership"),
        cell: ({ row }) => t(`ownership.${row.original.ownership}`),
      },
      { id: "model", header: () => t("columns.model"), cell: ({ row }) => row.original.model ?? "—" },
      {
        id: "year",
        header: () => t("columns.year"),
        cell: ({ row }) => <span className="tabular-nums">{row.original.year ?? "—"}</span>,
      },
      {
        id: "status",
        header: () => t("columns.status"),
        cell: ({ row }) => <UnitStatusBadge status={row.original.status} />,
      },
    ],
    [t],
  );

  // eslint-disable-next-line react-hooks/incompatible-library -- TanStack Table не поддерживает мемоизацию компилятора
  const table = useReactTable({
    data: rows,
    columns,
    getRowId: (row) => row.id,
    getCoreRowModel: getCoreRowModel(),
  });
  const tableRows = table.getRowModel().rows;
  const headers = table.getHeaderGroups()[0]?.headers ?? [];
  const scroller = useRef<HTMLDivElement>(null);
  const { start, end } = useRowWindow(scroller, tableRows.length, ROW_HEIGHT, { offset: HEADER_HEIGHT });

  return (
    <section className="flex flex-col gap-4" aria-labelledby="registry-title">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 id="registry-title" className="text-xl font-semibold tracking-tight">
            {t("title")}
          </h1>
          <p className="text-sm text-muted-foreground" data-testid="registry-count">
            {units.isSuccess ? t("count", { count: rows.length }) : common("loading")}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link href="/equipment/calendar" className={buttonVariants({ variant: "outline" })}>
            {t("toCalendar")}
          </Link>
          {access.canWrite ? (
            <Link href="/equipment/import" className={buttonVariants({ variant: "default" })}>
              {t("toImport")}
            </Link>
          ) : null}
        </div>
      </div>

      <div
        className={cn("grid gap-2 sm:grid-cols-2", access.canObjects ? "lg:grid-cols-5" : "lg:grid-cols-4")}
        role="search"
      >
        <Input
          aria-label={t("searchLabel")}
          placeholder={t("searchPlaceholder")}
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          data-testid="registry-search"
        />
        <NativeSelect
          aria-label={t("typeLabel")}
          value={typeId}
          onChange={(event) => setTypeId(event.target.value)}
          data-testid="registry-type"
        >
          <option value="">{t("allTypes")}</option>
          {(types.data ?? []).map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
        </NativeSelect>
        <NativeSelect
          aria-label={t("ownershipLabel")}
          value={ownership}
          onChange={(event) => setOwnership(event.target.value)}
          data-testid="registry-ownership"
        >
          <option value="">{t("allOwnership")}</option>
          {OWNERSHIP.map((value) => (
            <option key={value} value={value}>
              {t(`ownership.${value}`)}
            </option>
          ))}
        </NativeSelect>
        <NativeSelect
          aria-label={t("statusLabel")}
          value={status}
          onChange={(event) => setStatus(event.target.value)}
          data-testid="registry-status"
        >
          <option value="">{t("allStatuses")}</option>
          {UNIT_STATUSES.map((value) => (
            <option key={value} value={value}>
              {statusText(value)}
            </option>
          ))}
        </NativeSelect>
        {access.canObjects ? (
          <NativeSelect
            aria-label={t("projectLabel")}
            value={projectId}
            onChange={(event) => setProjectId(event.target.value)}
            data-testid="registry-project"
          >
            <option value="">{t("allProjects")}</option>
            {(projects.data ?? []).map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </NativeSelect>
        ) : null}
      </div>

      {units.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {common("loadFailed")}
        </p>
      ) : null}

      {units.isSuccess && rows.length === 0 ? (
        <p className="rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">
          {t("empty")}
        </p>
      ) : null}

      {rows.length > 0 ? (
        <>
          <div
            ref={scroller}
            data-testid="registry-table"
            className="relative hidden h-[min(68dvh,44rem)] min-h-80 overflow-auto rounded-xl border md:block"
          >
            <table className="w-full table-fixed border-separate border-spacing-0 text-sm">
              <colgroup>
                <col className="w-[18%]" />
                <col className="w-[14%]" />
                <col className="w-[12%]" />
                <col className="w-[26%]" />
                <col className="w-[8%]" />
                <col className="w-[22%]" />
              </colgroup>
              <thead>
                {table.getHeaderGroups().map((group) => (
                  <tr key={group.id} style={{ height: HEADER_HEIGHT }}>
                    {group.headers.map((header) => (
                      <th
                        key={header.id}
                        scope="col"
                        className="sticky top-0 z-10 border-b bg-background px-3 text-left text-xs font-medium text-muted-foreground"
                      >
                        {flexRender(header.column.columnDef.header, header.getContext())}
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody>
                {start > 0 ? (
                  <tr aria-hidden style={{ height: start * ROW_HEIGHT }}>
                    <td colSpan={headers.length} className="p-0" />
                  </tr>
                ) : null}
                {tableRows.slice(start, end).map((row) => (
                  <tr key={row.id} style={{ height: ROW_HEIGHT }} className="hover:bg-muted/50">
                    {row.getVisibleCells().map((cell) => (
                      <td key={cell.id} className="truncate border-b px-3">
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                    ))}
                  </tr>
                ))}
                {end < tableRows.length ? (
                  <tr aria-hidden style={{ height: (tableRows.length - end) * ROW_HEIGHT }}>
                    <td colSpan={headers.length} className="p-0" />
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>

          <ul className="flex flex-col gap-2 md:hidden" data-testid="registry-cards">
            {rows.map((unit) => (
              <li key={unit.id}>
                <Link
                  href={`/equipment/${unit.id}`}
                  className={cn(
                    "flex min-h-14 flex-col gap-2 rounded-xl border bg-card p-3 outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
                  )}
                >
                  <div className="flex items-start justify-between gap-2">
                    <span className="min-w-0 break-all font-medium">{unit.registry_number}</span>
                    <UnitStatusBadge status={unit.status} />
                  </div>
                  <div className="flex flex-wrap gap-x-3 gap-y-1 text-sm text-muted-foreground">
                    <span>{unit.type_code}</span>
                    <span>{t(`ownership.${unit.ownership}`)}</span>
                    {unit.model ? <span className="min-w-0 truncate">{unit.model}</span> : null}
                    {unit.year ? <span className="tabular-nums">{unit.year}</span> : null}
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </section>
  );
}
