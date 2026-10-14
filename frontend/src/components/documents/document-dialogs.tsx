"use client";

import { useMutation } from "@tanstack/react-query";
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
import { parseAmount } from "@/lib/documents/format";
import { documentsApi, useInvalidateDocuments } from "@/lib/documents/queries";

// Архивирование меняет статус, строку не удаляет: документ остаётся в реестре, новые версии не принимаются.
export function ArchiveDialog({ documentId, onClose }: { documentId: string; onClose: () => void }) {
  const t = useTranslations("Documents.card");
  const common = useTranslations("Documents.common");
  const message = useDocumentErrorMessage();
  const invalidate = useInvalidateDocuments();
  const archive = useMutation({
    mutationFn: () => documentsApi.archive(documentId),
    onSuccess: async () => {
      await invalidate();
      onClose();
    },
  });
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !archive.isPending) onClose();
      }}
    >
      <DialogContent data-testid="document-archive-dialog">
        <DialogHeader>
          <DialogTitle>{t("archiveTitle")}</DialogTitle>
          <DialogDescription>{t("archiveText")}</DialogDescription>
        </DialogHeader>
        {archive.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {message(archive.error)}
          </p>
        ) : null}
        <DialogFooter>
          <Button type="button" variant="outline" disabled={archive.isPending} onClick={onClose}>
            {common("cancel")}
          </Button>
          <Button
            type="button"
            variant="destructive"
            disabled={archive.isPending}
            onClick={() => archive.mutate()}
            data-testid="document-archive-confirm"
          >
            {archive.isPending ? t("archiveConfirming") : t("archiveConfirm")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// Сумма финансового документа, внесённая после создания (PUT /amount, право documents:read:financial).
export function AmountDialog({
  documentId,
  initial,
  onClose,
}: {
  documentId: string;
  initial: string | null;
  onClose: () => void;
}) {
  const t = useTranslations("Documents.card");
  const common = useTranslations("Documents.common");
  const message = useDocumentErrorMessage();
  const invalidate = useInvalidateDocuments();
  const [text, setText] = useState(initial ?? "");
  const amount = parseAmount(text);
  const invalid = text.trim() !== "" && amount === null;
  const save = useMutation({
    mutationFn: (value: string) => documentsApi.setAmount(documentId, value),
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
      <DialogContent data-testid="document-amount-dialog">
        <DialogHeader>
          <DialogTitle>{t("amountTitle")}</DialogTitle>
          <DialogDescription>{t("amountText")}</DialogDescription>
        </DialogHeader>
        <form
          className="grid gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (amount !== null && !save.isPending) save.mutate(amount);
          }}
        >
          <Field id="document-amount-input" label={t("amountField")}>
            <Input
              id="document-amount-input"
              inputMode="decimal"
              value={text}
              aria-invalid={invalid ? true : undefined}
              onChange={(event) => setText(event.target.value)}
              data-testid="document-amount-input"
            />
            {invalid ? <p className="text-xs text-destructive">{common("amountInvalid")}</p> : null}
          </Field>
          {save.isError ? (
            <p role="alert" className="text-sm text-destructive">
              {message(save.error)}
            </p>
          ) : null}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              {common("cancel")}
            </Button>
            <Button type="submit" disabled={amount === null || save.isPending} data-testid="document-amount-submit">
              {save.isPending ? common("saving") : t("amountSave")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
