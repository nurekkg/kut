"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { flexRender, getCoreRowModel, useReactTable, type ColumnDef } from "@tanstack/react-table";
import { ArrowLeft, ChevronLeft, ChevronRight, Download, FilePlus } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useMemo, useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { UserMenu } from "@/components/auth/user-menu";
import { CreateDocumentDialog } from "@/components/documents/create-document-dialog";
import { StatusBadge, useDocumentErrorMessage, useKindLabel, useStatusLabel } from "@/components/documents/shared";
import { useDebounced } from "@/components/settings/shared";
import { AppNav } from "@/components/projects/app-nav";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import {
  canCreate,
  canExport,
  canSeeAmountColumn,
  canShowAmounts,
  canUseCounterparties,
} from "@/lib/documents/access";
import { formatAmount, formatDay } from "@/lib/documents/format";
import {
  exportPath,
  type DocumentFilters,
  useDocumentKinds,
  useDocuments,
} from "@/lib/documents/queries";
import { DOCUMENT_STATUSES, type DocumentOut } from "@/lib/documents/types";
import { fetchBlob, saveBlob } from "@/lib/equipment/download";
import { useCounterparties } from "@/lib/equipment/queries";
import { api } from "@/lib/api";
import { API, projectKey } from "@/lib/projects/keys";
import type { Project } from "@/lib/projects/types";
import { todayIso } from "@/lib/schedule/dates";
import { useMediaQuery } from "@/lib/use-media-query";

export const PAGE_SIZE = 50;

