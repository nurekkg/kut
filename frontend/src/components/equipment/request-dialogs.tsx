"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { api, newIdempotencyKey } from "@/lib/api";
import { useEquipmentErrorMessage } from "@/lib/equipment/errors";
import { EQUIPMENT_KEY } from "@/lib/equipment/keys";
import {
  EQUIPMENT_API,
  useEquipmentTypes,
  useOperators,
  useProjectNodes,
  useUnits,
  useVisibleProjects,
} from "@/lib/equipment/queries";
import type { AssignmentCheck, DocumentKind, RequestRow } from "@/lib/equipment/types";
import { buildIndex, pathLabel } from "@/lib/projects/tree";
import { addDays, formatShortDate, todayIso } from "@/lib/schedule/dates";

type Done = () => void;

function Field({ id, label, children }: { id: string; label: string; children: ReactNode }) {
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
    </div>
  );
}

function Shell({
  title,
  description,
  onClose,
  onSubmit,
  disabled,
  pending,
  error,
  submitLabel,
  children,
}: {
  title: string;
  description?: string;
  onClose: Done;
  onSubmit: () => void;
  disabled: boolean;
  pending: boolean;
  error: unknown;
  submitLabel: string;
  children: ReactNode;
}) {
  const t = useTranslations("Equipment.forms");
  const message = useEquipmentErrorMessage();
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description ? <DialogDescription>{description}</DialogDescription> : null}
        </DialogHeader>
        <form
          className="grid gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (!disabled) onSubmit();
          }}
        >
          {children}
          {error ? (
            <p role="alert" className="text-sm text-destructive" data-testid="form-error">
              {message(error)}
            </p>
          ) : null}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              {t("cancel")}
            </Button>
            <Button type="submit" disabled={disabled || pending} data-testid="form-submit">
              {pending ? t("saving") : submitLabel}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function useInvalidate() {
  const client = useQueryClient();
  return () => client.invalidateQueries({ queryKey: EQUIPMENT_KEY });
}

