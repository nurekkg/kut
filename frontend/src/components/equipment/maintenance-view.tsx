"use client";

import { useTranslations } from "next-intl";
import { useMemo, useState } from "react";

import { MaintenanceDialog } from "@/components/equipment/equipment-dialogs";
import { Section } from "@/components/equipment/section";
import { MaintenanceList } from "@/components/equipment/unit-sections";
import { Button } from "@/components/ui/button";
import { useEquipmentAccess } from "@/lib/equipment/access";
import { useMaintenance, useUnits } from "@/lib/equipment/queries";
import { maintenanceBucket } from "@/lib/equipment/status";
import type { MaintenanceRow } from "@/lib/equipment/types";
import { todayIso } from "@/lib/schedule/dates";

// ТО и ремонты: что просрочено, что в ремонте сейчас, что начнётся в ближайшие 14 дней.
export function MaintenanceView() {
  const t = useTranslations("Equipment.maintenancePage");
  const access = useEquipmentAccess();
  const [today] = useState(todayIso);
  const planned = useMaintenance("planned");
  const active = useMaintenance("in_progress");
  const units = useUnits({});
  const [adding, setAdding] = useState(false);
  const registry = useMemo(
    () => new Map((units.data ?? []).map((unit) => [unit.id, unit.registry_number])),
    [units.data],
  );
  const groups = useMemo(() => {
    const result: Record<"overdue" | "inRepair" | "upcoming", MaintenanceRow[]> = {
      overdue: [],
      inRepair: [],
      upcoming: [],
    };
    for (const row of [...(planned.data ?? []), ...(active.data ?? [])]) {
      const bucket = maintenanceBucket(row, today);
      if (bucket === "overdue" || bucket === "inRepair" || bucket === "upcoming") result[bucket].push(row);
    }
    return result;
  }, [planned.data, active.data, today]);
  const loading = planned.isPending || active.isPending;
  const failed = planned.isError || active.isError;

  return (
    <section className="flex flex-col gap-4" aria-labelledby="maintenance-title">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 id="maintenance-title" className="text-xl font-semibold tracking-tight">
            {t("title")}
          </h1>
          <p className="text-sm text-muted-foreground">{t("hint")}</p>
        </div>
        {access.canMaintenance ? (
          <Button onClick={() => setAdding(true)} data-testid="new-maintenance">
            {t("add")}
          </Button>
        ) : null}
      </div>

      {loading ? <p className="text-sm text-muted-foreground">{t("loading")}</p> : null}
      {failed ? (
        <p role="alert" className="text-sm text-destructive">
          {t("loadFailed")}
        </p>
      ) : null}

      {(["overdue", "inRepair", "upcoming"] as const).map((group) => (
        <Section key={group} id={`maintenance-${group}`} title={t(`group.${group}`, { count: groups[group].length })}>
          <MaintenanceList
            rows={groups[group]}
            access={access}
            unitLabel={(unitId) => registry.get(unitId) ?? "—"}
            today={today}
          />
        </Section>
      ))}

      {adding ? (
        <MaintenanceDialog units={units.data ?? []} onClose={() => setAdding(false)} />
      ) : null}
    </section>
  );
}
