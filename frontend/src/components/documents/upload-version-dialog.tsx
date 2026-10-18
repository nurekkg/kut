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
import { newIdempotencyKey } from "@/lib/api";
import { parseAmount } from "@/lib/documents/format";
import { useInvalidateDocuments, versionTransport } from "@/lib/documents/queries";
import {
  isRetryable,
  prepareUpload,
  UPLOAD_ACCEPT,
  uploadVersion,
  type UploadWork,
} from "@/lib/documents/upload";

// Загрузка версии: файл проверяется и хэшируется при выборе, затем presign -> S3 -> confirm. Комментарий и
// сумма фиксируются с первой попытки: сервер связывает ключ идемпотентности с телом запроса, и повтор с другим
// телом он отвергает. Другой файл - новая рабочая копия, со своим ключом. Сумма принадлежит версии (R1) и
// нужна только там, где пользователь вправе её видеть (canAmount, сервер проверит то же).
export function UploadVersionDialog({
  documentId,
  canAmount,
  onClose,
}: {
  documentId: string;
  canAmount: boolean;
  onClose: () => void;
}) {
  const t = useTranslations("Documents.upload");
  const common = useTranslations("Documents.common");
  const message = useDocumentErrorMessage();
  const invalidate = useInvalidateDocuments();
  const [work, setWork] = useState<UploadWork | null>(null);
  const [pickError, setPickError] = useState<unknown>(null);
  const [checking, setChecking] = useState(false);
  const [comment, setComment] = useState("");
  const [amountText, setAmountText] = useState("");
  const [percent, setPercent] = useState<number | null>(null);
  const amountFilled = canAmount && amountText.trim() !== "";
  const amount = amountFilled ? parseAmount(amountText) : null;
  const amountInvalid = amountFilled && amount === null;

  const save = useMutation({
    mutationFn: ({ current, text, sum }: { current: UploadWork; text: string | null; sum: string | null }) =>
      uploadVersion(current, versionTransport(documentId), {
        comment: text,
        amount: sum,
        onProgress: (loaded, total) => setPercent(total > 0 ? Math.round((loaded / total) * 100) : 0),
      }),
    onSuccess: async () => {
      await invalidate();
      onClose();
    },
  });

  const busy = checking || save.isPending;
  const locked = busy || save.isError || save.isSuccess;
  const failure = save.error ?? pickError;
  // Отказ по содержимому файла повтором не лечится: предлагаем другой файл, а не ещё одну попытку.
  const rejected = save.isError && !isRetryable(save.error);

  async function pick(file: File | undefined) {
    if (!file) return;
    save.reset();
    setPickError(null);
    setWork(null);
    setPercent(null);
    setChecking(true);
    try {
      setWork(await prepareUpload(file, newIdempotencyKey()));
    } catch (error) {
      setPickError(error);
    } finally {
      setChecking(false);
    }
  }

  function submit() {
    if (work === null || busy || save.isSuccess || amountInvalid) return;
    setPercent(null);
    save.mutate({ current: work, text: comment.trim() || null, sum: amount });
  }

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose();
      }}
    >
      <DialogContent data-testid="document-upload-dialog">
        <DialogHeader>
          <DialogTitle>{t("title")}</DialogTitle>
          <DialogDescription>{t("description")}</DialogDescription>
        </DialogHeader>
        <form
          className="grid gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <Field id="upload-file" label={t("file")}>
            <Input
              id="upload-file"
              type="file"
              accept={UPLOAD_ACCEPT}
              disabled={busy}
              onChange={(event) => {
                const file = event.target.files?.[0];
                // Сброс поля нужен, чтобы тот же файл можно было выбрать снова (после отказа).
                event.target.value = "";
                void pick(file);
              }}
              data-testid="document-upload-file"
            />
            <p className="text-xs text-muted-foreground">
              {work ? t("chosen", { name: work.name }) : checking ? t("checking") : t("fileTypes")}
            </p>
          </Field>
          {canAmount ? (
            <Field id="upload-amount" label={t("amount")}>
              <Input
                id="upload-amount"
                inputMode="decimal"
                value={amountText}
                disabled={locked}
                aria-invalid={amountInvalid ? true : undefined}
                onChange={(event) => setAmountText(event.target.value)}
                data-testid="document-upload-amount"
              />
              <p className={amountInvalid ? "text-xs text-destructive" : "text-xs text-muted-foreground"}>
                {amountInvalid ? common("amountInvalid") : t("amountHint")}
              </p>
            </Field>
          ) : null}
          <Field id="upload-comment" label={t("comment")}>
            <textarea
              id="upload-comment"
              value={comment}
              maxLength={2000}
              disabled={locked}
              rows={3}
              onChange={(event) => setComment(event.target.value)}
              data-testid="document-upload-comment"
              className="w-full min-w-0 resize-y rounded-lg border border-input bg-transparent px-2.5 py-2 text-base outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 md:text-sm dark:bg-input/30"
            />
            <p className="text-xs text-muted-foreground">{t("commentHint")}</p>
          </Field>
          {save.isPending ? (
            <p role="status" className="text-sm text-muted-foreground">
              {percent === null ? t("saving") : t("uploading", { percent })}
            </p>
          ) : null}
          {failure ? (
            <p role="alert" className="text-sm text-destructive" data-testid="document-upload-error">
              {message(failure)}
              {rejected ? <span className="mt-1 block text-foreground">{t("chooseAnother")}</span> : null}
            </p>
          ) : null}
          <DialogFooter>
            <Button type="button" variant="outline" disabled={busy} onClick={onClose}>
              {t("cancel")}
            </Button>
            <Button
              type="submit"
              disabled={work === null || busy || save.isSuccess || rejected || amountInvalid}
              data-testid="document-upload-submit"
            >
              {save.isError && !rejected ? t("retry") : t("submit")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
