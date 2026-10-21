"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ApiError } from "@/lib/auth/api";
import { importMaterials, MATERIALS_KEY } from "@/lib/stock/queries";
import type { ImportIssue, ImportReport } from "@/lib/stock/types";
import { useQueryClient } from "@tanstack/react-query";

import { useStockErrorMessage } from "./use-stock-error";

// Импорт справочника: сначала проверка без записи (dry_run), ошибки - по номерам строк; затем запись.
// Файл, в котором есть хоть одна ошибка, не записывается ничего (всё или ничего).
export function MaterialsImportDialog({
  legalEntityId,
  onClose,
}: {
  legalEntityId: string;
  onClose: () => void;
}) {
  const t = useTranslations("Stock.import");
  const message = useStockErrorMessage();
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [checked, setChecked] = useState<ImportReport | null>(null);
  const [issues, setIssues] = useState<ImportIssue[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function issuesOf(cause: unknown): ImportIssue[] | null {
    if (!(cause instanceof ApiError) || !cause.detail || typeof cause.detail !== "object") return null;
    const list = (cause.detail as { issues?: unknown }).issues;
    return Array.isArray(list) ? (list as ImportIssue[]) : null;
  }

  async function run(dryRun: boolean) {
    if (file === null) return;
    setBusy(true);
    setError(null);
    setIssues([]);
    try {
      const report = await importMaterials(legalEntityId, file, dryRun);
      if (dryRun) {
        setChecked(report);
      } else {
        await queryClient.invalidateQueries({ queryKey: MATERIALS_KEY });
        onClose();
      }
    } catch (cause) {
      const list = issuesOf(cause);
      if (list !== null) {
        setIssues(list);
        setChecked(null);
      } else {
        setError(message(cause));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("title")}</DialogTitle>
          <DialogDescription>{t("hint")}</DialogDescription>
        </DialogHeader>

        <input
          accept=".xlsx,.csv"
          aria-label={t("file")}
          className="block w-full text-sm"
          onChange={(event) => {
            setFile(event.target.files?.[0] ?? null);
            setChecked(null);
            setIssues([]);
            setError(null);
          }}
          type="file"
        />

        {checked !== null && issues.length === 0 && (
          <p className="text-sm">{t("checkedOk", { count: checked.created })}</p>
        )}

        {issues.length > 0 && (
          <div className="space-y-2">
            <p className="text-sm font-medium text-destructive">{t("issuesTitle")}</p>
            <ul className="max-h-64 space-y-1 overflow-y-auto text-sm" data-testid="import-issues">
              {issues.map((issue) => (
                <li key={`${issue.row}-${issue.message}`}>
                  {t("issueRow", { row: issue.row, message: issue.message })}
                </li>
              ))}
            </ul>
          </div>
        )}

        {error !== null && <p className="text-sm text-destructive">{error}</p>}

        <DialogFooter className="gap-2">
          <Button disabled={file === null || busy} onClick={() => run(true)} type="button" variant="outline">
            {t("check")}
          </Button>
          <Button
            disabled={file === null || busy || checked === null || issues.length > 0}
            onClick={() => run(false)}
            type="button"
          >
            {t("import")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
