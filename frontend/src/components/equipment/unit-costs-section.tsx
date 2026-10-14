"use client";

import { useTranslations } from "next-intl";

import { Section } from "@/components/equipment/section";
import { QueryState } from "@/components/settings/shared";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useCostEntries } from "@/lib/equipment/shifts";
import { todayIso } from "@/lib/schedule/dates";

// Затраты по единице за 90 дней: проводки журнала (аренда, ГСМ, ремонт). Денег без права на ставки
// сервер не отдаёт, раздел показывают только тем, у кого это право.
export function UnitCostsSection({ unitId }: { unitId: string }) {
  const t = useTranslations("Equipment.unit.costs");
  const tCategory = useTranslations("Equipment.categories");
  const today = todayIso();
  const from = new Date(`${today}T00:00:00Z`);
  from.setUTCDate(from.getUTCDate() - 89);
  const entries = useCostEntries({ unitId, dateFrom: from.toISOString().slice(0, 10), dateTo: today });
  const rows = entries.data ?? [];
  return (
    <Section id="unit-costs" title={t("title")}>
      <p className="text-sm text-muted-foreground">{t("note")}</p>
      <QueryState loading={entries.isPending} error={entries.error} onRetry={() => void entries.refetch()}>
        {rows.length === 0 ? (
          <p className="text-sm text-muted-foreground" data-testid="unit-costs-empty">
            {t("empty")}
          </p>
        ) : (
          <div className="overflow-x-auto rounded-lg border">
            <Table data-testid="unit-costs-table">
              <TableHeader>
                <TableRow>
                  <TableHead>{t("date")}</TableHead>
                  <TableHead>{t("category")}</TableHead>
                  <TableHead className="text-right">{t("amount")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row) => (
                  <TableRow key={row.id}>
                    <TableCell>{row.period}</TableCell>
                    <TableCell>{tCategory(row.category as "equipment_own")}</TableCell>
                    <TableCell className="text-right tabular-nums">{row.amount}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </QueryState>
    </Section>
  );
}
