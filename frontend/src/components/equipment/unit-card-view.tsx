"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";

import { ContractStatusBadge, UnitStatusBadge } from "@/components/equipment/badges";
import { Section } from "@/components/equipment/section";
import { UnitCostsSection } from "@/components/equipment/unit-costs-section";
import {
  UnitDocumentsSection,
  UnitMaintenanceSection,
  UnitRatesSection,
} from "@/components/equipment/unit-sections";
import { useEquipmentAccess } from "@/lib/equipment/access";
import { useEquipmentErrorMessage } from "@/lib/equipment/errors";
import {
  useAssignments,
  useContracts,
  useCounterparties,
  useRates,
  useUnitCard,
} from "@/lib/equipment/queries";
import type { UnitCard, UnitName } from "@/lib/equipment/types";
import { formatShortDate } from "@/lib/schedule/dates";

// Карточка единицы: назначения и ТО, документы, тарифы договора и нормативы - только с правом.
export function UnitCardView({ unitId }: { unitId: string }) {
  const t = useTranslations("Equipment.unit");
  const message = useEquipmentErrorMessage();
  const access = useEquipmentAccess();
  const unit = useUnitCard(unitId);
  if (unit.isPending) return <p className="text-sm text-muted-foreground">{t("loading")}</p>;
  if (unit.isError) {
    return (
      <p role="alert" className="text-sm text-destructive">
        {message(unit.error)}
      </p>
    );
  }
  const card = unit.data;
  return (
    <div className="flex flex-col gap-5" data-testid="unit-card">
      <UnitHeader unit={card} />
      <UnitAssignmentsSection unitId={card.id} />
      <UnitMaintenanceSection unit={card} />
      <UnitDocumentsSection unit={card} />
      {access.canRates && card.contract_id ? <UnitContractSection unit={card} /> : null}
      {access.canRates || access.canWrite ? <UnitRatesSection unit={card} access={access} /> : null}
      {access.canRates ? <UnitCostsSection unitId={unitId} /> : null}
    </div>
  );
}

function UnitHeader({ unit }: { unit: UnitCard }) {
  const t = useTranslations("Equipment.unit");
  const ownership = useTranslations("Equipment.registry.ownership");
  return (
    <header className="flex flex-col gap-2">
      <Link href="/equipment" className="text-sm text-muted-foreground underline-offset-4 hover:underline">
        {t("back")}
      </Link>
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="min-w-0 break-all text-xl font-semibold tracking-tight">{unit.registry_number}</h1>
        <UnitStatusBadge status={unit.status} />
      </div>
      <p className="text-sm text-muted-foreground">
        {[unit.type_code, ownership(unit.ownership), unit.model, unit.year].filter(Boolean).join(" · ")}
      </p>
    </header>
  );
}

function UnitAssignmentsSection({ unitId }: { unitId: string }) {
  const t = useTranslations("Equipment.unit");
  const statusText = useTranslations("Equipment.assignmentStatus");
  const assignments = useAssignments({ unitId });
  const rows = [...(assignments.data ?? [])].sort((a, b) => b.date_from.localeCompare(a.date_from));
  return (
    <Section id="unit-assignments" title={t("assignmentsTitle")}>
      {assignments.isSuccess && rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("noAssignments")}</p>
      ) : null}
      {rows.length > 0 ? (
        <ul className="flex flex-col divide-y rounded-lg border" data-testid="assignment-list">
          {rows.map((row) => (
            <li key={row.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 p-3 text-sm">
              <span className="min-w-0 flex-1 font-medium">{row.project_name ?? "—"}</span>
              <span className="tabular-nums">
                {formatShortDate(row.date_from)} — {formatShortDate(row.date_to)}
              </span>
              <span className="tabular-nums">{t("hoursPerDay", { hours: row.planned_hours_per_day })}</span>
              <span className="text-muted-foreground">{statusText(row.status)}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </Section>
  );
}

// Договор и тарифы арендованной техники: только для тех, кто видит деньги (read:rates).
function UnitContractSection({ unit }: { unit: UnitCard }) {
  const t = useTranslations("Equipment.unit");
  const unitText = useTranslations("Equipment.unitName");
  const contracts = useContracts();
  const counterparties = useCounterparties();
  const rates = useRates(unit.contract_id);
  const contract = (contracts.data ?? []).find((item) => item.id === unit.contract_id);
  const counterparty = (counterparties.data ?? []).find((item) => item.id === contract?.counterparty_id);
  return (
    <Section id="unit-contract" title={t("contractTitle")}>
      {contract ? (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
          <span className="font-medium">{t("contractNumber", { number: contract.number })}</span>
          <ContractStatusBadge status={contract.status} />
          <span className="text-muted-foreground">{counterparty?.name ?? "—"}</span>
          <span className="tabular-nums text-muted-foreground">
            {formatShortDate(contract.date_from)}
            {contract.date_to ? ` — ${formatShortDate(contract.date_to)}` : ""}
          </span>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">{t("contractHidden")}</p>
      )}
      {rates.isSuccess && rates.data.length > 0 ? (
        <ul className="flex flex-col divide-y rounded-lg border" data-testid="contract-rates">
          {rates.data.map((rate) => (
            <li key={rate.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 p-3 text-sm">
              <span className="font-medium">{unitText(rate.unit as UnitName)}</span>
              <span className="text-muted-foreground">
                {rate.with_operator ? t("withOperator") : t("withoutOperator")}
              </span>
              <span className="tabular-nums">{rate.amount}</span>
              <span className="text-muted-foreground">{formatShortDate(rate.valid_from)}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </Section>
  );
}
