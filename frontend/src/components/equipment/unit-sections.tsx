"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useState } from "react";

import { DocumentStateBadge, MaintenanceKindBadge } from "@/components/equipment/badges";
import { DocumentDialog, MaintenanceDialog, OwnRateDialog } from "@/components/equipment/equipment-dialogs";
import { Section } from "@/components/equipment/section";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { type EquipmentAccess, useEquipmentAccess } from "@/lib/equipment/access";
import { useEquipmentErrorMessage } from "@/lib/equipment/errors";
import { EQUIPMENT_KEY } from "@/lib/equipment/keys";
import { EQUIPMENT_API, useOwnRates } from "@/lib/equipment/queries";
import { documentState, latestDocuments, maintenanceBucket } from "@/lib/equipment/status";
import type { MaintenanceRow, MaintenanceStatus, UnitCard } from "@/lib/equipment/types";
import { formatShortDate, todayIso } from "@/lib/schedule/dates";

// Переходы ТО и ремонта по статусам; сервер проверяет их ещё раз (equipment.transition_forbidden).
const NEXT: Record<string, { status: MaintenanceStatus; key: "start" | "finish" | "cancel" }[]> = {
  planned: [
    { status: "in_progress", key: "start" },
    { status: "cancelled", key: "cancel" },
  ],
  in_progress: [
    { status: "done", key: "finish" },
    { status: "cancelled", key: "cancel" },
  ],
};

export function useMaintenanceTransition() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, status }: { id: string; status: MaintenanceStatus }) =>
      api.patch(`${EQUIPMENT_API}/maintenance/${id}`, { status }),
    onSuccess: () => client.invalidateQueries({ queryKey: EQUIPMENT_KEY }),
  });
}

