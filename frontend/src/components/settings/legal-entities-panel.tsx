"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useMemo, useState, type FormEvent } from "react";

import { ConfirmModal, FormActions, Modal } from "@/components/settings/dialogs";
import { DataTable, PanelHeader, QueryState } from "@/components/settings/shared";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/auth/form-parts";
import { Input } from "@/components/ui/input";
import { api, newIdempotencyKey } from "@/lib/api";
import { LEGAL_ENTITIES_KEY } from "@/lib/settings/keys";
import type { LegalEntity } from "@/lib/settings/types";

const BASE = "/api/v1/settings/legal-entities";

type Editor = { entity: LegalEntity | null } | null;

export function LegalEntitiesPanel() {
  const t = useTranslations("Settings.legalEntities");
  const common = useTranslations("Settings.common");
  const queryClient = useQueryClient();
  const [editor, setEditor] = useState<Editor>(null);
  const [removing, setRemoving] = useState<LegalEntity | null>(null);

  const query = useQuery({
    queryKey: LEGAL_ENTITIES_KEY,
    queryFn: () => api.get<LegalEntity[]>(BASE),
  });

  const remove = useMutation({
    mutationFn: (entity: LegalEntity) => api.delete(`${BASE}/${entity.id}`),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: LEGAL_ENTITIES_KEY });
      setRemoving(null);
    },
  });

  const columns = useMemo<ColumnDef<LegalEntity, unknown>[]>(
    () => [
      { accessorKey: "name", header: common("name"), cell: ({ row }) => <span className="font-medium">{row.original.name}</span> },
      { accessorKey: "tax_id", header: common("taxId"), cell: ({ row }) => row.original.tax_id ?? common("noTaxId") },
      { accessorKey: "project_count", header: t("projects") },
      {
        id: "actions",
        enableSorting: false,
        header: () => <span className="sr-only">{common("edit")}</span>,
        cell: ({ row }) => (
          <div className="flex justify-end gap-1">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setEditor({ entity: row.original })}
              aria-label={`${common("edit")}: ${row.original.name}`}
            >
              <Pencil data-icon="inline-start" aria-hidden />
              {common("edit")}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="text-destructive"
              onClick={() => {
                remove.reset();
                setRemoving(row.original);
              }}
              aria-label={`${common("delete")}: ${row.original.name}`}
            >
              <Trash2 data-icon="inline-start" aria-hidden />
              {common("delete")}
            </Button>
          </div>
        ),
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps -- remove.reset стабилен, остальное — переводы
    [common, t],
  );

  return (
    <section className="space-y-4">
      <PanelHeader
        title={t("title")}
        description={t("description")}
        action={
          <Button size="lg" onClick={() => setEditor({ entity: null })}>
            <Plus data-icon="inline-start" aria-hidden />
            {t("add")}
          </Button>
        }
      />
      <QueryState loading={query.isPending} error={query.error} onRetry={() => void query.refetch()}>
        <DataTable columns={columns} data={query.data ?? []} getRowId={(row) => row.id} empty={t("empty")} />
      </QueryState>
      {editor ? <LegalEntityModal entity={editor.entity} onClose={() => setEditor(null)} /> : null}
      {removing ? (
        <ConfirmModal
          title={t("deleteTitle")}
          text={t("deleteText", { name: removing.name })}
          confirmLabel={common("delete")}
          pendingLabel={common("deleting")}
          pending={remove.isPending}
          error={remove.error}
          onConfirm={() => remove.mutate(removing)}
          onClose={() => setRemoving(null)}
        />
      ) : null}
    </section>
  );
}

function LegalEntityModal({ entity, onClose }: { entity: LegalEntity | null; onClose: () => void }) {
  const t = useTranslations("Settings.legalEntities");
  const common = useTranslations("Settings.common");
  const queryClient = useQueryClient();
  const [name, setName] = useState(entity?.name ?? "");
  const [taxId, setTaxId] = useState(entity?.tax_id ?? "");
  const [key] = useState(newIdempotencyKey);

  const save = useMutation({
    mutationFn: (body: { name: string; tax_id: string | null }) =>
      entity
        ? api.patch<LegalEntity>(`${BASE}/${entity.id}`, body)
        : api.post<LegalEntity>(BASE, body, key),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: LEGAL_ENTITIES_KEY });
      onClose();
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    save.mutate({ name, tax_id: taxId.trim() || null });
  }

  return (
    <Modal title={entity ? t("editTitle") : t("createTitle")} onClose={onClose}>
      <form method="post" onSubmit={submit} className="space-y-4">
        <Field id="le-name" label={common("name")}>
          <Input id="le-name" required maxLength={200} autoFocus value={name} onChange={(event) => setName(event.target.value)} />
        </Field>
        <Field id="le-tax" label={common("taxId")} hint={common("taxIdHint")}>
          <Input id="le-tax" maxLength={50} value={taxId} onChange={(event) => setTaxId(event.target.value)} aria-describedby="le-tax-hint" />
        </Field>
        <FormActions
          pending={save.isPending}
          submitLabel={entity ? common("save") : common("create")}
          pendingLabel={entity ? common("saving") : common("creating")}
          onCancel={onClose}
          error={save.error}
        />
      </form>
    </Modal>
  );
}
