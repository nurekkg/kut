"use client";

import { useTranslations } from "next-intl";
import { useMemo } from "react";

import { useAssignments, useUnits } from "@/lib/equipment/queries";
import { formatShortDate } from "@/lib/schedule/dates";

// Машинист: только свои назначения (RLS отдаёт лишь их). Только чтение; путевые листы - SE-2.
export function MyAssignmentsView() {
  const t = useTranslations("Equipment.my");
  const statusText = useTranslations("Equipment.assignmentStatus");
  const assignments = useAssignments({});
  const units = useUnits({});
  const registry = useMemo(
    () => new Map((units.data ?? []).map((unit) => [unit.id, unit.registry_number])),
    [units.data],
  );
  const rows = useMemo(
    () => [...(assignments.data ?? [])].sort((a, b) => a.date_from.localeCompare(b.date_from)),
    [assignments.data],
  );
  return (
    <section className="flex flex-col gap-4" aria-labelledby="my-title">
      <div className="flex flex-col gap-1">
        <h1 id="my-title" className="text-xl font-semibold tracking-tight">
          {t("title")}
        </h1>
        <p className="text-sm text-muted-foreground">{t("hint")}</p>
      </div>
      {assignments.isSuccess && rows.length === 0 ? (
        <p className="rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">
          {t("empty")}
        </p>
      ) : null}
      {assignments.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {t("loadFailed")}
        </p>
      ) : null}
      <ul className="flex flex-col gap-3" data-testid="my-assignments">
        {rows.map((row) => (
          <li key={row.id} className="flex flex-col gap-2 rounded-xl border bg-card p-4" data-status={row.status} data-assignment-id={row.id}>
            <div className="flex items-start justify-between gap-2">
              <span className="min-w-0 break-all text-base font-semibold">{registry.get(row.equipment_unit_id) ?? "—"}</span>
              <span className="shrink-0 text-sm text-muted-foreground">{statusText(row.status)}</span>
            </div>
            <p className="text-sm">{row.project_name ?? "—"}</p>
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm tabular-nums">
              <span>
                {formatShortDate(row.date_from)} — {formatShortDate(row.date_to)}
              </span>
              <span>{t("hours", { hours: row.planned_hours_per_day })}</span>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
