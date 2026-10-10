"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";

import { Field } from "@/components/auth/form-parts";
import { FormActions, Modal } from "@/components/settings/dialogs";
import { api } from "@/lib/api";
import type { Actual } from "@/lib/field/types";
import { API } from "@/lib/projects/keys";
import { invalidateProgress } from "@/lib/schedule/queries";

const MAX_REASON = 1000;

// Отклонение требует причины: мастер увидит её рядом с записью и исправит (ТЗ §5.3).
export function RejectDialog({
  fact,
  onClose,
  onDone,
}: {
  fact: Actual;
  onClose: () => void;
  onDone: () => void;
}) {
  const t = useTranslations("Approvals.reject");
  const common = useTranslations("Settings.common");
  const queryClient = useQueryClient();
  const [reason, setReason] = useState("");

  const reject = useMutation({
    mutationFn: () =>
      api.post<Actual>(`${API}/projects/${fact.project_id}/work-actuals/${fact.id}/reject`, {
        reason: reason.trim(),
      }),
    onSuccess: () => {
      // Списки обновятся сами; окно закрывается сразу, не ожидая пересчёта процентов.
      void invalidateProgress(queryClient, fact.project_id);
      onDone();
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (reason.trim() !== "") reject.mutate();
  }

  return (
    <Modal
      title={t("title")}
      description={t("text", { work: fact.work_name ?? "", author: fact.author_name ?? "" })}
      onClose={onClose}
    >
      <form method="post" onSubmit={submit} className="space-y-4">
        <Field id="reject-reason" label={t("reason")} hint={t("hint")}>
          <textarea
            id="reject-reason"
            required
            autoFocus
            rows={4}
            maxLength={MAX_REASON}
            className="w-full min-w-0 rounded-lg border border-input bg-transparent px-2.5 py-2 text-base outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 md:text-sm dark:bg-input/30"
            placeholder={t("placeholder")}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            aria-describedby="reject-reason-hint"
            data-testid="reject-reason-input"
          />
        </Field>
        <FormActions
          pending={reject.isPending}
          submitLabel={t("submit")}
          pendingLabel={common("saving")}
          onCancel={onClose}
          error={reject.error}
          disabled={reason.trim() === ""}
        />
      </form>
    </Modal>
  );
}
