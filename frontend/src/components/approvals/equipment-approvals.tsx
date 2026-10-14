"use client";

import { useQueries, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { useEquipmentErrorMessage } from "@/lib/equipment/errors";
import { EQUIPMENT_KEY } from "@/lib/equipment/keys";
import {
  EQUIPMENT_API,
  useOperators,
  useUnits,
  useVisibleProjects,
} from "@/lib/equipment/queries";
import {
  candidatesKey,
  useDelegates,
  useFuel,
  useShifts,
  type DelegateRow,
  type ShiftRow,
} from "@/lib/equipment/shifts";
import { todayIso } from "@/lib/schedule/dates";

import { DelegateDialog, EditShiftDialog, RejectShiftDialog } from "./equipment-approval-dialogs";

const PARALLEL = 3;

function daysBefore(iso: string, days: number): string {
  const date = new Date(`${iso}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() - days);
  return date.toISOString().slice(0, 10);
}

// Техника в очереди подтверждения: листы объектов человека. Количества и литры - без денег. Подтверждать
// может тот, у кого право на листы (прораб, техдиректор) или кто назначен делегатом на сегодня; сервер
// проверит это при каждом подтверждении. Правка подтверждённого листа - техдиректору (сторно в журнале).
export function EquipmentApprovals({
  userId,
  canApprove,
  canEdit,
}: {
  userId: string;
  canApprove: boolean;
  canEdit: boolean;
}) {
  const t = useTranslations("Approvals.equipment");
  const message = useEquipmentErrorMessage();
  const queryClient = useQueryClient();
  const today = todayIso();
  const submitted = useShifts({ status: "submitted" });
  const confirmed = useShifts(
    { status: "confirmed", dateFrom: daysBefore(today, 7), dateTo: today },
    canEdit,
  );
  const units = useUnits({});
  const operators = useOperators();
  const projects = useVisibleProjects();
  const delegates = useDelegates();
  const rows = useMemo(
    () => [...(submitted.data ?? [])].sort((a, b) => a.work_date.localeCompare(b.work_date)),
    [submitted.data],
  );
  const firstDay = rows[0]?.work_date ?? today;
  const lastDay = rows[rows.length - 1]?.work_date ?? today;
  const fuel = useFuel({ dateFrom: firstDay, dateTo: lastDay });

  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [running, setRunning] = useState(false);
  const [rejecting, setRejecting] = useState<ShiftRow | null>(null);
  const [editing, setEditing] = useState<ShiftRow | null>(null);
  const [delegating, setDelegating] = useState(false);

  const unitLabel = new Map((units.data ?? []).map((row) => [row.id, row.registry_number]));
  const operatorName = new Map((operators.data ?? []).map((row) => [row.id, row.full_name]));
  const projectName = new Map((projects.data ?? []).map((row) => [row.id, row.name]));
  const liters = new Map<string, number>();
  for (const row of fuel.data ?? []) {
    if (!row.shift_id || row.reversed_at || row.liters === null) continue;
    liters.set(row.shift_id, (liters.get(row.shift_id) ?? 0) + Number(row.liters));
  }
  const activeDelegations = (delegates.data ?? []).filter(
    (row) => row.delegate_user_id === userId && !row.revoked_at && row.date_to >= today,
  );
  // Подтверждать могут право на листы и делегат на сегодня (сервер проверит при каждом подтверждении).
  const canConfirm = canApprove || activeDelegations.length > 0;

  async function confirmMany(ids: string[]): Promise<void> {
    setRunning(true);
    const failed: Record<string, string> = {};
    const waiting = [...ids];
    async function worker(): Promise<void> {
      for (let id = waiting.shift(); id; id = waiting.shift()) {
        try {
          await api.post<ShiftRow>(`${EQUIPMENT_API}/shifts/${id}/confirm`);
        } catch (error) {
          failed[id] = message(error);
        }
      }
    }
    await Promise.all(Array.from({ length: PARALLEL }, () => worker()));
    setErrors(failed);
    setSelected(new Set(Object.keys(failed)));
    setRunning(false);
    await queryClient.invalidateQueries({ queryKey: EQUIPMENT_KEY });
  }

  async function revoke(id: string): Promise<void> {
    await api.post(`${EQUIPMENT_API}/delegates/${id}/revoke`);
    await queryClient.invalidateQueries({ queryKey: EQUIPMENT_KEY });
  }

  const allSelected = rows.length > 0 && rows.every((row) => selected.has(row.id));

  return (
    <section className="space-y-4" aria-labelledby="equipment-approvals-title" data-testid="equipment-approvals">
      <div className="space-y-1">
        <h2 id="equipment-approvals-title" className="text-xl font-semibold tracking-tight">
          {t("title")}
        </h2>
        <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
      </div>

      {activeDelegations.map((row) => (
        <p key={row.id} role="status" className="rounded-lg bg-muted p-3 text-sm" data-testid="delegate-banner">
          {t("delegateBanner", { date: row.date_to })}
        </p>
      ))}

      {submitted.isPending ? (
        <p className="text-sm text-muted-foreground">…</p>
      ) : rows.length === 0 ? (
        <p className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground" data-testid="equipment-approvals-empty">
          {t("empty")}
        </p>
      ) : (
        <>
          {canConfirm ? (
            <div className="flex flex-wrap items-center gap-3">
              <label className="flex min-h-11 cursor-pointer items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  className="size-5 accent-primary"
                  checked={allSelected}
                  onChange={(event) =>
                    setSelected(event.target.checked ? new Set(rows.map((row) => row.id)) : new Set())
                  }
                  data-testid="equipment-select-all"
                />
                {t("selectAll")}
              </label>
              <Button
                type="button"
                size="lg"
                className="ml-auto h-12"
                disabled={selected.size === 0 || running}
                onClick={() => void confirmMany([...selected])}
                data-testid="equipment-approve-selected"
              >
                {t("confirmSelected", { count: selected.size })}
              </Button>
            </div>
          ) : null}
          <ul className="space-y-3">
            {rows.map((row) => (
              <li key={row.id} className="space-y-2 rounded-xl border p-4" data-testid="shift-row">
                <div className="flex items-start gap-3">
                  {canConfirm ? (
                    <input
                      type="checkbox"
                      className="mt-1 size-5 accent-primary"
                      checked={selected.has(row.id)}
                      aria-label={t("select")}
                      onChange={(event) =>
                        setSelected((current) => {
                          const next = new Set(current);
                          if (event.target.checked) next.add(row.id);
                          else next.delete(row.id);
                          return next;
                        })
                      }
                      data-testid="shift-select"
                    />
                  ) : null}
                  <div className="min-w-0 flex-1 space-y-1">
                    <p className="font-medium" data-testid="shift-unit">
                      {row.unit_registry_number ?? unitLabel.get(row.equipment_unit_id) ?? row.equipment_unit_id}
                    </p>
                    <p className="text-sm text-muted-foreground">
                      {projectName.get(row.project_id) ?? ""} · {row.work_date} ·{" "}
                      {operatorName.get(row.operator_id) ?? ""}
                    </p>
                    <p className="text-sm" data-testid="shift-quantities">
                      {t("hours")}: {row.hours}
                      {row.trips !== null ? ` · ${t("trips")}: ${row.trips}` : ""}
                      {Number(row.downtime_hours) > 0 ? ` · ${row.downtime_hours} ч простой` : ""}
                      {liters.has(row.id) ? ` · ${t("fuel")}: ${liters.get(row.id)}` : ""}
                    </p>
                    {errors[row.id] ? (
                      <p role="alert" className="text-sm text-destructive">
                        {errors[row.id]}
                      </p>
                    ) : null}
                  </div>
                </div>
                {canConfirm ? (
                  <div className="grid grid-cols-2 gap-2">
                    <Button
                      type="button"
                      className="h-11"
                      disabled={running}
                      onClick={() => void confirmMany([row.id])}
                      data-testid="shift-confirm"
                    >
                      {t("confirm")}
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      className="h-11"
                      disabled={running}
                      onClick={() => setRejecting(row)}
                      data-testid="shift-reject"
                    >
                      {t("reject")}
                    </Button>
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        </>
      )}

      {canEdit && (confirmed.data ?? []).length > 0 ? (
        <div className="space-y-3">
          <h3 className="text-base font-semibold">{t("confirmedTitle")}</h3>
          <ul className="space-y-3">
            {(confirmed.data ?? []).map((row) => (
              <li
                key={row.id}
                className="flex items-center justify-between gap-3 rounded-xl border p-4"
                data-testid="confirmed-shift-row"
              >
                <div className="min-w-0 text-sm">
                  <p className="font-medium">{row.unit_registry_number ?? unitLabel.get(row.equipment_unit_id) ?? row.equipment_unit_id}</p>
                  <p className="text-muted-foreground">
                    {row.work_date} · {t("hours")}: {row.hours}
                  </p>
                </div>
                <Button
                  type="button"
                  variant="outline"
                  className="h-11 shrink-0"
                  onClick={() => setEditing(row)}
                  data-testid="shift-edit"
                >
                  {t("edit.action")}
                </Button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {canApprove ? (
        <DelegatesPanel
          rows={(delegates.data ?? []).filter((row) => !row.revoked_at)}
          projectName={projectName}
          onRevoke={(id) => void revoke(id)}
          onAdd={() => setDelegating(true)}
        />
      ) : null}

      {rejecting ? (
        <RejectShiftDialog
          shift={rejecting}
          onClose={() => setRejecting(null)}
          onDone={() => setRejecting(null)}
        />
      ) : null}
      {editing ? (
        <EditShiftDialog shift={editing} onClose={() => setEditing(null)} onDone={() => setEditing(null)} />
      ) : null}
      {delegating ? (
        <DelegateDialog
          projects={(projects.data ?? []).map((row) => ({ id: row.id, name: row.name }))}
          onClose={() => setDelegating(false)}
          onDone={() => setDelegating(false)}
        />
      ) : null}
    </section>
  );
}

// Делегаты объектов: кто подтверждает на период. Имена берём из кандидатов объекта (он отдаёт ФИО и роль).
function DelegatesPanel({
  rows,
  projectName,
  onRevoke,
  onAdd,
}: {
  rows: DelegateRow[];
  projectName: Map<string, string>;
  onRevoke: (id: string) => void;
  onAdd: () => void;
}) {
  const t = useTranslations("Approvals.equipment.delegates");
  const projectIds = [...new Set(rows.map((row) => row.project_id))];
  const candidates = useQueries({
    queries: projectIds.map((projectId) => ({
      queryKey: candidatesKey(projectId),
      queryFn: () =>
        api.get<{ user_id: string; full_name: string; role_code: string }[]>(
          `${EQUIPMENT_API}/delegate-candidates`,
          { project_id: projectId },
        ),
      staleTime: 60_000,
    })),
  });
  const names = new Map<string, string>();
  for (const result of candidates) {
    for (const row of result.data ?? []) names.set(row.user_id, row.full_name);
  }
  return (
    <div className="space-y-3" data-testid="delegates-panel">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-base font-semibold">{t("title")}</h3>
        <Button type="button" variant="outline" className="h-11" onClick={onAdd} data-testid="delegate-add">
          {t("add")}
        </Button>
      </div>
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("empty")}</p>
      ) : (
        <ul className="space-y-2">
          {rows.map((row) => (
            <li key={row.id} className="flex items-center justify-between gap-3 rounded-xl border p-3 text-sm" data-testid="delegate-row">
              <div className="min-w-0">
                <p className="font-medium">{names.get(row.delegate_user_id) ?? "…"}</p>
                <p className="text-muted-foreground">
                  {projectName.get(row.project_id) ?? ""} · {t("period", { from: row.date_from, to: row.date_to })}
                </p>
                <p className="text-muted-foreground">{row.reason}</p>
              </div>
              <Button type="button" variant="outline" className="h-11 shrink-0" onClick={() => onRevoke(row.id)} data-testid="delegate-revoke">
                {t("revoke")}
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

