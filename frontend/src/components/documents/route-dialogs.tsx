"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";

import { Field as ReasonField } from "@/components/auth/form-parts";
import { FormActions, Modal } from "@/components/settings/dialogs";
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
import { DOCUMENTS_KEY, documentsApi, versionTransport } from "@/lib/documents/queries";
import {
  isRetryable,
  prepareUpload,
  storeFile,
  UPLOAD_ACCEPT,
  type UploadWork,
} from "@/lib/documents/upload";

// Сервер режет причину по 2000 знаков; здесь та же граница, чтобы не ловить отказ после отправки.
const MAX_REASON = 2000;

// Возврат версии в черновик: причина обязательна (сервер отвечает 422 без неё). Для денежных видов подсказка
// о сумме: причину видят участники и держатели права на деньги, но в тексте не должно быть цифр бюджета.
export function RouteRejectDialog({
  documentId,
  versionId,
  position,
  financial,
  onClose,
}: {
  documentId: string;
  versionId: string;
  position: number;
  financial: boolean;
  onClose: () => void;
}) {
  const t = useTranslations("Documents.route");
  const common = useTranslations("Settings.common");
  const message = useDocumentErrorMessage();
  const queryClient = useQueryClient();
  const [reason, setReason] = useState("");

  const reject = useMutation({
    mutationFn: () =>
      documentsApi.decide(
        documentId,
        versionId,
        { position, decision: "reject", reason: reason.trim() },
        newIdempotencyKey(),
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: DOCUMENTS_KEY });
      onClose();
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (reason.trim() !== "") reject.mutate();
  }

  const hint = financial ? `${t("reasonHint")} ${t("moneyHint")}` : t("reasonHint");

  return (
    <Modal title={t("rejectTitle")} description={t("rejectText")} onClose={onClose}>
      <form method="post" onSubmit={submit} className="space-y-4">
        <ReasonField id="route-reject-reason" label={t("reasonLabel")} hint={hint}>
          <textarea
            id="route-reject-reason"
            required
            autoFocus
            rows={4}
            maxLength={MAX_REASON}
            className="w-full min-w-0 rounded-lg border border-input bg-transparent px-2.5 py-2 text-base outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 md:text-sm dark:bg-input/30"
            placeholder={t("reasonPlaceholder")}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            aria-describedby="route-reject-reason-hint"
            data-testid="route-reject-reason"
          />
        </ReasonField>
        {reject.isError ? (
          <p role="alert" className="text-sm text-destructive" data-testid="route-reject-error">
            {message(reject.error)}
          </p>
        ) : null}
        <FormActions
          pending={reject.isPending}
          submitLabel={t("submitReject")}
          pendingLabel={common("saving")}
          onCancel={onClose}
          error={null}
          disabled={reason.trim() === ""}
        />
      </form>
    </Modal>
  );
}

// Подписанный скан (Q10): файл проходит тот же путь, что версия (presign -> PUT), и сервер создаёт версию
// signed от согласованной. Повтор с тем же файлом и комментарием идёт с тем же ключом идемпотентности.
export function SignedScanDialog({
  documentId,
  versionId,
  onClose,
}: {
  documentId: string;
  versionId: string;
  onClose: () => void;
}) {
  const t = useTranslations("Documents.route");
  const upload = useTranslations("Documents.upload");
  const message = useDocumentErrorMessage();
  const queryClient = useQueryClient();
  const [work, setWork] = useState<UploadWork | null>(null);
  const [pickError, setPickError] = useState<unknown>(null);
  const [checking, setChecking] = useState(false);
  const [comment, setComment] = useState("");

  const save = useMutation({
    mutationFn: async (current: UploadWork) => {
      const key = await storeFile(current, versionTransport(documentId), { comment: null, amount: null }, Date.now);
      return documentsApi.signedScan(
        documentId,
        versionId,
        { key, comment: comment.trim() || null },
        current.idempotencyKey,
      );
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: DOCUMENTS_KEY });
      onClose();
    },
  });

  const busy = checking || save.isPending;
  const locked = busy || save.isError || save.isSuccess;
  const failure = save.error ?? pickError;
  const rejected = save.isError && !isRetryable(save.error);

  async function pick(file: File | undefined) {
    if (!file) return;
    save.reset();
    setPickError(null);
    setWork(null);
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
    if (work === null || busy || save.isSuccess) return;
    save.mutate(work);
  }

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose();
      }}
    >
      <DialogContent data-testid="route-scan-dialog">
        <DialogHeader>
          <DialogTitle>{t("scanTitle")}</DialogTitle>
          <DialogDescription>{t("scanText")}</DialogDescription>
        </DialogHeader>
        <form
          className="grid gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <Field id="route-scan-file" label={upload("file")}>
            <Input
              id="route-scan-file"
              type="file"
              accept={UPLOAD_ACCEPT}
              disabled={busy}
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
                void pick(file);
              }}
              data-testid="route-scan-file"
            />
            <p className="text-xs text-muted-foreground">
              {work ? upload("chosen", { name: work.name }) : checking ? upload("checking") : upload("fileTypes")}
            </p>
          </Field>
          <Field id="route-scan-comment" label={t("scanComment")}>
            <textarea
              id="route-scan-comment"
              value={comment}
              maxLength={500}
              disabled={locked}
              rows={2}
              onChange={(event) => setComment(event.target.value)}
              data-testid="route-scan-comment"
              className="w-full min-w-0 resize-y rounded-lg border border-input bg-transparent px-2.5 py-2 text-base outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 md:text-sm dark:bg-input/30"
            />
          </Field>
          {save.isPending ? (
            <p role="status" className="text-sm text-muted-foreground">
              {upload("saving")}
            </p>
          ) : null}
          {failure ? (
            <p role="alert" className="text-sm text-destructive" data-testid="route-scan-error">
              {message(failure)}
              {rejected ? <span className="mt-1 block text-foreground">{upload("chooseAnother")}</span> : null}
            </p>
          ) : null}
          <DialogFooter>
            <Button type="button" variant="outline" disabled={busy} onClick={onClose} className="h-11">
              {upload("cancel")}
            </Button>
            <Button
              type="submit"
              className="h-11"
              disabled={work === null || busy || save.isSuccess || rejected}
              data-testid="route-scan-submit"
            >
              {t("scanSubmit")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
