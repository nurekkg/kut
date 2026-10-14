"use client";

import { ArrowLeft } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useMemo, useState } from "react";

import { AppNav } from "@/components/projects/app-nav";
import { QueryState } from "@/components/settings/shared";
import { Button, buttonVariants } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useUnits } from "@/lib/equipment/queries";
import { useUsage, type UsageRow } from "@/lib/equipment/shifts";
import { todayIso } from "@/lib/schedule/dates";
import { cn } from "@/lib/utils";

// Вкладка «Техника» объекта: часы, рейсы, тонны, простой и литры по единицам и дням. Здесь нет ни одной
// суммы: деньги по технике - в журнале затрат, с правом на ставки. Строки - по подтверждённым листам
// и заправкам; сервер отдаёт только объекты, которые видит пользователь.
export function ProjectEquipment({ projectId }: { projectId: string }) {
  const t = useTranslations("ProjectEquipment");
  const [days, setDays] = useState<7 | 30>(7);
  const today = todayIso();
  const from = useMemo(() => {
    const date = new Date(`${today}T00:00:00Z`);
    date.setUTCDate(date.getUTCDate() - (days - 1));
    return date.toISOString().slice(0, 10);
  }, [today, days]);
  const usage = useUsage({ projectId, dateFrom: from, dateTo: today });
  const units = useUnits({ projectId });
  const label = new Map((units.data ?? []).map((row) => [row.id, row.registry_number]));
  const rows: UsageRow[] = usage.data ?? [];

  const totals = rows.reduce(
    (sum, row) => ({
      shifts: sum.shifts + row.shifts,
      hours: sum.hours + Number(row.hours),
      trips: sum.trips + row.trips,
      tonnes: sum.tonnes + Number(row.tonnes),
      downtime: sum.downtime + Number(row.downtime_hours),
      liters: sum.liters + Number(row.liters),
    }),
    { shifts: 0, hours: 0, trips: 0, tonnes: 0, downtime: 0, liters: 0 },
  );

  return (
    <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-4 px-4 py-4">
      <AppNav />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">{t("title")}</h1>
          <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
        </div>
        <Link
          href={`/projects/${projectId}`}
          className={buttonVariants({ variant: "outline", size: "lg", className: "h-10" })}
        >
          <ArrowLeft data-icon="inline-start" aria-hidden />
          {t("back")}
        </Link>
      </div>
      <div className="flex flex-wrap gap-2" role="group" aria-label={t("period.label")}>
        {([7, 30] as const).map((value) => (
          <Button
            key={value}
            type="button"
            variant={days === value ? "default" : "outline"}
            size="lg"
            className={cn("h-10", days === value && "pointer-events-none")}
            aria-pressed={days === value}
            onClick={() => setDays(value)}
          >
            {value === 7 ? t("period.week") : t("period.month")}
          </Button>
        ))}
      </div>
      <QueryState loading={usage.isPending} error={usage.error} onRetry={() => void usage.refetch()}>
        {rows.length === 0 ? (
          <p className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground" data-testid="project-equipment-empty">
            {t("empty")}
          </p>
        ) : (
          <div className="overflow-x-auto rounded-xl border">
            <Table data-testid="project-equipment-table">
              <TableHeader>
                <TableRow>
                  <TableHead>{t("table.date")}</TableHead>
                  <TableHead>{t("table.unit")}</TableHead>
                  <TableHead className="text-right">{t("table.shifts")}</TableHead>
                  <TableHead className="text-right">{t("table.hours")}</TableHead>
                  <TableHead className="text-right">{t("table.trips")}</TableHead>
                  <TableHead className="text-right">{t("table.tonnes")}</TableHead>
                  <TableHead className="text-right">{t("table.downtime")}</TableHead>
                  <TableHead className="text-right">{t("table.liters")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row) => (
                  <TableRow key={`${row.day}-${row.equipment_unit_id}`}>
                    <TableCell>{row.day}</TableCell>
                    <TableCell>{label.get(row.equipment_unit_id) ?? row.equipment_unit_id}</TableCell>
                    <TableCell className="text-right tabular-nums">{row.shifts}</TableCell>
                    <TableCell className="text-right tabular-nums">{row.hours}</TableCell>
                    <TableCell className="text-right tabular-nums">{row.trips}</TableCell>
                    <TableCell className="text-right tabular-nums">{row.tonnes}</TableCell>
                    <TableCell className="text-right tabular-nums">{row.downtime_hours}</TableCell>
                    <TableCell className="text-right tabular-nums">{row.liters}</TableCell>
                  </TableRow>
                ))}
                <TableRow className="font-medium" data-testid="project-equipment-totals">
                  <TableCell colSpan={2}>{t("totals")}</TableCell>
                  <TableCell className="text-right tabular-nums">{totals.shifts}</TableCell>
                  <TableCell className="text-right tabular-nums">{totals.hours.toFixed(2)}</TableCell>
                  <TableCell className="text-right tabular-nums">{totals.trips}</TableCell>
                  <TableCell className="text-right tabular-nums">{totals.tonnes.toFixed(3)}</TableCell>
                  <TableCell className="text-right tabular-nums">{totals.downtime.toFixed(2)}</TableCell>
                  <TableCell className="text-right tabular-nums">{totals.liters.toFixed(2)}</TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </div>
        )}
      </QueryState>
    </main>
  );
}
