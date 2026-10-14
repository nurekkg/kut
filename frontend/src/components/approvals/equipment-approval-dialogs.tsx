"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";

import { Field } from "@/components/auth/form-parts";
import { FormActions, Modal } from "@/components/settings/dialogs";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { api, newIdempotencyKey } from "@/lib/api";
import { EQUIPMENT_KEY } from "@/lib/equipment/keys";
import { EQUIPMENT_API } from "@/lib/equipment/queries";
import { useDelegateCandidates, type ShiftRow } from "@/lib/equipment/shifts";
import { todayIso } from "@/lib/schedule/dates";

const MAX_REASON = 1000;
const FIELD_CLASS =
  "h-12 w-full min-w-0 text-base";

// Отклонение листа требует причины: машинист увидит её и исправит лист (отправит снова).
export function RejectShiftDialog({
  shift,
  onClose,
  onDone,
}: {
  shift: ShiftRow;
  onClose: () => void;
  onDone: () => void;
}) {
  const t = useTranslations("Approvals.equipment");
  const common = useTranslations("Settings.common");
  const queryClient = useQueryClient();
  const [reason, setReason] = useState("");
  const reject = useMutation({
    mutationFn: () =>
      api.post<ShiftRow>(`${EQUIPMENT_API}/shifts/${shift.id}/reject`, { reason: reason.trim() }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: EQUIPMENT_KEY });
      onDone();
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (reason.trim() !== "") reject.mutate();
  }

  return (
    <Modal title={t("rejectTitle")} description={`${shift.work_date} · ${shift.hours} ч`} onClose={onClose}>
      <form method="post" onSubmit={submit} className="space-y-4">
        <Field id="equipment-reject-reason" label={t("rejectReason")}>
          <textarea
            id="equipment-reject-reason"
            required
            autoFocus
            rows={4}
            maxLength={MAX_REASON}
            className="w-full min-w-0 rounded-lg border border-input bg-transparent px-2.5 py-2 text-base outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            data-testid="equipment-reject-reason"
          />
        </Field>
        <FormActions
          pending={reject.isPending}
          submitLabel={t("rejectSubmit")}
          pendingLabel={common("saving")}
          onCancel={onClose}
          error={reject.error}
          disabled={reason.trim() === ""}
        />
      </form>
    </Modal>
  );
}