export function MaintenanceList({
  rows,
  access,
  unitLabel,
  today,
}: {
  rows: MaintenanceRow[];
  access: EquipmentAccess;
  unitLabel?: (unitId: string) => string;
  today: string;
}) {
  const t = useTranslations("Equipment.maintenance");
  const message = useEquipmentErrorMessage();
  const transition = useMaintenanceTransition();
  const sorted = [...rows].sort((a, b) => b.planned_from.localeCompare(a.planned_from));
  return (
    <>
      {transition.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {message(transition.error)}
        </p>
      ) : null}
      {sorted.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("empty")}</p>
      ) : (
        <ul className="flex flex-col divide-y rounded-lg border" data-testid="maintenance-list">
          {sorted.map((row) => {
            const bucket = maintenanceBucket(row, today);
            const actions = access.canMaintenance ? (NEXT[row.status] ?? []) : [];
            return (
              <li key={row.id} className="flex flex-col gap-2 p-3" data-status={row.status}>
                <div className="flex flex-wrap items-center gap-2">
                  <MaintenanceKindBadge item={row} />
                  <span className="min-w-0 flex-1 font-medium">{row.title}</span>
                  <span className="text-xs text-muted-foreground" data-bucket={bucket}>
                    {t(`status.${row.status}`)}
                  </span>
                </div>
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
                  {unitLabel ? <span className="font-medium text-foreground">{unitLabel(row.equipment_unit_id)}</span> : null}
                  <span className="tabular-nums">
                    {formatShortDate(row.planned_from)}
                    {row.planned_to ? ` — ${formatShortDate(row.planned_to)}` : ""}
                  </span>
                  {row.done_at ? <span>{t("doneAt", { date: formatShortDate(row.done_at.slice(0, 10)) })}</span> : null}
                </div>
                {actions.length > 0 ? (
                  <div className="flex flex-wrap gap-2">
                    {actions.map((action) => (
                      <Button
                        key={action.status}
                        size="sm"
                        variant={action.status === "cancelled" ? "outline" : "secondary"}
                        disabled={transition.isPending}
                        onClick={() => transition.mutate({ id: row.id, status: action.status })}
                        data-testid={`maintenance-${action.key}`}
                      >
                        {t(`action.${action.key}`)}
                      </Button>
                    ))}
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}

export function UnitMaintenanceSection({ unit }: { unit: UnitCard }) {
  const t = useTranslations("Equipment.unit");
  const access = useEquipmentAccess();
  const [today] = useState(todayIso);
  const [adding, setAdding] = useState(false);
  return (
    <Section
      id="unit-maintenance"
      title={t("maintenanceTitle")}
      action={
        access.canMaintenance ? (
          <Button size="sm" variant="outline" onClick={() => setAdding(true)} data-testid="add-maintenance">
            {t("addMaintenance")}
          </Button>
        ) : null
      }
    >
      <MaintenanceList rows={unit.maintenance} access={access} today={today} />
      {adding ? (
        <MaintenanceDialog units={[]} unitId={unit.id} onClose={() => setAdding(false)} />
      ) : null}
    </Section>
  );
}

export function UnitDocumentsSection({ unit }: { unit: UnitCard }) {
  const t = useTranslations("Equipment.unit");
  const kindText = useTranslations("Equipment.documentKind");
  const access = useEquipmentAccess();
  const [today] = useState(todayIso);
  const [adding, setAdding] = useState(false);
  const latest = latestDocuments(unit.documents);
  return (
    <Section
      id="unit-documents"
      title={t("documentsTitle")}
      action={
        access.canMaintenance ? (
          <Button size="sm" variant="outline" onClick={() => setAdding(true)} data-testid="add-document">
            {t("addDocument")}
          </Button>
        ) : null
      }
    >
      {latest.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("noDocuments")}</p>
      ) : (
        <ul className="flex flex-col divide-y rounded-lg border" data-testid="document-list">
          {latest.map((document) => (
            <li key={document.id} className="flex flex-wrap items-center gap-2 p-3">
              <span className="min-w-0 flex-1 font-medium">{kindText(document.kind)}</span>
              <span className="text-sm text-muted-foreground">{document.number}</span>
              <span className="text-sm tabular-nums">{formatShortDate(document.valid_until)}</span>
              {document.document_id ? (
                <Link
                  href={`/documents/${document.document_id}`}
                  className="text-sm underline-offset-4 hover:underline"
                  data-testid="document-row-link"
                >
                  {document.document_no ?? t("linkedDocument")}
                </Link>
              ) : null}
              <DocumentStateBadge state={documentState(document.valid_until, today)} />
            </li>
          ))}
        </ul>
      )}
      {adding ? <DocumentDialog unitId={unit.id} onClose={() => setAdding(false)} /> : null}
    </Section>
  );
}

export function UnitRatesSection({ unit, access }: { unit: UnitCard; access: EquipmentAccess }) {
  const t = useTranslations("Equipment.unit");
  const unitText = useTranslations("Equipment.unitName");
  const message = useEquipmentErrorMessage();
  const client = useQueryClient();
  const rates = useOwnRates(unit.legal_entity_id, unit.equipment_type_id, access.canRates);
  const [proposing, setProposing] = useState(false);
  const approve = useMutation({
    mutationFn: (id: string) => api.post(`${EQUIPMENT_API}/own-rates/${id}/approve`),
    onSuccess: () => client.invalidateQueries({ queryKey: EQUIPMENT_KEY }),
  });
  return (
    <Section
      id="unit-rates"
      title={t("ratesTitle")}
      action={
        access.canWrite ? (
          <Button size="sm" variant="outline" onClick={() => setProposing(true)} data-testid="propose-rate">
            {t("proposeRate")}
          </Button>
        ) : null
      }
    >
      {approve.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {message(approve.error)}
        </p>
      ) : null}
      {rates.isSuccess && rates.data.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("noRates")}</p>
      ) : null}
      {rates.isSuccess && rates.data.length > 0 ? (
        <ul className="flex flex-col divide-y rounded-lg border" data-testid="rate-list">
          {rates.data.map((row) => {
            const approved = row.approved_by !== null;
            const canApprove =
              access.canApproveRate && !approved && row.proposed_by !== access.userId;
            return (
              <li key={row.id} className="flex flex-wrap items-center gap-2 p-3" data-approved={approved}>
                <span className="min-w-0 flex-1 font-medium">{unitText(row.unit)}</span>
                <span className="tabular-nums">{row.amount}</span>
                <span className="text-sm text-muted-foreground">{formatShortDate(row.valid_from)}</span>
                <span className="text-sm">{approved ? t("rateApproved") : t("ratePending")}</span>
                {canApprove ? (
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={approve.isPending}
                    onClick={() => approve.mutate(row.id)}
                    data-testid="approve-rate"
                  >
                    {t("approveRate")}
                  </Button>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : null}
      {proposing ? <OwnRateDialog unit={unit} onClose={() => setProposing(false)} /> : null}
    </Section>
  );
}
