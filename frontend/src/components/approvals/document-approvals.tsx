"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useState } from "react";

import { RouteRejectDialog } from "@/components/documents/route-dialogs";
import { useDocumentErrorMessage, useKindLabel } from "@/components/documents/shared";
import { Button } from "@/components/ui/button";
import { newIdempotencyKey } from "@/lib/api";
import { DOCUMENTS_KEY, documentsApi, useDocumentApprovals, useDocumentKinds } from "@/lib/documents/queries";
import { isRouteRoleCode } from "@/lib/documents/route";
import type { ApprovalQueueItem } from "@/lib/documents/types";

// Документы в очереди подтверждения: маршрут, где сейчас мой шаг (роль или замещение). Шаг, автора и право
// на деньги проверяет сервер при каждом решении; здесь - те же действия, что на карточке, без перехода на неё.
export function DocumentApprovals() {
  const t = useTranslations("Approvals.documents");
  const roles = useTranslations("Roles");
  const kindLabel = useKindLabel();
  const message = useDocumentErrorMessage();
  const queryClient = useQueryClient();
  const queue = useDocumentApprovals(true);
  const kinds = useDocumentKinds();
  const [rejecting, setRejecting] = useState<ApprovalQueueItem | null>(null);

  const approve = useMutation({
    mutationFn: (item: ApprovalQueueItem) =>
      documentsApi.decide(
        item.document_id,
        item.version_id,
        { position: item.position, decision: "approve" },
        newIdempotencyKey(),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: DOCUMENTS_KEY }),
  });

  const financialOf = (code: string) => kinds.data?.find((kind) => kind.code === code)?.financial ?? false;

  return (
    <section className="space-y-4" aria-labelledby="document-approvals-title" data-testid="document-approvals">
      <div className="space-y-1">
        <h2 id="document-approvals-title" className="text-xl font-semibold tracking-tight">
          {t("title")}
        </h2>
        <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
      </div>

      {queue.isPending ? <p className="text-sm text-muted-foreground">…</p> : null}
      {queue.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {message(queue.error)}
        </p>
      ) : null}
      {queue.isSuccess && queue.data.length === 0 ? (
        <p
          className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground"
          data-testid="document-approvals-empty"
        >
          {t("empty")}
        </p>
      ) : null}
      {queue.isSuccess && queue.data.length > 0 ? (
        <ul className="space-y-3" data-testid="document-approval-list">
          {queue.data.map((item) => (
            <li key={item.version_id} className="space-y-3 rounded-xl border p-4" data-testid="document-approval-row">
              <div className="min-w-0 space-y-1">
                <Link
                  href={`/documents/${item.document_id}`}
                  className="break-words font-medium underline-offset-4 hover:underline"
                  data-testid="document-approval-title"
                >
                  {item.document_title}
                </Link>
                <p className="break-words text-sm text-muted-foreground">
                  {[kindLabel(item.kind), item.project_name, t("version", { number: item.version_no })]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
                <p className="text-sm" data-testid="document-approval-step">
                  {t("step", { role: isRouteRoleCode(item.role_code) ? roles(item.role_code) : item.role_code })}
                </p>
              </div>
              {approve.isError && approve.variables?.version_id === item.version_id ? (
                <p role="alert" className="text-sm text-destructive">
                  {message(approve.error)}
                </p>
              ) : null}
              <div className="grid grid-cols-2 gap-2">
                <Button
                  type="button"
                  className="h-12"
                  disabled={approve.isPending}
                  onClick={() => approve.mutate(item)}
                  data-testid="document-approve"
                >
                  {t("approve")}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  className="h-12"
                  disabled={approve.isPending}
                  onClick={() => setRejecting(item)}
                  data-testid="document-reject"
                >
                  {t("reject")}
                </Button>
              </div>
            </li>
          ))}
        </ul>
      ) : null}

      {rejecting ? (
        <RouteRejectDialog
          documentId={rejecting.document_id}
          versionId={rejecting.version_id}
          position={rejecting.position}
          financial={financialOf(rejecting.kind)}
          onClose={() => setRejecting(null)}
        />
      ) : null}
    </section>
  );
}
