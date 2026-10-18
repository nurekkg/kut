"use client";

import { useMutation } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { Field, useDocumentErrorMessage } from "@/components/documents/shared";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { newIdempotencyKey } from "@/lib/api";
import { normalizeQuantity, parseAmount, parseQuantity } from "@/lib/documents/format";
import { documentsApi, useInvalidateDocuments, type LineBody } from "@/lib/documents/queries";
import type { LineDraft, LineOut, PriceDraft } from "@/lib/documents/types";

type Problem = "name" | "unit" | "quantity";

function problemOf(row: LineDraft): Problem | null {
  const name = row.name.trim();
  const unit = row.unit.trim();
  if (name === "" || name.length > 300) return "name";
  if (unit === "" || unit.length > 20) return "unit";
  return parseQuantity(row.quantity) === null ? "quantity" : null;
}

// Список позиций целиком: сервер заменяет его за один запрос (ключ повтора - на открытие диалога).
export function LinesEditDialog({
  documentId,
  versionId,
  versionNo,
  initial,
  onClose,
}: {
  documentId: string;
  versionId: string;
  versionNo: number;
  initial: LineDraft[];
  onClose: () => void;
}) {
  const t = useTranslations("Documents.lines");
  const common = useTranslations("Documents.common");
  const message = useDocumentErrorMessage();
  const invalidate = useInvalidateDocuments();
  const [rows, setRows] = useState<LineDraft[]>(initial);
  const [idempotencyKey] = useState(newIdempotencyKey);
  const problems = rows.map(problemOf);
  const valid = rows.length > 0 && problems.every((problem) => problem === null);
  const save = useMutation({
    mutationFn: () =>
      documentsApi.putLines(
        documentId,
        versionId,
        rows.map(
          (row): LineBody => ({
            name: row.name.trim(),
            unit: row.unit.trim(),
            quantity: normalizeQuantity(row.quantity),
            work_id: null,
            project_node_id: null,
          }),
        ),
        idempotencyKey,
      ),
    onSuccess: async () => {
      await invalidate();
      onClose();
    },
  });
  const patch = (index: number, change: Partial<LineDraft>) =>
    setRows(rows.map((row, position) => (position === index ? { ...row, ...change } : row)));

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !save.isPending) onClose();
      }}
    >
      <DialogContent data-testid="document-lines-dialog" className="max-h-[90dvh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t("editTitle", { number: versionNo })}</DialogTitle>
          <DialogDescription>{t("editText")}</DialogDescription>
        </DialogHeader>
        <form
          className="grid gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (valid && !save.isPending) save.mutate();
          }}
        >
          {rows.length === 0 ? <p className="text-sm text-muted-foreground">{t("noLines")}</p> : null}
          <ul className="grid gap-3" data-testid="document-lines-editor">
            {rows.map((row, index) => {
              const number = index + 1;
              const problem = problems[index];
              return (
                <li
                  key={index}
                  className="grid gap-2 sm:grid-cols-[1fr_6rem_8rem_auto] sm:items-end"
                  data-testid="document-line-row"
                >
                  <Field id={`line-name-${index}`} label={t("lineName", { number })}>
                    <Input
                      id={`line-name-${index}`}
                      value={row.name}
                      aria-invalid={problem === "name" ? true : undefined}
                      onChange={(event) => patch(index, { name: event.target.value })}
                      data-testid="document-line-name"
                    />
                  </Field>
                  <Field id={`line-unit-${index}`} label={t("lineUnit", { number })}>
                    <Input
                      id={`line-unit-${index}`}
                      value={row.unit}
                      aria-invalid={problem === "unit" ? true : undefined}
                      onChange={(event) => patch(index, { unit: event.target.value })}
                      data-testid="document-line-unit"
                    />
                  </Field>
                  <Field id={`line-quantity-${index}`} label={t("lineQuantity", { number })}>
                    <Input
                      id={`line-quantity-${index}`}
                      inputMode="decimal"
                      value={row.quantity}
                      aria-invalid={problem === "quantity" ? true : undefined}
                      onChange={(event) => patch(index, { quantity: event.target.value })}
                      data-testid="document-line-quantity"
                    />
                  </Field>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => setRows(rows.filter((_, position) => position !== index))}
                    data-testid="document-line-remove"
                  >
                    <Trash2 data-icon="inline-start" aria-hidden />
                    {t("removeLine")}
                  </Button>
                  {problem ? (
                    <p className="text-xs text-destructive sm:col-span-4">
                      {problem === "name"
                        ? t("nameInvalid")
                        : problem === "unit"
                          ? t("unitInvalid")
                          : t("quantityInvalid")}
                    </p>
                  ) : null}
                </li>
              );
            })}
          </ul>
          <div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setRows([...rows, { name: "", unit: "", quantity: "" }])}
              data-testid="document-line-add"
            >
              <Plus data-icon="inline-start" aria-hidden />
              {t("addLine")}
            </Button>
          </div>
          {save.isError ? (
            <p role="alert" className="text-sm text-destructive">
              {message(save.error)}
            </p>
          ) : null}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              {common("cancel")}
            </Button>
            <Button type="submit" disabled={!valid || save.isPending} data-testid="document-lines-submit">
              {save.isPending ? common("saving") : t("save")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// Цены по позициям: пустое поле цену не меняет; сервер сохраняет только то, что пришло.
export function LinePricesDialog({
  documentId,
  versionId,
  versionNo,
  lines,
  onClose,
}: {
  documentId: string;
  versionId: string;
  versionNo: number;
  lines: LineOut[];
  onClose: () => void;
}) {
  const t = useTranslations("Documents.lines");
  const common = useTranslations("Documents.common");
  const message = useDocumentErrorMessage();
  const invalidate = useInvalidateDocuments();
  const [texts, setTexts] = useState<string[]>(() => lines.map((line) => line.unit_price ?? ""));
  const [idempotencyKey] = useState(newIdempotencyKey);
  const invalid = texts.some((text) => text.trim() !== "" && parseAmount(text) === null);
  const prices = lines.flatMap((line, index): PriceDraft[] => {
    const price = parseAmount(texts[index]);
    return price === null ? [] : [{ position_no: line.position_no, unit_price: price }];
  });
  const valid = !invalid && prices.length > 0;
  const save = useMutation({
    mutationFn: () => documentsApi.putPrices(documentId, versionId, prices, idempotencyKey),
    onSuccess: async () => {
      await invalidate();
      onClose();
    },
  });

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !save.isPending) onClose();
      }}
    >
      <DialogContent data-testid="document-line-prices-dialog" className="max-h-[90dvh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t("pricesTitle", { number: versionNo })}</DialogTitle>
          <DialogDescription>{t("pricesText")}</DialogDescription>
        </DialogHeader>
        <form
          className="grid gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (valid && !save.isPending) save.mutate();
          }}
        >
          <ul className="grid gap-3" data-testid="document-line-prices">
            {lines.map((line, index) => (
              <li key={line.position_no} className="grid gap-2 sm:grid-cols-[1fr_10rem] sm:items-end">
                <p className="text-sm break-words">
                  {line.position_no}. {line.name}
                </p>
                <Field id={`line-price-${line.position_no}`} label={t("linePrice", { number: line.position_no })}>
                  <Input
                    id={`line-price-${line.position_no}`}
                    inputMode="decimal"
                    value={texts[index]}
                    aria-invalid={texts[index].trim() !== "" && parseAmount(texts[index]) === null ? true : undefined}
                    onChange={(event) =>
                      setTexts(texts.map((text, position) => (position === index ? event.target.value : text)))
                    }
                    data-testid="document-line-price-input"
                  />
                </Field>
              </li>
            ))}
          </ul>
          {save.isError ? (
            <p role="alert" className="text-sm text-destructive">
              {message(save.error)}
            </p>
          ) : null}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              {common("cancel")}
            </Button>
            <Button type="submit" disabled={!valid || save.isPending} data-testid="document-line-prices-submit">
              {save.isPending ? common("saving") : t("save")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
