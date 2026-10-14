"use client";

import { useTranslations } from "next-intl";
import { useMemo, useState } from "react";

import { useEquipmentAccess } from "@/lib/equipment/access";
import { useUnits, useVisibleProjects } from "@/lib/equipment/queries";
import { useCostEntries, type CostEntryRow } from "@/lib/equipment/shifts";
import { todayIso } from "@/lib/schedule/dates";

import { Field } from "@/components/auth/form-parts";
import { QueryState } from "@/components/settings/shared";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

const CATEGORIES = [
  "equipment_own",
  "equipment_rental",
  "equipment_fixed",
  "equipment_min_payment",
  "equipment_fuel",
  "equipment_repair",
] as const;

// Журнал затрат: строки и итоги по технике. Только с правом на ставки (деньги); сервер не отдаёт строк
// без права, поэтому здесь доступ - лишь подсказка экрану.
export function JournalView() {
  const t = useTranslations("EquipmentJournal");
  const tCategory = useTranslations("Equipment.categories");
  const access = useEquipmentAccess();
  const today = todayIso();
  const [projectId, setProjectId] = useState("");
  const [unitId, setUnitId] = useState("");
  const [category, setCategory] = useState("");
  const [from, setFrom] = useState(`${today.slice(0, 7)}-01`);
  const [to, setTo] = useState(today);
  const projects = useVisibleProjects();
  const units = useUnits({});
  const entries = useCostEntries({
    projectId: projectId || undefined,
    unitId: unitId || undefined,
    category: category || undefined,
    dateFrom: from,
    dateTo: to,
  });
  const projectName = new Map((projects.data ?? []).map((row) => [row.id, row.name]));
  const unitLabel = new Map((units.data ?? []).map((row) => [row.id, row.registry_number]));
  const rows = useMemo<CostEntryRow[]>(() => entries.data ?? [], [entries.data]);

  // Итоги - по строкам фильтра: сторно уже с отрицательной суммой, поэтому просто складываем.
  const byCategory = useMemo(() => {
    const sums = new Map<string, number>();
    for (const row of rows) sums.set(row.category, (sums.get(row.category) ?? 0) + Number(row.amount));
    return [...sums.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [rows]);

  if (!access.canRates) {
    return (
      <p role="alert" className="rounded-xl border p-4 text-sm" data-testid="journal-forbidden">
        {t("forbidden")}
      </p>
    );
  }

  return (
    <section className="space-y-4" data-testid="journal">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">{t("title")}</h1>
        <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
      </div>

      <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <Field id="journal-project" label={t("filters.project")}>
          <NativeSelect id="journal-project" value={projectId} onChange={(event) => setProjectId(event.target.value)} data-testid="journal-project">
            <option value="">{t("filters.all")}</option>
            {(projects.data ?? []).map((row) => (
              <option key={row.id} value={row.id}>
                {row.name}
              </option>
            ))}
          </NativeSelect>
        </Field>
        <Field id="journal-category" label={t("filters.category")}>
          <NativeSelect id="journal-category" value={category} onChange={(event) => setCategory(event.target.value)} data-testid="journal-category">
            <option value="">{t("filters.all")}</option>
            {CATEGORIES.map((code) => (
              <option key={code} value={code}>
                {tCategory(code)}
              </option>
            ))}
          </NativeSelect>
        </Field>
        <Field id="journal-unit" label={t("filters.unit")}>
          <NativeSelect id="journal-unit" value={unitId} onChange={(event) => setUnitId(event.target.value)} data-testid="journal-unit">
            <option value="">{t("filters.all")}</option>
            {(units.data ?? []).map((row) => (
              <option key={row.id} value={row.id}>
                {row.registry_number}
              </option>
            ))}
          </NativeSelect>
        </Field>
        <Field id="journal-from" label={t("filters.from")}>
          <Input id="journal-from" type="date" value={from} onChange={(event) => setFrom(event.target.value)} data-testid="journal-from" />
        </Field>
        <Field id="journal-to" label={t("filters.to")}>
          <Input id="journal-to" type="date" value={to} onChange={(event) => setTo(event.target.value)} data-testid="journal-to" />
        </Field>
      </div>

      <div className="rounded-xl border p-4" data-testid="journal-totals">
        <h2 className="mb-2 text-base font-semibold">{t("totals.title")}</h2>
        {byCategory.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("totals.empty")}</p>
        ) : (
          <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {byCategory.map(([code, sum]) => (
              <li key={code} className="flex items-center justify-between gap-3 text-sm">
                <span className="text-muted-foreground">{tCategory(code as (typeof CATEGORIES)[number])}</span>
                <span className="tabular-nums font-medium" data-testid="journal-total-amount">{sum.toFixed(2)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <QueryState loading={entries.isPending} error={entries.error} onRetry={() => void entries.refetch()}>
        {rows.length === 0 ? (
          <p className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">{t("table.empty")}</p>
        ) : (
          <div className="overflow-x-auto rounded-xl border">
            <Table data-testid="journal-table">
              <TableHeader>
                <TableRow>
                  <TableHead>{t("table.date")}</TableHead>
                  <TableHead>{t("table.project")}</TableHead>
                  <TableHead>{t("table.category")}</TableHead>
                  <TableHead>{t("table.unit")}</TableHead>
                  <TableHead className="text-right">{t("table.quantity")}</TableHead>
                  <TableHead className="text-right">{t("table.rate")}</TableHead>
                  <TableHead className="text-right">{t("table.amount")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row) => (
                  <TableRow key={row.id} data-testid="journal-row">
                    <TableCell>{row.period}</TableCell>
                    <TableCell>{row.project_id ? (projectName.get(row.project_id) ?? "—") : "—"}</TableCell>
                    <TableCell>
                      {tCategory(row.category as (typeof CATEGORIES)[number])}
                      {row.reversal_of_id ? (
                        <span className="ml-2 rounded bg-muted px-1.5 py-0.5 text-xs" data-testid="journal-reversal">
                          {t("reversal")}
                        </span>
                      ) : null}
                    </TableCell>
                    <TableCell>{row.equipment_unit_id ? (unitLabel.get(row.equipment_unit_id) ?? "—") : "—"}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {row.quantity !== null ? `${row.quantity} ${row.tariff_unit ?? ""}` : "—"}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{row.rate ?? "—"}</TableCell>
                    <TableCell className="text-right tabular-nums">{row.amount}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </QueryState>
    </section>
  );
}
