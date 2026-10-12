"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { ContractStatusBadge } from "@/components/equipment/badges";
import { Field, FormDialog } from "@/components/equipment/equipment-dialogs";
import { Section } from "@/components/equipment/section";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { api, newIdempotencyKey } from "@/lib/api";
import { useEquipmentAccess } from "@/lib/equipment/access";
import { useEquipmentErrorMessage } from "@/lib/equipment/errors";
import { EQUIPMENT_KEY } from "@/lib/equipment/keys";
import {
  EQUIPMENT_API,
  useContracts,
  useCounterparties,
  useLegalEntities,
  useRates,
} from "@/lib/equipment/queries";
import { type ContractRow, UNIT_NAMES, type UnitName } from "@/lib/equipment/types";
import { formatShortDate, todayIso } from "@/lib/schedule/dates";

// Договоры аренды и тарифы: ввод и активация - финансовый контур (finance:write). Номер и
// контрагент после активации не меняются: исправления - допсоглашением (равно правилам БД).
export function ContractsView() {
  const t = useTranslations("Equipment.contracts");
  const access = useEquipmentAccess();
  const contracts = useContracts();
  const counterparties = useCounterparties();
  const legal = useLegalEntities();
  const [selected, setSelected] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const rows = contracts.data ?? [];
  const counterpartyName = (id: string) => (counterparties.data ?? []).find((item) => item.id === id)?.name ?? "—";
  const legalName = (id: string) => (legal.data ?? []).find((item) => item.id === id)?.name ?? "—";
  const current = rows.find((row) => row.id === selected) ?? null;

  return (
    <section className="flex flex-col gap-4" aria-labelledby="contracts-title">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 id="contracts-title" className="text-xl font-semibold tracking-tight">
            {t("title")}
          </h1>
          <p className="text-sm text-muted-foreground">{t("hint")}</p>
        </div>
        {access.canFinanceWrite ? (
          <Button onClick={() => setCreating(true)} data-testid="new-contract">
            {t("add")}
          </Button>
        ) : null}
      </div>

      {contracts.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {t("loadFailed")}
        </p>
      ) : null}
      {contracts.isSuccess && rows.length === 0 ? (
        <p className="rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">
          {t("empty")}
        </p>
      ) : null}

      <ul className="flex flex-col gap-2" data-testid="contract-list">
        {rows.map((row) => (
          <li key={row.id}>
            <button
              type="button"
              onClick={() => setSelected(row.id === selected ? null : row.id)}
              aria-expanded={row.id === selected}
              className="flex w-full min-w-0 flex-col gap-2 rounded-xl border bg-card p-4 text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50 aria-expanded:border-primary/50"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-medium">{t("numberLabel", { number: row.number })}</span>
                <ContractStatusBadge status={row.status} />
              </div>
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground">
                <span>{counterpartyName(row.counterparty_id)}</span>
                <span>{legalName(row.legal_entity_id)}</span>
                <span className="tabular-nums">
                  {formatShortDate(row.date_from)}
                  {row.date_to ? ` — ${formatShortDate(row.date_to)}` : ""}
                </span>
              </div>
            </button>
          </li>
        ))}
      </ul>

      {current ? <ContractPanel contract={current} canWrite={access.canFinanceWrite} /> : null}
      {creating ? (
        <ContractDialog
          legal={legal.data ?? []}
          counterparties={counterparties.data ?? []}
          onClose={() => setCreating(false)}
        />
      ) : null}
    </section>
  );
}

