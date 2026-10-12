"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { apiFetch } from "@/lib/auth/session";
import { newIdempotencyKey } from "@/lib/api";
import { useEquipmentErrorMessage } from "@/lib/equipment/errors";
import { fetchBlob, saveBlob } from "@/lib/equipment/download";
import { batchesKey, EQUIPMENT_KEY } from "@/lib/equipment/keys";
import { EQUIPMENT_API, useImportBatches } from "@/lib/equipment/queries";
import type { ImportReport } from "@/lib/equipment/types";
import { formatShortDate } from "@/lib/schedule/dates";

// Импорт реестра, всё или ничего: сначала проверка (ничего не пишется, отчёт по строкам),
// затем загрузка - только если проверка без ошибок. Повтор того же файла ничего не меняет.
export function ImportView() {
  const t = useTranslations("Equipment.import");
  const message = useEquipmentErrorMessage();
  const client = useQueryClient();
  const batches = useImportBatches();
  const [file, setFile] = useState<File | null>(null);
  const [report, setReport] = useState<ImportReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const [loaded, setLoaded] = useState<ImportReport | null>(null);

  const download = async (format: "xlsx" | "csv") => {
    setFailure(null);
    try {
      const blob = await fetchBlob(`${EQUIPMENT_API}/import/template?format=${format}`);
      saveBlob(blob, `equipment-import.${format}`);
    } catch (error) {
      setFailure(error);
    }
  };

  const send = async (commit: boolean): Promise<ImportReport | null> => {
    if (!file) return null;
    setBusy(true);
    setFailure(null);
    try {
      const body = await file.arrayBuffer();
      const path = `${EQUIPMENT_API}/import${commit ? "" : "/check"}?filename=${encodeURIComponent(file.name)}`;
      const response = await apiFetch<ImportReport>(path, {
        method: "POST",
        headers: {
          "Content-Type": file.type || "application/octet-stream",
          ...(commit ? { "Idempotency-Key": newIdempotencyKey() } : {}),
        },
        body,
      });
      return response;
    } catch (error) {
      setFailure(error);
      return null;
    } finally {
      setBusy(false);
    }
  };

  const check = async () => {
    const result = await send(false);
    setLoaded(null);
    setReport(result);
    await client.invalidateQueries({ queryKey: batchesKey });
  };

  const commit = async () => {
    const result = await send(true);
    if (result) {
      setLoaded(result);
      setReport(null);
      setFile(null);
      await client.invalidateQueries({ queryKey: EQUIPMENT_KEY });
    }
  };

  const downloadErrors = async (batchId: string) => {
    try {
      const blob = await fetchBlob(`${EQUIPMENT_API}/import/batches/${batchId}/errors.csv`);
      saveBlob(blob, "equipment-import-errors.csv");
    } catch (error) {
      setFailure(error);
    }
  };

  const rejected = report?.status === "rejected";
  const checked = report?.status === "checked";

  return (
    <section className="flex flex-col gap-5" aria-labelledby="import-title">
      <div className="flex flex-col gap-1">
        <h1 id="import-title" className="text-xl font-semibold tracking-tight">
          {t("title")}
        </h1>
        <p className="text-sm text-muted-foreground">{t("intro")}</p>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div className="flex flex-col gap-3 rounded-xl border bg-card p-4">
          <h2 className="text-base font-semibold">{t("step1")}</h2>
          <p className="text-sm text-muted-foreground">{t("templateHint")}</p>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={() => void download("xlsx")} data-testid="download-xlsx">
              {t("downloadXlsx")}
            </Button>
            <Button variant="outline" onClick={() => void download("csv")} data-testid="download-csv">
              {t("downloadCsv")}
            </Button>
          </div>
        </div>

        <div className="flex flex-col gap-3 rounded-xl border bg-card p-4">
          <h2 className="text-base font-semibold">{t("step2")}</h2>
          <label htmlFor="import-file" className="text-sm text-muted-foreground">
            {t("fileHint")}
          </label>
          <Input
            id="import-file"
            type="file"
            accept=".xlsx,.csv"
            data-testid="import-file"
            onChange={(event) => {
              setFile(event.target.files?.[0] ?? null);
              setReport(null);
              setLoaded(null);
            }}
          />
          <Button
            onClick={() => void check()}
            disabled={!file || busy}
            className="w-full sm:w-auto"
            data-testid="import-check"
          >
            {busy ? t("working") : t("check")}
          </Button>
        </div>
      </div>

      {failure ? (
        <p role="alert" className="text-sm text-destructive" data-testid="import-error">
          {message(failure)}
        </p>
      ) : null}

      {rejected ? (
        <div className="flex flex-col gap-3 rounded-xl border border-destructive/40 bg-destructive/5 p-4" data-testid="import-report">
          <h2 className="text-base font-semibold text-destructive">
            {t("rejectedTitle", { count: report.errors.length })}
          </h2>
          <p className="text-sm">{t("rejectedHint")}</p>
          <div className="overflow-x-auto rounded-lg border bg-background">
            <table className="w-full min-w-[32rem] text-sm">
              <thead>
                <tr className="border-b text-left text-xs text-muted-foreground">
                  <th scope="col" className="px-3 py-2 font-medium">{t("colRow")}</th>
                  <th scope="col" className="px-3 py-2 font-medium">{t("colColumn")}</th>
                  <th scope="col" className="px-3 py-2 font-medium">{t("colReason")}</th>
                </tr>
              </thead>
              <tbody>
                {report.errors.map((issue, index) => (
                  <tr key={`${issue.row}-${issue.code}-${index}`} className="border-b last:border-0" data-testid="import-issue">
                    <td className="px-3 py-2 tabular-nums">{issue.row}</td>
                    <td className="px-3 py-2">{issue.column || "—"}</td>
                    <td className="px-3 py-2">{issue.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {report.batch_id ? (
            <Button
              variant="outline"
              className="w-full sm:w-auto"
              onClick={() => void downloadErrors(report.batch_id as string)}
              data-testid="download-errors"
            >
              {t("downloadErrors")}
            </Button>
          ) : null}
        </div>
      ) : null}

      {checked ? (
        <div className="flex flex-col gap-3 rounded-xl border border-emerald-600/40 bg-emerald-600/5 p-4" data-testid="import-ready">
          <h2 className="text-base font-semibold">{t("readyTitle")}</h2>
          <p className="text-sm tabular-nums" data-testid="import-counts">
            {t("counts", {
              rows: report.rows_total,
              created: report.created,
              updated: report.updated,
              unchanged: report.unchanged,
            })}
          </p>
          <Button className="w-full sm:w-auto" disabled={busy} onClick={() => void commit()} data-testid="import-commit">
            {busy ? t("working") : t("commit")}
          </Button>
        </div>
      ) : null}

      {loaded ? (
        <p role="status" className="rounded-xl border p-4 text-sm tabular-nums" data-testid="import-loaded">
          {t("loaded", {
            created: loaded.created,
            updated: loaded.updated,
            unchanged: loaded.unchanged,
          })}
        </p>
      ) : null}

      <div className="flex flex-col gap-2">
        <h2 className="text-base font-semibold">{t("historyTitle")}</h2>
        {batches.isSuccess && batches.data.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("historyEmpty")}</p>
        ) : null}
        {batches.isSuccess && batches.data.length > 0 ? (
          <ul className="flex flex-col divide-y rounded-lg border" data-testid="batch-list">
            {batches.data.map((batch) => (
              <li key={batch.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 p-3 text-sm">
                <span className="tabular-nums">{formatShortDate(batch.created_at.slice(0, 10))}</span>
                <span className="font-medium">{t(`batchStatus.${batch.status}`)}</span>
                <span className="tabular-nums text-muted-foreground">
                  {t("historyCounts", {
                    created: batch.created_count,
                    updated: batch.updated_count,
                    errors: batch.error_count,
                  })}
                </span>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </section>
  );
}
