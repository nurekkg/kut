"use client";

import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { useAssignments, useUnits } from "@/lib/equipment/queries";
import { useShifts, type ShiftRow } from "@/lib/equipment/shifts";

export type FixTarget = {
  shift: ShiftRow;
  unitLabel: string;
  projectName: string;
  operatorId: string;
};

// Мои листы машиниста: статус и причина отклонения; отклонённый лист исправляют и отправляют снова.
// Сервер отдаёт машинисту только его листы (RLS). Название объекта берём из назначений, номер единицы -
// из листа (он приходит вместе с листом), список объектов машинисту не нужен.
export function MineView({ onFix }: { onFix: (target: FixTarget) => void }) {
  const t = useTranslations("EquipmentField.mine");
  const shifts = useShifts({});
  const units = useUnits({});
  const assignments = useAssignments({});

  if (shifts.isPending) return <p className="text-sm text-muted-foreground">…</p>;
  const rows = [...(shifts.data ?? [])].sort((a, b) => b.work_date.localeCompare(a.work_date));
  if (rows.length === 0) return <p className="text-sm text-muted-foreground">{t("empty")}</p>;

  const unitLabel = new Map((units.data ?? []).map((row) => [row.id, row.registry_number]));
  const projectName = new Map(
    (assignments.data ?? []).map((row) => [row.project_id, row.project_name ?? ""]),
  );
  return (
    <ul className="space-y-3" data-testid="mine-list">
      {rows.map((row) => {
        const label =
          row.unit_registry_number ?? unitLabel.get(row.equipment_unit_id) ?? row.equipment_unit_id;
        const project = projectName.get(row.project_id) ?? "";
        return (
          <li key={row.id} className="space-y-2 rounded-xl border p-4" data-testid="mine-row">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0 space-y-1">
                <p className="font-medium">{label}</p>
                <p className="text-sm text-muted-foreground">
                  {project} · {row.work_date} · {row.hours} ч
                </p>
              </div>
              <span className="shrink-0 rounded-md bg-muted px-2 py-1 text-xs" data-testid="mine-status">
                {t(`status.${row.status}`)}
              </span>
            </div>
            {row.status === "rejected" && row.reject_reason ? (
              <p className="text-sm text-destructive">{t("reason", { reason: row.reject_reason })}</p>
            ) : null}
            {row.status === "rejected" ? (
              <Button
                type="button"
                size="lg"
                className="h-12 w-full"
                onClick={() =>
                  onFix({
                    shift: row,
                    unitLabel: label,
                    projectName: project,
                    operatorId: row.operator_id,
                  })
                }
                data-testid="mine-fix"
              >
                {t("fix")}
              </Button>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
