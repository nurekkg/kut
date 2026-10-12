"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useMemo, useState } from "react";

import { RequestStatusBadge } from "@/components/equipment/badges";
import { AssignDialog, RejectDialog, RequestDialog } from "@/components/equipment/request-dialogs";
import { Button } from "@/components/ui/button";
import { NativeSelect } from "@/components/ui/native-select";
import { api } from "@/lib/api";
import { useEquipmentAccess } from "@/lib/equipment/access";
import { useEquipmentErrorMessage } from "@/lib/equipment/errors";
import { EQUIPMENT_KEY } from "@/lib/equipment/keys";
import { EQUIPMENT_API, useEquipmentTypes, useRequests } from "@/lib/equipment/queries";
import { REQUEST_STATUSES, type RequestRow } from "@/lib/equipment/types";
import { useNodeLabels } from "@/lib/projects/labels";
import { formatShortDate } from "@/lib/schedule/dates";

type Action = "submit" | "cancel" | "reopen";

// Заявки: прораб подаёт и отзывает свои, начальник отдела назначает или отклоняет. Список - по
// видимости RLS: прораб видит заявки своих объектов.
export function RequestsView() {
  const t = useTranslations("Equipment.requests");
  const statusText = useTranslations("Equipment.requestStatus");
  const access = useEquipmentAccess();
  const [status, setStatus] = useState("");
  const requests = useRequests({ status: status || undefined });
  const types = useEquipmentTypes();
  const rows = useMemo(() => requests.data ?? [], [requests.data]);
  const projectIds = useMemo(() => [...new Set(rows.map((row) => row.project_id))], [rows]);
  const labels = useNodeLabels(access.canObjects ? projectIds : []);
  const [creating, setCreating] = useState(false);
  const [assigning, setAssigning] = useState<RequestRow | null>(null);
  const [rejecting, setRejecting] = useState<RequestRow | null>(null);
  const typeName = (id: string) => (types.data ?? []).find((item) => item.id === id)?.name ?? "—";

  return (
    <section className="flex flex-col gap-4" aria-labelledby="requests-title">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 id="requests-title" className="text-xl font-semibold tracking-tight">
            {t("title")}
          </h1>
          <p className="text-sm text-muted-foreground">{t(access.canAssign ? "hintAssign" : "hintRequest")}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <NativeSelect
            aria-label={t("statusLabel")}
            className="w-auto min-w-40"
            value={status}
            onChange={(event) => setStatus(event.target.value)}
            data-testid="requests-status"
          >
            <option value="">{t("allStatuses")}</option>
            {REQUEST_STATUSES.map((value) => (
              <option key={value} value={value}>
                {statusText(value)}
              </option>
            ))}
          </NativeSelect>
          {access.canRequest ? (
            <Button onClick={() => setCreating(true)} data-testid="new-request">
              {t("newRequest")}
            </Button>
          ) : null}
        </div>
      </div>

      {requests.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {t("loadFailed")}
        </p>
      ) : null}

      {requests.isSuccess && rows.length === 0 ? (
        <p className="rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">
          {t("empty")}
        </p>
      ) : null}

      <ul className="flex flex-col gap-3" data-testid="request-list">
        {rows.map((row) => (
          <li key={row.id} className="flex flex-col gap-3 rounded-xl border bg-card p-4" data-status={row.status} data-request-id={row.id}>
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="font-medium">{row.project_name ?? "—"}</p>
                <p className="text-sm text-muted-foreground">
                  {row.node_id ? (labels.get(row.node_id) ?? "—") : t("noNode")}
                </p>
              </div>
              <RequestStatusBadge status={row.status} />
            </div>
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
              <span>{typeName(row.equipment_type_id)}</span>
              <span className="tabular-nums">
                {formatShortDate(row.date_from)} — {formatShortDate(row.date_to)}
              </span>
              <span className="tabular-nums">{t("hoursPerDay", { hours: row.planned_hours_per_day })}</span>
            </div>
            {row.reject_reason ? (
              <p className="text-sm text-destructive">{t("rejectedBecause", { reason: row.reject_reason })}</p>
            ) : null}
            <RequestActions
              row={row}
              canRequest={access.canRequest}
              canAssign={access.canAssign}
              onAssign={() => setAssigning(row)}
              onReject={() => setRejecting(row)}
            />
          </li>
        ))}
      </ul>

      {creating ? <RequestDialog onClose={() => setCreating(false)} /> : null}
      {assigning ? (
        <AssignDialog
          request={assigning}
          projectName={assigning.project_name ?? "—"}
          nodeLabel={assigning.node_id ? (labels.get(assigning.node_id) ?? "—") : t("noNode")}
          onClose={() => setAssigning(null)}
        />
      ) : null}
      {rejecting ? <RejectDialog request={rejecting} onClose={() => setRejecting(null)} /> : null}
    </section>
  );
}

function RequestActions({
  row,
  canRequest,
  canAssign,
  onAssign,
  onReject,
}: {
  row: RequestRow;
  canRequest: boolean;
  canAssign: boolean;
  onAssign: () => void;
  onReject: () => void;
}) {
  const t = useTranslations("Equipment.requests");
  const message = useEquipmentErrorMessage();
  const client = useQueryClient();
  const act = useMutation({
    mutationFn: (action: Action) => api.post(`${EQUIPMENT_API}/requests/${row.id}/${action}`),
    onSuccess: () => client.invalidateQueries({ queryKey: EQUIPMENT_KEY }),
  });
  const buttons: { action: Action; label: string }[] = [];
  if (canRequest && row.status === "draft") {
    buttons.push({ action: "submit", label: t("submit") }, { action: "cancel", label: t("cancel") });
  }
  if (canRequest && row.status === "rejected") buttons.push({ action: "reopen", label: t("reopen") });
  if (canRequest && row.status === "submitted") buttons.push({ action: "cancel", label: t("cancel") });
  return (
    <div className="flex flex-col gap-2">
      {act.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {message(act.error)}
        </p>
      ) : null}
      {buttons.length > 0 || (canAssign && row.status === "submitted") ? (
        <div className="flex flex-wrap gap-2">
          {canAssign && row.status === "submitted" ? (
            <>
              <Button size="sm" onClick={onAssign} data-testid="assign-request">
                {t("assign")}
              </Button>
              <Button size="sm" variant="outline" onClick={onReject} data-testid="reject-request">
                {t("reject")}
              </Button>
            </>
          ) : null}
          {buttons.map((button) => (
            <Button
              key={button.action}
              size="sm"
              variant={button.action === "cancel" ? "outline" : "secondary"}
              disabled={act.isPending}
              onClick={() => act.mutate(button.action)}
              data-testid={`request-${button.action}`}
            >
              {button.label}
            </Button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