function ContractPanel({ contract, canWrite }: { contract: ContractRow; canWrite: boolean }) {
  const t = useTranslations("Equipment.contracts");
  const unitText = useTranslations("Equipment.unitName");
  const message = useEquipmentErrorMessage();
  const client = useQueryClient();
  const rates = useRates(contract.id);
  const transition = useMutation({
    mutationFn: (status: "active" | "closed" | "terminated") =>
      api.patch(`${EQUIPMENT_API}/contracts/${contract.id}`, { status }),
    onSuccess: () => client.invalidateQueries({ queryKey: EQUIPMENT_KEY }),
  });
  const next: { status: "active" | "closed" | "terminated"; label: string }[] =
    contract.status === "draft"
      ? [{ status: "active", label: t("activate") }]
      : contract.status === "active"
        ? [
            { status: "closed", label: t("close") },
            { status: "terminated", label: t("terminate") },
          ]
        : [];
  return (
    <Section id="contract-panel" title={t("panelTitle", { number: contract.number })}>
      {contract.status !== "draft" ? <p className="text-sm text-muted-foreground">{t("frozenHint")}</p> : null}
      {transition.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {message(transition.error)}
        </p>
      ) : null}
      {canWrite && next.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {next.map((action) => (
            <Button
              key={action.status}
              size="sm"
              variant={action.status === "active" ? "default" : "outline"}
              disabled={transition.isPending}
              onClick={() => transition.mutate(action.status)}
              data-testid={`contract-${action.status}`}
            >
              {action.label}
            </Button>
          ))}
        </div>
      ) : null}
      <h3 className="text-sm font-medium">{t("ratesTitle")}</h3>
      {rates.isSuccess && rates.data.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("noRates")}</p>
      ) : null}
      {rates.isSuccess && rates.data.length > 0 ? (
        <ul className="flex flex-col divide-y rounded-lg border" data-testid="rate-table">
          {rates.data.map((rate) => (
            <li key={rate.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 p-3 text-sm">
              <span className="font-medium">{unitText(rate.unit)}</span>
              <span className="text-muted-foreground">
                {rate.with_operator ? t("withOperator") : t("withoutOperator")}
              </span>
              <span className="tabular-nums">{rate.amount}</span>
              {rate.min_quantity ? <span className="tabular-nums text-muted-foreground">{t("minQuantity", { value: rate.min_quantity })}</span> : null}
              <span className="text-muted-foreground">{formatShortDate(rate.valid_from)}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {canWrite ? <RateForm contractId={contract.id} /> : null}
    </Section>
  );
}

function RateForm({ contractId }: { contractId: string }) {
  const t = useTranslations("Equipment.contracts");
  const unitText = useTranslations("Equipment.unitName");
  const message = useEquipmentErrorMessage();
  const client = useQueryClient();
  const [key, setKey] = useState(newIdempotencyKey);
  const [unit, setUnit] = useState<UnitName>("hour");
  const [withOperator, setWithOperator] = useState(false);
  const [amount, setAmount] = useState("");
  const [minQuantity, setMinQuantity] = useState("");
  const [validFrom, setValidFrom] = useState(todayIso);
  const save = useMutation({
    mutationFn: () =>
      api.post(
        `${EQUIPMENT_API}/contracts/${contractId}/rates`,
        {
          unit,
          with_operator: withOperator,
          amount: amount.trim(),
          min_quantity: minQuantity.trim() || null,
          valid_from: validFrom,
        },
        key,
      ),
    onSuccess: async () => {
      setAmount("");
      setMinQuantity("");
      setKey(newIdempotencyKey());
      await client.invalidateQueries({ queryKey: EQUIPMENT_KEY });
    },
  });
  return (
    <form
      className="grid gap-3 rounded-lg border p-3 sm:grid-cols-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (amount.trim()) save.mutate();
      }}
      data-testid="rate-form"
    >
      <h3 className="text-sm font-medium sm:col-span-2">{t("addRate")}</h3>
      <Field id="rate-unit-add" label={t("rateUnit")}>
        <NativeSelect id="rate-unit-add" value={unit} onChange={(event) => setUnit(event.target.value as UnitName)}>
          {UNIT_NAMES.map((value) => (
            <option key={value} value={value}>
              {unitText(value)}
            </option>
          ))}
        </NativeSelect>
      </Field>
      <div className="flex items-end gap-2">
        <input
          id="rate-operator"
          type="checkbox"
          className="size-4"
          checked={withOperator}
          onChange={(event) => setWithOperator(event.target.checked)}
        />
        <Label htmlFor="rate-operator">{t("withOperatorField")}</Label>
      </div>
      <Field id="rate-amount-add" label={t("rateAmount")}>
        <Input id="rate-amount-add" inputMode="decimal" value={amount} onChange={(event) => setAmount(event.target.value)} required />
      </Field>
      <Field id="rate-min-add" label={t("minQuantityField")}>
        <Input id="rate-min-add" inputMode="decimal" value={minQuantity} onChange={(event) => setMinQuantity(event.target.value)} />
      </Field>
      <Field id="rate-valid-add" label={t("rateValidFrom")}>
        <Input id="rate-valid-add" type="date" value={validFrom} onChange={(event) => setValidFrom(event.target.value)} required />
      </Field>
      <div className="flex flex-col gap-2 sm:col-span-2">
        {save.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {message(save.error)}
          </p>
        ) : null}
        <Button type="submit" className="w-full sm:w-auto" disabled={!amount.trim() || save.isPending}>
          {t("saveRate")}
        </Button>
      </div>
    </form>
  );
}

function ContractDialog({
  legal,
  counterparties,
  onClose,
}: {
  legal: { id: string; name: string }[];
  counterparties: { id: string; name: string }[];
  onClose: () => void;
}) {
  const t = useTranslations("Equipment.contracts");
  const client = useQueryClient();
  const [key] = useState(newIdempotencyKey);
  const [legalEntity, setLegalEntity] = useState("");
  const [counterparty, setCounterparty] = useState("");
  const [number, setNumber] = useState("");
  const [from, setFrom] = useState(todayIso);
  const [until, setUntil] = useState("");
  const save = useMutation({
    mutationFn: () =>
      api.post(
        `${EQUIPMENT_API}/contracts`,
        {
          legal_entity_id: legalEntity,
          counterparty_id: counterparty,
          kind: "rental_equipment",
          number: number.trim(),
          date_from: from,
          date_to: until || null,
        },
        key,
      ),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: EQUIPMENT_KEY });
      onClose();
    },
  });
  return (
    <FormDialog
      title={t("newTitle")}
      description={t("newHint")}
      onClose={onClose}
      submitDisabled={!legalEntity || !counterparty || !number.trim() || !from}
      pending={save.isPending}
      error={save.error}
      onSubmit={() => save.mutate()}
    >
      <Field id="contract-legal" label={t("legalEntity")}>
        <NativeSelect id="contract-legal" value={legalEntity} onChange={(event) => setLegalEntity(event.target.value)} required>
          <option value="">{t("chooseLegal")}</option>
          {legal.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
        </NativeSelect>
      </Field>
      <Field id="contract-counterparty" label={t("counterparty")}>
        <NativeSelect id="contract-counterparty" value={counterparty} onChange={(event) => setCounterparty(event.target.value)} required>
          <option value="">{t("chooseCounterparty")}</option>
          {counterparties.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
        </NativeSelect>
      </Field>
      <Field id="contract-number" label={t("number")}>
        <Input id="contract-number" value={number} maxLength={100} onChange={(event) => setNumber(event.target.value)} required />
      </Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field id="contract-from" label={t("dateFrom")}>
          <Input id="contract-from" type="date" value={from} onChange={(event) => setFrom(event.target.value)} required />
        </Field>
        <Field id="contract-until" label={t("dateTo")}>
          <Input id="contract-until" type="date" value={until} onChange={(event) => setUntil(event.target.value)} />
        </Field>
      </div>
    </FormDialog>
  );
}