// Заявка прораба: объект и узел - в его видимости (RLS), вид техники и даты - из справочника.
export function RequestDialog({ onClose }: { onClose: Done }) {
  const t = useTranslations("Equipment.forms");
  const invalidate = useInvalidate();
  const projects = useVisibleProjects();
  const types = useEquipmentTypes();
  const [key] = useState(newIdempotencyKey);
  const [projectId, setProjectId] = useState("");
  const [nodeId, setNodeId] = useState("");
  const [typeId, setTypeId] = useState("");
  const [from, setFrom] = useState(todayIso);
  const [until, setUntil] = useState(() => addDays(todayIso(), 7));
  const [hours, setHours] = useState("8");
  const nodes = useProjectNodes(projectId || null);
  const labels = buildIndex(nodes.data ?? []);
  const save = useMutation({
    mutationFn: () =>
      api.post(
        `${EQUIPMENT_API}/requests`,
        {
          project_id: projectId,
          node_id: nodeId || null,
          equipment_type_id: typeId,
          date_from: from,
          date_to: until,
          planned_hours_per_day: hours.trim(),
        },
        key,
      ),
    onSuccess: async () => {
      await invalidate();
      onClose();
    },
  });
  const hoursValue = Number(hours);
  const ready =
    Boolean(projectId) && Boolean(typeId) && from <= until && hoursValue > 0 && hoursValue <= 24;
  return (
    <Shell
      title={t("requestTitle")}
      description={t("requestHint")}
      onClose={onClose}
      onSubmit={() => save.mutate()}
      disabled={!ready}
      pending={save.isPending}
      error={save.error}
      submitLabel={t("submitRequest")}
    >
      <Field id="request-project" label={t("project")}>
        <NativeSelect
          id="request-project"
          value={projectId}
          onChange={(event) => {
            setProjectId(event.target.value);
            setNodeId("");
          }}
          required
        >
          <option value="">{t("chooseProject")}</option>
          {(projects.data ?? []).map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
        </NativeSelect>
      </Field>
      <Field id="request-node" label={t("node")}>
        <NativeSelect
          id="request-node"
          value={nodeId}
          onChange={(event) => setNodeId(event.target.value)}
          disabled={!projectId}
        >
          <option value="">{t("noNode")}</option>
          {(nodes.data ?? []).map((node) => (
            <option key={node.id} value={node.id}>
              {pathLabel(labels, node)}
            </option>
          ))}
        </NativeSelect>
      </Field>
      <Field id="request-type" label={t("equipmentType")}>
        <NativeSelect id="request-type" value={typeId} onChange={(event) => setTypeId(event.target.value)} required>
          <option value="">{t("chooseType")}</option>
          {(types.data ?? []).map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
        </NativeSelect>
      </Field>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field id="request-from" label={t("dateFrom")}>
          <Input id="request-from" type="date" value={from} onChange={(event) => setFrom(event.target.value)} required />
        </Field>
        <Field id="request-until" label={t("dateTo")}>
          <Input id="request-until" type="date" value={until} onChange={(event) => setUntil(event.target.value)} required />
        </Field>
        <Field id="request-hours" label={t("hoursPerDay")}>
          <Input
            id="request-hours"
            inputMode="decimal"
            value={hours}
            onChange={(event) => setHours(event.target.value)}
            required
          />
        </Field>
      </div>
    </Shell>
  );
}

// Назначение начальником: подбор единицы и проверка при выборе. Проверка - тот же путь, что
// запись, только транзакция откатывается: причина отказа - код правила из БД.
export function AssignDialog({
  request,
  projectName,
  nodeLabel,
  onClose,
}: {
  request: RequestRow;
  projectName: string;
  nodeLabel: string;
  onClose: Done;
}) {
  const t = useTranslations("Equipment.forms");
  const statusText = useTranslations("Equipment.status");
  const kindText = useTranslations("Equipment.documentKind");
  const message = useEquipmentErrorMessage();
  const invalidate = useInvalidate();
  const units = useUnits({ typeId: request.equipment_type_id });
  const operators = useOperators();
  const [key] = useState(newIdempotencyKey);
  const [unitId, setUnitId] = useState("");
  const [operatorId, setOperatorId] = useState("");
  const [from, setFrom] = useState(request.date_from);
  const [until, setUntil] = useState(request.date_to);
  const [hours, setHours] = useState(request.planned_hours_per_day);
  const body = {
    equipment_unit_id: unitId,
    project_id: request.project_id,
    request_id: request.id,
    node_id: request.node_id,
    schedule_item_id: request.schedule_item_id,
    date_from: from,
    date_to: until,
    planned_hours_per_day: hours.trim(),
    operator_id: operatorId || null,
  };
  const check = useQuery({
    queryKey: [...EQUIPMENT_KEY, "check", body],
    queryFn: () => api.post<AssignmentCheck>(`${EQUIPMENT_API}/assignments/check`, body),
    enabled: Boolean(unitId) && from <= until,
    retry: false,
  });
  const assign = useMutation({
    mutationFn: () => api.post(`${EQUIPMENT_API}/assignments`, body, key),
    onSuccess: async () => {
      await invalidate();
      onClose();
    },
  });
  const ready = check.isSuccess && !assign.isPending;
  return (
    <Shell
      title={t("assignTitle")}
      description={t("assignHint", { project: projectName, node: nodeLabel })}
      onClose={onClose}
      onSubmit={() => assign.mutate()}
      disabled={!ready}
      pending={assign.isPending}
      error={assign.error}
      submitLabel={t("submitAssign")}
    >
      <Field id="assign-unit" label={t("unit")}>
        <NativeSelect id="assign-unit" value={unitId} onChange={(event) => setUnitId(event.target.value)} required>
          <option value="">{t("chooseUnit")}</option>
          {(units.data ?? []).map((item) => (
            <option key={item.id} value={item.id}>
              {`${item.registry_number} — ${statusText(item.status)}`}
            </option>
          ))}
        </NativeSelect>
      </Field>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field id="assign-from" label={t("dateFrom")}>
          <Input id="assign-from" type="date" value={from} onChange={(event) => setFrom(event.target.value)} required />
        </Field>
        <Field id="assign-until" label={t("dateTo")}>
          <Input id="assign-until" type="date" value={until} onChange={(event) => setUntil(event.target.value)} required />
        </Field>
        <Field id="assign-hours" label={t("hoursPerDay")}>
          <Input id="assign-hours" inputMode="decimal" value={hours} onChange={(event) => setHours(event.target.value)} required />
        </Field>
      </div>
      <Field id="assign-operator" label={t("operator")}>
        <NativeSelect id="assign-operator" value={operatorId} onChange={(event) => setOperatorId(event.target.value)}>
          <option value="">{t("noOperator")}</option>
          {(operators.data ?? []).map((item) => (
            <option key={item.id} value={item.id}>
              {item.full_name}
            </option>
          ))}
        </NativeSelect>
      </Field>
      <div className="rounded-lg border p-3 text-sm" aria-live="polite" data-testid="assign-check">
        {!unitId ? <p className="text-muted-foreground">{t("pickUnitHint")}</p> : null}
        {unitId && check.isPending ? <p className="text-muted-foreground">{t("checking")}</p> : null}
        {unitId && check.isError ? (
          <p className="text-destructive" data-testid="assign-reason">
            {t("notFit", { reason: message(check.error) })}
          </p>
        ) : null}
        {check.isSuccess ? (
          <div className="flex flex-col gap-1 text-emerald-800 dark:text-emerald-300" data-testid="assign-fits">
            <p>{t("fits")}</p>
            {check.data.warnings.map((warning) => (
              <p key={`${warning.kind}-${warning.valid_until}`} className="text-amber-900 dark:text-amber-300">
                {t("documentWarning", {
                  kind: kindText(warning.kind as DocumentKind),
                  date: formatShortDate(warning.valid_until),
                })}
              </p>
            ))}
          </div>
        ) : null}
      </div>
    </Shell>
  );
}

export function RejectDialog({ request, onClose }: { request: RequestRow; onClose: Done }) {
  const t = useTranslations("Equipment.forms");
  const invalidate = useInvalidate();
  const [reason, setReason] = useState("");
  const save = useMutation({
    mutationFn: () => api.post(`${EQUIPMENT_API}/requests/${request.id}/reject`, { reason: reason.trim() }),
    onSuccess: async () => {
      await invalidate();
      onClose();
    },
  });
  return (
    <Shell
      title={t("rejectTitle")}
      description={t("rejectHint")}
      onClose={onClose}
      onSubmit={() => save.mutate()}
      disabled={!reason.trim()}
      pending={save.isPending}
      error={save.error}
      submitLabel={t("submitReject")}
    >
      <Field id="reject-reason" label={t("reason")}>
        <textarea
          id="reject-reason"
          rows={3}
          maxLength={500}
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-base outline-none focus-visible:ring-3 focus-visible:ring-ring/50 md:text-sm"
          required
        />
      </Field>
    </Shell>
  );
}
