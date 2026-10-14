"use client";

import { useTranslations } from "next-intl";
import { useMemo } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { Button } from "@/components/ui/button";
import { useAssignments, useOperators, useUnits } from "@/lib/equipment/queries";
import { todayIso } from "@/lib/schedule/dates";

export type TodayItem = {
  assignmentId: string;
  unitId: string;
  unitLabel: string;
  projectId: string;
  projectName: string;
  operatorId: string;
  plannedHours: string;
  workDate: string;
};

// Назначения машиниста на сегодня: может быть два объекта в один день. Единицы и объекты видны по
// RLS; список - только назначения с сегодняшней датой и не снятые. Список объектов машинисту не нужен:
// название объекта приходит в назначении.
export function TodayView({ onEnter }: { onEnter: (item: TodayItem) => void }) {
  const t = useTranslations("EquipmentField.today");
  const { user } = useAuth();
  const today = todayIso();
  const assignments = useAssignments({});
  const units = useUnits({});
  const operators = useOperators();

  const items = useMemo<TodayItem[]>(() => {
    const operator = operators.data?.find((row) => row.user_id === user?.id);
    if (!operator) return [];
    const unitLabel = new Map((units.data ?? []).map((row) => [row.id, row.registry_number]));
    return (assignments.data ?? [])
      .filter(
        (row) =>
          row.status === "planned" &&
          row.date_from <= today &&
          today <= row.date_to &&
          (row.operator_id === null || row.operator_id === operator.id),
      )
      .map((row) => ({
        assignmentId: row.id,
        unitId: row.equipment_unit_id,
        unitLabel: unitLabel.get(row.equipment_unit_id) ?? row.equipment_unit_id,
        projectId: row.project_id,
        projectName: row.project_name ?? "",
        operatorId: operator.id,
        plannedHours: row.planned_hours_per_day,
        workDate: today,
      }))
      .sort((a, b) => a.projectName.localeCompare(b.projectName, "ru"));
  }, [assignments.data, units.data, operators.data, user?.id, today]);

  if (assignments.isPending || operators.isPending) {
    return <p className="text-sm text-muted-foreground">…</p>;
  }
  if (items.length === 0) {
    return <p className="text-sm text-muted-foreground">{t("empty")}</p>;
  }
  return (
    <ul className="space-y-3" data-testid="today-list">
      {items.map((item) => (
        <li key={item.assignmentId} className="space-y-3 rounded-xl border p-4">
          <div className="space-y-1">
            <p className="text-base font-semibold" data-testid="today-unit">
              {item.unitLabel}
            </p>
            <p className="text-sm text-muted-foreground">
              {t("project", { name: item.projectName })}
            </p>
            <p className="text-sm text-muted-foreground">
              {t("plan", { hours: item.plannedHours })}
            </p>
          </div>
          <Button
            type="button"
            size="lg"
            className="h-12 w-full"
            onClick={() => onEnter(item)}
            data-testid="today-enter"
          >
            {t("enter")}
          </Button>
        </li>
      ))}
    </ul>
  );
}
