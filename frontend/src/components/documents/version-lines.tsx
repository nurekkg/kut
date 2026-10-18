"use client";

import { useMutation } from "@tanstack/react-query";
import { Download, Pencil, Upload } from "lucide-react";
import { useTranslations } from "next-intl";
import { useRef, useState } from "react";

import { LinePricesDialog, LinesEditDialog } from "@/components/documents/lines-dialogs";
import { useDocumentErrorMessage } from "@/components/documents/shared";
import { Button } from "@/components/ui/button";
import { newIdempotencyKey } from "@/lib/api";
import { canSeeLinePrices, canWriteLines, canWritePrices } from "@/lib/documents/access";
import { formatMoney, formatQuantity } from "@/lib/documents/format";
import {
  DOCUMENTS_API,
  documentsApi,
  useInvalidateDocuments,
  useVersionLines,
} from "@/lib/documents/queries";
import type { LineOut, VersionOut } from "@/lib/documents/types";
import { fetchBlob, saveBlob } from "@/lib/equipment/download";

// Позиции версии: таблица на широком экране, карточки на телефоне (375 px без горизонтальной прокрутки).
// Цена и сумма - только там, где их отдала БД: без них колонок и строки «Итого» в DOM нет.
// Изменение - только у черновика и с правом documents:write:lines; цены - ещё и с правом на деньги вида.
export function VersionLines({
  documentId,
  kind,
  archivedAt,
  version,
  permissions,
  internal,
}: {
  documentId: string;
  kind: string;
  archivedAt: string | null;
  version: Pick<VersionOut, "id" | "version_no" | "status">;
  permissions: readonly string[];
  internal: boolean;
}) {
  const t = useTranslations("Documents.lines");
  const common = useTranslations("Documents.common");
  const message = useDocumentErrorMessage();
  const invalidate = useInvalidateDocuments();
  const lines = useVersionLines(documentId, version.id, true);
  const [editing, setEditing] = useState(false);
  const [pricing, setPricing] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const doc = { kind, archived_at: archivedAt };
  const data = lines.data;
  const showPrices = data !== undefined && canSeeLinePrices(kind, data.lines, permissions, internal);
  const writable = canWriteLines(doc, version, permissions, internal);
  const priceable = canWritePrices(doc, version, permissions, internal);
  const importLines = useMutation({
    mutationFn: (file: File) => documentsApi.importLines(documentId, version.id, file, newIdempotencyKey()),
    onSuccess: () => invalidate(),
  });
  const template = useMutation({
    mutationFn: () => fetchBlob(`${DOCUMENTS_API}/lines/template.xlsx`),
    onSuccess: (blob) => saveBlob(blob, "document-lines-template.xlsx"),
  });

  return (
    <section aria-labelledby={`lines-title-${version.id}`} className="grid gap-3" data-testid="document-lines">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id={`lines-title-${version.id}`} className="text-sm font-medium">
          {t("title")}
          {data ? (
            <span className="ml-2 font-normal text-muted-foreground tabular-nums">
              {t("count", { count: data.lines.length })}
            </span>
          ) : null}
        </h3>
        <div className="flex flex-wrap gap-2">
          {writable ? (
            <>
              <Button type="button" variant="outline" size="sm" onClick={() => setEditing(true)} disabled={!data} data-testid="document-lines-edit">
                <Pencil data-icon="inline-start" aria-hidden />
                {t("edit")}
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={importLines.isPending}
                onClick={() => fileInput.current?.click()}
                data-testid="document-lines-import"
              >
                <Upload data-icon="inline-start" aria-hidden />
                {importLines.isPending ? t("importing") : t("importXlsx")}
              </Button>
              <input
                ref={fileInput}
                type="file"
                accept=".xlsx"
                className="hidden"
                data-testid="document-lines-file"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  event.target.value = "";
                  if (file) importLines.mutate(file);
                }}
              />
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={template.isPending}
                onClick={() => template.mutate()}
                data-testid="document-lines-template"
              >
                <Download data-icon="inline-start" aria-hidden />
                {t("template")}
              </Button>
            </>
          ) : null}
          {priceable && data ? (
            <Button type="button" variant="outline" size="sm" onClick={() => setPricing(true)} data-testid="document-lines-prices">
              <Pencil data-icon="inline-start" aria-hidden />
              {t("prices")}
            </Button>
          ) : null}
        </div>
      </div>

      {lines.isPending ? <p className="text-sm text-muted-foreground">{common("loading")}</p> : null}
      {lines.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {message(lines.error)}
        </p>
      ) : null}
      {importLines.isError || template.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {message(importLines.error ?? template.error)}
        </p>
      ) : null}

      {data && data.lines.length === 0 ? <p className="text-sm text-muted-foreground">{t("empty")}</p> : null}
      {data && data.lines.length > 0 ? (
        <>
          <ul className="flex flex-col divide-y rounded-lg border sm:hidden" data-testid="document-lines-cards">
            {data.lines.map((line) => (
              <li key={line.position_no} className="grid gap-1 p-3 text-sm" data-testid="document-line-card">
                <p className="font-medium break-words">
                  {line.position_no}. {line.name}
                </p>
                <p className="tabular-nums text-muted-foreground">
                  {line.unit} · {formatQuantity(line.quantity)}
                </p>
                {showPrices ? <PriceCells line={line} /> : null}
              </li>
            ))}
          </ul>
          <table className="hidden w-full text-sm sm:table" data-testid="document-lines-table">
            <thead className="text-left text-muted-foreground">
              <tr className="border-b">
                <th className="py-2 pr-3 font-normal">{t("columnNo")}</th>
                <th className="py-2 pr-3 font-normal">{t("columnName")}</th>
                <th className="py-2 pr-3 font-normal">{t("columnUnit")}</th>
                <th className="py-2 pr-3 text-right font-normal">{t("columnQuantity")}</th>
                {showPrices ? (
                  <>
                    <th className="py-2 pr-3 text-right font-normal">{t("columnPrice")}</th>
                    <th className="py-2 text-right font-normal">{t("columnAmount")}</th>
                  </>
                ) : null}
              </tr>
            </thead>
            <tbody>
              {data.lines.map((line) => (
                <tr key={line.position_no} className="border-b align-top" data-testid="document-line-row-cell">
                  <td className="py-2 pr-3 tabular-nums">{line.position_no}</td>
                  <td className="py-2 pr-3 break-words">{line.name}</td>
                  <td className="py-2 pr-3">{line.unit}</td>
                  <td className="py-2 pr-3 text-right tabular-nums">{formatQuantity(line.quantity)}</td>
                  {showPrices ? (
                    <>
                      <td className="py-2 pr-3 text-right tabular-nums">
                        {line.unit_price !== null ? formatMoney(line.unit_price) : common("noValue")}
                      </td>
                      <td className="py-2 text-right tabular-nums">
                        {line.amount !== null ? formatMoney(line.amount) : common("noValue")}
                      </td>
                    </>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
          {showPrices && data.total !== null ? (
            <p className="text-sm font-medium tabular-nums" data-testid="document-lines-total">
              {t("total")}: {formatMoney(data.total)}
            </p>
          ) : null}
        </>
      ) : null}

      {editing && data ? (
        <LinesEditDialog
          documentId={documentId}
          versionId={version.id}
          versionNo={version.version_no}
          initial={data.lines.map((line) => ({
            name: line.name,
            unit: line.unit,
            quantity: formatQuantity(line.quantity),
          }))}
          onClose={() => setEditing(false)}
        />
      ) : null}
      {pricing && data ? (
        <LinePricesDialog
          documentId={documentId}
          versionId={version.id}
          versionNo={version.version_no}
          lines={data.lines}
          onClose={() => setPricing(false)}
        />
      ) : null}
    </section>
  );
}

function PriceCells({ line }: { line: LineOut }) {
  const t = useTranslations("Documents.lines");
  const common = useTranslations("Documents.common");
  return (
    <p className="tabular-nums">
      <span className="text-muted-foreground">{t("columnPrice")}: </span>
      {line.unit_price !== null ? formatMoney(line.unit_price) : common("noValue")}
      <span className="text-muted-foreground"> · {t("columnAmount")}: </span>
      {line.amount !== null ? formatMoney(line.amount) : common("noValue")}
    </p>
  );
}