// Правка подтверждённого листа (техдиректор): сервер проводит лист заново, прежняя проводка сторнируется.
export function EditShiftDialog({
  shift,
  onClose,
  onDone,
}: {
  shift: ShiftRow;
  onClose: () => void;
  onDone: () => void;
}) {
  const t = useTranslations("Approvals.equipment");
  const common = useTranslations("Settings.common");
  const queryClient = useQueryClient();
  const [hours, setHours] = useState(shift.hours);
  const [trips, setTrips] = useState(shift.trips === null ? "" : String(shift.trips));
  const [tonnes, setTonnes] = useState(shift.tonnes ?? "");
  const [downtime, setDowntime] = useState(shift.downtime_hours);
  const [reason, setReason] = useState(shift.downtime_reason ?? "");
  const save = useMutation({
    mutationFn: () =>
      api.patch<ShiftRow>(`${EQUIPMENT_API}/shifts/${shift.id}`, {
        hours,
        trips: shift.trips === null ? null : Number(trips),
        tonnes: shift.tonnes === null ? null : tonnes,
        downtime_hours: downtime,
        downtime_reason: Number(downtime) > 0 ? reason.trim() : null,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: EQUIPMENT_KEY });
      onDone();
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    save.mutate();
  }

  return (
    <Modal title={t("edit.title")} description={t("edit.note")} onClose={onClose}>
      <form method="post" onSubmit={submit} className="space-y-4">
        <Field id="edit-hours" label={t("hours")}>
          <Input id="edit-hours" inputMode="decimal" className={FIELD_CLASS} value={hours} onChange={(e) => setHours(e.target.value)} />
        </Field>
        {shift.trips !== null ? (
          <Field id="edit-trips" label={t("trips")}>
            <Input id="edit-trips" inputMode="numeric" className={FIELD_CLASS} value={trips} onChange={(e) => setTrips(e.target.value)} />
          </Field>
        ) : null}
        {shift.tonnes !== null ? (
          <Field id="edit-tonnes" label={t("tonnes")}>
            <Input id="edit-tonnes" inputMode="decimal" className={FIELD_CLASS} value={tonnes} onChange={(e) => setTonnes(e.target.value)} />
          </Field>
        ) : null}
        <Field id="edit-downtime" label={t("downtime")}>
          <Input id="edit-downtime" inputMode="decimal" className={FIELD_CLASS} value={downtime} onChange={(e) => setDowntime(e.target.value)} />
        </Field>
        {Number(downtime) > 0 ? (
          <Field id="edit-downtime-reason" label={t("downtimeReason")}>
            <Input id="edit-downtime-reason" className={FIELD_CLASS} value={reason} onChange={(e) => setReason(e.target.value)} />
          </Field>
        ) : null}
        <FormActions
          pending={save.isPending}
          submitLabel={t("edit.save")}
          pendingLabel={common("saving")}
          onCancel={onClose}
          error={save.error}
        />
      </form>
    </Modal>
  );
}

// Назначение делегата на период: только с сегодняшнего дня; кандидаты - сотрудники, которые могут
// вести листы на этом объекте (сервер проверит право при подтверждении).
export function DelegateDialog({
  projects,
  onClose,
  onDone,
}: {
  projects: { id: string; name: string }[];
  onClose: () => void;
  onDone: () => void;
}) {
  const t = useTranslations("Approvals.equipment.delegates");
  const common = useTranslations("Settings.common");
  const queryClient = useQueryClient();
  const today = todayIso();
  const [chosenProject, setChosenProject] = useState("");
  const projectId = chosenProject !== "" ? chosenProject : (projects[0]?.id ?? "");
  const [delegateId, setDelegateId] = useState("");
  const [from, setFrom] = useState(today);
  const [to, setTo] = useState(today);
  const [reason, setReason] = useState("");
  const candidates = useDelegateCandidates(projectId, projectId !== "");
  const create = useMutation({
    mutationFn: () =>
      api.post(
        `${EQUIPMENT_API}/delegates`,
        {
          project_id: projectId,
          delegate_user_id: delegateId,
          date_from: from,
          date_to: to,
          reason: reason.trim(),
        },
        newIdempotencyKey(),
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: EQUIPMENT_KEY });
      onDone();
    },
  });

  const valid =
    projectId !== "" && delegateId !== "" && from >= today && to >= from && reason.trim() !== "";

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (valid) create.mutate();
  }

  return (
    <Modal title={t("add")} onClose={onClose}>
      <form method="post" onSubmit={submit} className="space-y-4">
        <Field id="delegate-project" label={t("project")}>
          <NativeSelect
            id="delegate-project"
            className="h-12 text-base"
            value={projectId}
            onChange={(event) => {
              setChosenProject(event.target.value);
              setDelegateId("");
            }}
            data-testid="delegate-project"
          >
            {projects.map((project) => (
              <option key={project.id} value={project.id}>
                {project.name}
              </option>
            ))}
          </NativeSelect>
        </Field>
        <Field id="delegate-user" label={t("candidate")}>
          <NativeSelect
            id="delegate-user"
            className="h-12 text-base"
            value={delegateId}
            onChange={(event) => setDelegateId(event.target.value)}
            data-testid="delegate-user"
          >
            <option value="">—</option>
            {(candidates.data ?? []).map((row) => (
              <option key={row.user_id} value={row.user_id}>
                {row.full_name}
              </option>
            ))}
          </NativeSelect>
          {candidates.isSuccess && (candidates.data ?? []).length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("noCandidates")}</p>
          ) : null}
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field id="delegate-from" label={t("from")}>
            <Input id="delegate-from" type="date" min={today} className="h-12 text-base" value={from} onChange={(event) => setFrom(event.target.value)} data-testid="delegate-from" />
          </Field>
          <Field id="delegate-to" label={t("to")}>
            <Input id="delegate-to" type="date" min={from} className="h-12 text-base" value={to} onChange={(event) => setTo(event.target.value)} data-testid="delegate-to" />
          </Field>
        </div>
        <Field id="delegate-reason" label={t("reason")}>
          <Input id="delegate-reason" className="h-12 text-base" value={reason} onChange={(event) => setReason(event.target.value)} data-testid="delegate-reason" />
        </Field>
        <FormActions
          pending={create.isPending}
          submitLabel={t("save")}
          pendingLabel={common("saving")}
          onCancel={onClose}
          error={create.error}
          disabled={!valid}
        />
      </form>
    </Modal>
  );
}