// Реестр документов объекта. Строки приходят постранично с сервера; таблица на планшете и выше,
// карточки на телефоне - выбор по ширине экрана, а не CSS: второй вариант не должен считаться строками.
export function DocumentsRegistry({ projectId }: { projectId: string }) {
  const t = useTranslations("Documents.registry");
  const common = useTranslations("Documents.common");
  const statusText = useStatusLabel();
  const kindLabel = useKindLabel();
  const message = useDocumentErrorMessage();
  const { user } = useAuth();
  const permissions = user?.permissions ?? [];
  const internal = user?.is_internal ?? false;
  const wide = useMediaQuery("(min-width: 768px)");

  const [kind, setKind] = useState("");
  const [status, setStatus] = useState("");
  const [counterpartyId, setCounterpartyId] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [creating, setCreating] = useState(false);
  const query = useDebounced(search.trim(), 300);

  const filters = useMemo<DocumentFilters>(
    () => ({
      projectId,
      kind: kind || undefined,
      status: status || undefined,
      counterpartyId: counterpartyId || undefined,
      dateFrom: dateFrom || undefined,
      dateTo: dateTo || undefined,
      q: query || undefined,
    }),
    [projectId, kind, status, counterpartyId, dateFrom, dateTo, query],
  );
  const list = useDocuments(filters, { limit: PAGE_SIZE, offset: page * PAGE_SIZE });
  const rows = useMemo<DocumentOut[]>(() => list.data ?? [], [list.data]);
  const kinds = useDocumentKinds();
  const counterpartySource = canUseCounterparties(permissions);
  const counterparties = useCounterparties(counterpartySource);
  // Юрлицо для создания берётся из объекта; внешним ролям карточка объекта не нужна.
  const project = useQuery({
    queryKey: projectKey(projectId),
    queryFn: () => api.get<Project>(`${API}/projects/${projectId}`),
    enabled: internal,
  });
  const exporting = useMutation({
    mutationFn: () => fetchBlob(exportPath(filters)),
    onSuccess: (blob) => saveBlob(blob, `documents-${todayIso()}.xlsx`),
  });

  const showAmount = canSeeAmountColumn(rows) && canShowAmounts(permissions, internal);
  const columns = useMemo<ColumnDef<DocumentOut>[]>(() => {
    const base: ColumnDef<DocumentOut>[] = [
      {
        id: "number",
        header: () => t("columns.number"),
        cell: ({ row }) => (
          <Link
            href={`/documents/${row.original.id}`}
            className="break-all rounded-sm font-medium underline-offset-4 outline-none hover:underline focus-visible:ring-3 focus-visible:ring-ring/50"
          >
            {row.original.registration_no ?? t("noValue")}
          </Link>
        ),
      },
      { id: "kind", header: () => t("columns.kind"), cell: ({ row }) => kindLabel(row.original.kind) },
      {
        id: "title",
        header: () => t("columns.title"),
        cell: ({ row }) => <span className="break-words">{row.original.title}</span>,
      },
      {
        id: "counterparty",
        header: () => t("columns.counterparty"),
        cell: ({ row }) => row.original.counterparty_name ?? t("noValue"),
      },
      {
        id: "status",
        header: () => t("columns.status"),
        cell: ({ row }) => <StatusBadge status={row.original.latest_status} />,
      },
      {
        id: "date",
        header: () => t("columns.date"),
        cell: ({ row }) => <span className="tabular-nums">{formatDay(row.original.created_at)}</span>,
      },
    ];
    if (showAmount) {
      base.push({
        id: "amount",
        header: () => t("columns.amount"),
        cell: ({ row }) =>
          row.original.amount === null ? (
            t("noValue")
          ) : (
            <span className="tabular-nums">{formatAmount(row.original.amount)}</span>
          ),
      });
    }
    return base;
  }, [t, kindLabel, showAmount]);

  // eslint-disable-next-line react-hooks/incompatible-library -- TanStack Table не поддерживает мемоизацию компилятора
  const table = useReactTable({
    data: rows,
    columns,
    getRowId: (row) => row.id,
    getCoreRowModel: getCoreRowModel(),
  });

  const hasNext = rows.length === PAGE_SIZE;
  const kindsList = kinds.data ?? [];
  const failed = list.isError ? message(list.error) : null;

  return (
    <main
      className="mx-auto flex w-full max-w-7xl flex-1 flex-col gap-4 px-4 py-4"
      data-testid="documents-registry"
    >
      <UserMenu />
      <AppNav />
      <Link
        href={`/projects/${projectId}`}
        className="inline-flex min-h-10 w-fit items-center gap-1.5 rounded-md text-sm text-muted-foreground outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50"
      >
        <ArrowLeft className="size-4" aria-hidden />
        {t("toProject")}
      </Link>

      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">{t("title")}</h1>
          {project.data ? (
            <p className="text-sm break-words text-muted-foreground">{t("subtitle", { name: project.data.name })}</p>
          ) : null}
        </div>
        <div className="flex flex-wrap gap-2">
          {canExport(permissions) ? (
            <Button
              type="button"
              variant="outline"
              size="lg"
              className="h-10"
              disabled={exporting.isPending}
              onClick={() => exporting.mutate()}
              data-testid="documents-export"
            >
              <Download data-icon="inline-start" aria-hidden />
              {exporting.isPending ? t("exporting") : t("export")}
            </Button>
          ) : null}
          {canCreate(permissions) && internal ? (
            <Button
              type="button"
              size="lg"
              className="h-10"
              onClick={() => setCreating(true)}
              data-testid="documents-create"
            >
              <FilePlus data-icon="inline-start" aria-hidden />
              {t("create")}
            </Button>
          ) : null}
        </div>
      </header>

      <div role="search" className="grid gap-2 sm:grid-cols-2 lg:grid-cols-6">
        <Input
          type="search"
          aria-label={t("searchLabel")}
          placeholder={t("searchPlaceholder")}
          maxLength={64}
          value={search}
          onChange={(event) => {
            setSearch(event.target.value);
            setPage(0);
          }}
          data-testid="documents-search"
        />
        <NativeSelect
          aria-label={t("kindLabel")}
          value={kind}
          onChange={(event) => {
            setKind(event.target.value);
            setPage(0);
          }}
          data-testid="documents-filter-kind"
        >
          <option value="">{t("allKinds")}</option>
          {kindsList.map((item) => (
            <option key={item.code} value={item.code}>
              {kindLabel(item.code)}
            </option>
          ))}
        </NativeSelect>
        <NativeSelect
          aria-label={t("statusLabel")}
          value={status}
          onChange={(event) => {
            setStatus(event.target.value);
            setPage(0);
          }}
          data-testid="documents-filter-status"
        >
          <option value="">{t("allStatuses")}</option>
          {DOCUMENT_STATUSES.map((value) => (
            <option key={value} value={value}>
              {statusText(value)}
            </option>
          ))}
        </NativeSelect>
        {counterpartySource ? (
          <NativeSelect
            aria-label={t("counterpartyLabel")}
            value={counterpartyId}
            onChange={(event) => {
              setCounterpartyId(event.target.value);
              setPage(0);
            }}
            data-testid="documents-filter-counterparty"
          >
            <option value="">{t("allCounterparties")}</option>
            {(counterparties.data ?? []).map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </NativeSelect>
        ) : null}
        <Input
          type="date"
          aria-label={t("dateFrom")}
          value={dateFrom}
          onChange={(event) => {
            setDateFrom(event.target.value);
            setPage(0);
          }}
          data-testid="documents-filter-from"
        />
        <Input
          type="date"
          aria-label={t("dateTo")}
          value={dateTo}
          onChange={(event) => {
            setDateTo(event.target.value);
            setPage(0);
          }}
          data-testid="documents-filter-to"
        />
      </div>

      {failed ? (
        <p role="alert" className="text-sm text-destructive">
          {failed}
        </p>
      ) : null}
      {exporting.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {message(exporting.error)}
        </p>
      ) : null}

      {list.isPending && rows.length === 0 ? <p className="text-sm text-muted-foreground">{common("loading")}</p> : null}
      {list.isSuccess && rows.length === 0 ? (
        <p className="rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">{t("empty")}</p>
      ) : null}

      {rows.length > 0 && wide ? (
        <div className="overflow-x-auto rounded-xl border" data-testid="documents-table">
          <table className="w-full border-collapse text-sm">
            <thead className="bg-muted/40 text-left text-xs text-muted-foreground">
              {table.getHeaderGroups().map((group) => (
                <tr key={group.id}>
                  {group.headers.map((header) => (
                    <th
                      key={header.id}
                      scope="col"
                      className="px-3 py-2 font-medium"
                      data-testid={header.column.id === "amount" ? "documents-amount-header" : undefined}
                    >
                      {flexRender(header.column.columnDef.header, header.getContext())}
                    </th>
                  ))}
                </tr>
              ))}
            </thead>
            <tbody>
              {table.getRowModel().rows.map((row) => (
                <tr
                  key={row.id}
                  data-testid="documents-row"
                  data-document-id={row.original.id}
                  className="border-t align-top hover:bg-muted/50"
                >
                  {row.getVisibleCells().map((cell) => (
                    <td key={cell.id} className="px-3 py-2">
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {rows.length > 0 && !wide ? (
        <ul className="flex flex-col gap-2" data-testid="documents-cards">
          {rows.map((row) => (
            <li key={row.id}>
              <Link
                href={`/documents/${row.id}`}
                data-testid="documents-row"
                data-document-id={row.id}
                className="flex flex-col gap-2 rounded-xl border bg-card p-3 outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
              >
                <div className="flex items-start justify-between gap-2">
                  <span className="min-w-0 font-medium break-all">{row.registration_no ?? t("noValue")}</span>
                  <StatusBadge status={row.latest_status} />
                </div>
                <p className="text-sm break-words">{row.title}</p>
                <div className="flex flex-wrap gap-x-3 gap-y-1 text-sm text-muted-foreground">
                  <span className="min-w-0 break-words">{kindLabel(row.kind)}</span>
                  {row.counterparty_name ? <span className="min-w-0 break-words">{row.counterparty_name}</span> : null}
                  <span className="tabular-nums">{formatDay(row.created_at)}</span>
                  {showAmount && row.amount !== null ? (
                    <span className="tabular-nums text-foreground">{formatAmount(row.amount)}</span>
                  ) : null}
                </div>
              </Link>
            </li>
          ))}
        </ul>
      ) : null}

      <nav aria-label={t("pagination")} className="flex items-center justify-between gap-2">
        <Button
          type="button"
          variant="outline"
          size="lg"
          className="h-10"
          disabled={page === 0 || list.isFetching}
          onClick={() => setPage((current) => Math.max(current - 1, 0))}
          data-testid="documents-page-prev"
        >
          <ChevronLeft data-icon="inline-start" aria-hidden />
          {t("prev")}
        </Button>
        <span className="text-sm text-muted-foreground tabular-nums" aria-live="polite">
          {t("page", { page: page + 1 })}
        </span>
        <Button
          type="button"
          variant="outline"
          size="lg"
          className="h-10"
          disabled={!hasNext || list.isFetching}
          onClick={() => setPage((current) => current + 1)}
          data-testid="documents-page-next"
        >
          {t("next")}
          <ChevronRight data-icon="inline-end" aria-hidden />
        </Button>
      </nav>

      {creating ? (
        <CreateDocumentDialog
          projectId={projectId}
          project={project.data}
          kinds={kindsList}
          counterparties={counterparties.data ?? []}
          canCounterparty={counterpartySource}
          canFinancial={permissions.includes("documents:read:financial")}
          onClose={() => setCreating(false)}
        />
      ) : null}
    </main>
  );
}
