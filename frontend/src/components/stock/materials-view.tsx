"use client";

import { useQuery } from "@tanstack/react-query";
import { flexRender, getCoreRowModel, useReactTable, type ColumnDef } from "@tanstack/react-table";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useMemo, useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { UserMenu } from "@/components/auth/user-menu";
import { AppNav } from "@/components/projects/app-nav";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { api } from "@/lib/api";
import { API } from "@/lib/projects/keys";
import { useMaterials } from "@/lib/stock/queries";
import type { MaterialOut } from "@/lib/stock/types";

import { MaterialsImportDialog } from "./materials-import-dialog";

type LegalEntity = { id: string; name: string };

// Справочник материалов юрлица: список, поиск, импорт. Колонка плана-цены - только при праве на цены
// закупок; без права элемента цены нет вовсе (не скрыт CSS).
export function MaterialsView() {
  const t = useTranslations("Stock.materials");
  const { user } = useAuth();
  const canSeePrices = user?.permissions.includes("procurement:read:prices") ?? false;
  const canImport = user?.permissions.includes("materials:write:catalog") ?? false;

  const entities = useQuery({
    queryKey: ["legal-entities"],
    queryFn: () => api.get<LegalEntity[]>(`${API}/materials/legal-entities`),
  });
  const [chosen, setChosen] = useState<string | null>(null);
  const legalEntityId = chosen ?? entities.data?.[0]?.id ?? null;
  const materials = useMaterials(legalEntityId, false);
  const [query, setQuery] = useState("");
  const [importOpen, setImportOpen] = useState(false);

  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const list = materials.data ?? [];
    if (needle === "") return list;
    return list.filter((item) => `${item.code} ${item.name}`.toLowerCase().includes(needle));
  }, [materials.data, query]);

  const columns = useMemo<ColumnDef<MaterialOut>[]>(() => {
    const base: ColumnDef<MaterialOut>[] = [
      {
        header: t("code"),
        accessorKey: "code",
        cell: ({ row }) => (
          <Link className="font-medium text-primary underline-offset-4 hover:underline" href={`/materials/${row.original.id}`}>
            {row.original.code}
          </Link>
        ),
      },
      { header: t("name"), accessorKey: "name" },
      { header: t("unit"), accessorKey: "base_unit" },
      {
        header: t("certificate"),
        accessorKey: "requires_certificate",
        cell: ({ row }) => (row.original.requires_certificate ? t("certRequired") : t("certNotRequired")),
      },
    ];
    if (canSeePrices) {
      base.push({
        header: t("planPrice"),
        id: "plan_price",
        cell: ({ row }) => (row.original.plan_price === null ? null : row.original.plan_price),
      });
    }
    return base;
  }, [canSeePrices, t]);

  // eslint-disable-next-line react-hooks/incompatible-library -- TanStack Table не поддерживает мемоизацию компилятора
  const table = useReactTable({ data: rows, columns, getCoreRowModel: getCoreRowModel() });

  return (
    <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-4 px-4 py-4">
      <UserMenu />
      <AppNav />
      <header className="flex flex-wrap items-end justify-between gap-3">
        <h1 className="text-2xl font-semibold">{t("title")}</h1>
        {canImport && legalEntityId !== null && (
          <Button onClick={() => setImportOpen(true)} type="button">
            {t("import")}
          </Button>
        )}
      </header>

      <div className="flex flex-wrap items-end gap-3">
        {(entities.data?.length ?? 0) > 1 && (
          <label className="space-y-1 text-sm">
            <span className="block text-muted-foreground">{t("legalEntity")}</span>
            <NativeSelect value={legalEntityId ?? ""} onChange={(event) => setChosen(event.target.value)}>
              {entities.data?.map((entity) => (
                <option key={entity.id} value={entity.id}>
                  {entity.name}
                </option>
              ))}
            </NativeSelect>
          </label>
        )}
        <label className="min-w-64 flex-1 space-y-1 text-sm">
          <span className="block text-muted-foreground">{t("search")}</span>
          <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("searchPlaceholder")} />
        </label>
      </div>

      {materials.isPending && <p className="text-sm text-muted-foreground">{t("loading")}</p>}
      {materials.isError && <p className="text-sm text-destructive">{t("loadError")}</p>}
      {materials.isSuccess && rows.length === 0 && <p className="text-sm text-muted-foreground">{t("empty")}</p>}

      {rows.length > 0 && (
        <div className="overflow-x-auto rounded-md border">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-left">
              {table.getHeaderGroups().map((group) => (
                <tr key={group.id}>
                  {group.headers.map((header) => (
                    <th className="px-3 py-2 font-medium" key={header.id}>
                      {flexRender(header.column.columnDef.header, header.getContext())}
                    </th>
                  ))}
                </tr>
              ))}
            </thead>
            <tbody>
              {table.getRowModel().rows.map((row) => (
                <tr className="border-t" key={row.id}>
                  {row.getVisibleCells().map((cell) => (
                    <td className="px-3 py-2" key={cell.id}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {importOpen && legalEntityId !== null && (
        <MaterialsImportDialog
          legalEntityId={legalEntityId}
          onClose={() => setImportOpen(false)}
        />
      )}
    </main>
  );
}
