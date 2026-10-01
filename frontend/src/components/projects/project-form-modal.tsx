"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";

import { Field } from "@/components/auth/form-parts";
import { FormActions, Modal } from "@/components/settings/dialogs";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { api, newIdempotencyKey } from "@/lib/api";
import {
  API,
  CUSTOMERS_REF_KEY,
  LEGAL_ENTITIES_REF_KEY,
  PROJECTS_KEY,
  projectKey,
} from "@/lib/projects/keys";
import {
  LIFECYCLE_STAGES,
  PROJECT_STATUSES,
  type NamedRef,
  type Project,
} from "@/lib/projects/types";

type Props = {
  project: Project | null;
  onClose: () => void;
  onSaved: (project: Project) => void;
};

const field = "h-10 md:h-9";

// Пустое поле - null (очистка), число уходит строкой: сервер хранит деньги и координаты точно.
function textOrNull(value: string): string | null {
  const trimmed = value.trim().replace(",", ".");
  return trimmed === "" ? null : trimmed;
}

export function ProjectFormModal({ project, onClose, onSaved }: Props) {
  const t = useTranslations("ProjectForm");
  const common = useTranslations("Settings.common");
  const stages = useTranslations("Projects.stage");
  const statuses = useTranslations("Projects.status");
  const queryClient = useQueryClient();
  const [name, setName] = useState(project?.name ?? "");
  const [entity, setEntity] = useState(project?.legal_entity_id ?? "");
  const [status, setStatus] = useState(project?.status ?? "active");
  const [stage, setStage] = useState(project?.lifecycle_stage ?? "land_purchase");
  const [start, setStart] = useState(project?.planned_start ?? "");
  const [end, setEnd] = useState(project?.planned_end ?? "");
  const [budget, setBudget] = useState(project?.planned_budget ?? "");
  const [customer, setCustomer] = useState(project?.customer_id ?? "");
  const [address, setAddress] = useState(project?.address ?? "");
  const [latitude, setLatitude] = useState(project?.latitude ?? "");
  const [longitude, setLongitude] = useState(project?.longitude ?? "");
  const [warranty, setWarranty] = useState(project?.warranty_until ?? "");
  const [key] = useState(newIdempotencyKey);

  const entities = useQuery({
    queryKey: LEGAL_ENTITIES_REF_KEY,
    queryFn: () => api.get<NamedRef[]>(`${API}/legal-entities`),
    enabled: project === null,
  });
  const customers = useQuery({
    queryKey: CUSTOMERS_REF_KEY,
    queryFn: () => api.get<NamedRef[]>(`${API}/customers`),
  });

  const save = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      project
        ? api.patch<Project>(`${API}/projects/${project.id}`, body)
        : api.post<Project>(`${API}/projects`, { ...body, legal_entity_id: entity }, key),
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries({ queryKey: PROJECTS_KEY });
      await queryClient.invalidateQueries({ queryKey: projectKey(saved.id) });
      onSaved(saved);
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    save.mutate({
      name,
      status,
      lifecycle_stage: stage,
      planned_start: start || null,
      planned_end: end || null,
      planned_budget: textOrNull(budget),
      customer_id: customer || null,
      address: address.trim() || null,
      latitude: textOrNull(latitude),
      longitude: textOrNull(longitude),
      warranty_until: warranty || null,
    });
  }

  return (
    <Modal
      title={project ? t("editTitle") : t("createTitle")}
      onClose={onClose}
      wide
    >
      <form method="post" onSubmit={submit} className="space-y-4">
        <Field id="pf-name" label={t("name")}>
          <Input id="pf-name" required autoFocus maxLength={200} className={field} value={name} onChange={(event) => setName(event.target.value)} />
        </Field>
        {project === null ? (
          <Field id="pf-entity" label={t("legalEntity")} hint={t("legalEntityHint")}>
            <NativeSelect id="pf-entity" required className={field} value={entity} onChange={(event) => setEntity(event.target.value)} aria-describedby="pf-entity-hint">
              <option value="" disabled>
                {t("choose")}
              </option>
              {(entities.data ?? []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </NativeSelect>
          </Field>
        ) : null}
        <div className="grid gap-4 sm:grid-cols-2">
          <Field id="pf-stage" label={t("stage")}>
            <NativeSelect id="pf-stage" className={field} value={stage} onChange={(event) => setStage(event.target.value)}>
              {LIFECYCLE_STAGES.map((item) => (
                <option key={item} value={item}>
                  {stages(item)}
                </option>
              ))}
            </NativeSelect>
          </Field>
          <Field id="pf-status" label={t("status")}>
            <NativeSelect id="pf-status" className={field} value={status} onChange={(event) => setStatus(event.target.value)}>
              {PROJECT_STATUSES.map((item) => (
                <option key={item} value={item}>
                  {statuses(item)}
                </option>
              ))}
            </NativeSelect>
          </Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field id="pf-start" label={t("plannedStart")}>
            <Input id="pf-start" type="date" className={field} value={start} onChange={(event) => setStart(event.target.value)} />
          </Field>
          <Field id="pf-end" label={t("plannedEnd")}>
            <Input id="pf-end" type="date" className={field} value={end} min={start || undefined} onChange={(event) => setEnd(event.target.value)} />
          </Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field id="pf-budget" label={t("budget")} hint={t("budgetHint")}>
            <Input id="pf-budget" inputMode="decimal" className={field} value={budget} onChange={(event) => setBudget(event.target.value)} aria-describedby="pf-budget-hint" />
          </Field>
          <Field id="pf-customer" label={t("customer")}>
            <NativeSelect id="pf-customer" className={field} value={customer} onChange={(event) => setCustomer(event.target.value)}>
              <option value="">{t("noCustomer")}</option>
              {(customers.data ?? []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </NativeSelect>
          </Field>
        </div>
        <Field id="pf-address" label={t("address")}>
          <Input id="pf-address" maxLength={500} className={field} value={address} onChange={(event) => setAddress(event.target.value)} />
        </Field>
        <div className="grid gap-4 sm:grid-cols-3">
          <Field id="pf-lat" label={t("latitude")}>
            <Input id="pf-lat" inputMode="decimal" className={field} value={latitude} onChange={(event) => setLatitude(event.target.value)} />
          </Field>
          <Field id="pf-lon" label={t("longitude")}>
            <Input id="pf-lon" inputMode="decimal" className={field} value={longitude} onChange={(event) => setLongitude(event.target.value)} />
          </Field>
          <Field id="pf-warranty" label={t("warrantyUntil")}>
            <Input id="pf-warranty" type="date" className={field} value={warranty} onChange={(event) => setWarranty(event.target.value)} />
          </Field>
        </div>
        <p className="-mt-2 text-sm text-muted-foreground">{t("coordinatesHint")}</p>
        <FormActions
          pending={save.isPending}
          submitLabel={project ? common("save") : common("create")}
          pendingLabel={project ? common("saving") : common("creating")}
          onCancel={onClose}
          error={save.error}
        />
      </form>
    </Modal>
  );
}
