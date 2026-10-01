"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useMemo, useState, type FormEvent } from "react";

import { Field } from "@/components/auth/form-parts";
import { ConfirmModal, FormActions, Modal } from "@/components/settings/dialogs";
import { DataTable, PanelHeader, QueryState, useDebounced } from "@/components/settings/shared";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { api, newIdempotencyKey } from "@/lib/api";
import { COUNTERPARTIES_KEY } from "@/lib/settings/keys";
import {
  COUNTERPARTY_TYPES,
  type Counterparty,
  type CounterpartyType,
} from "@/lib/settings/types";

const BASE = "/api/v1/settings/counterparties";

function isType(value: string): value is CounterpartyType {
  return (COUNTERPARTY_TYPES as readonly string[]).includes(value);
}

type Editor = { entity: Counterparty | null } | null;

export function CounterpartiesPanel() {
  const t = useTranslations("Settings.counterparties");
  const common = useTranslations("Settings.common");
  const queryClient = useQueryClient();
  const [kind, setKind] = useState("");
  const [search, setSearch] = useState("");
  const q = useDebounced(search.trim());
  const [editor, setEditor] = useState<Editor>(null);
  const [removing, setRemoving] = useState<Counterparty | null>(null);

  const query = useQuery({
    queryKey: [...COUNTERPARTIES_KEY, { kind, q }],
    queryFn: () => api.get<Counterparty[]>(BASE, { type: kind, q, limit: 500 }),
  });

  const remove = useMutation({
    mutationFn: (entity: Counterparty) => api.delete(`${BASE}/${entity.id}`),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: COUNTERPARTIES_KEY });
      setRemoving(null);
    },
  });

  const columns = useMemo<ColumnDef<Counterparty, unknown>[]>(
    () => [
      { accessorKey: "name", header: common("name"), cell: ({ row }) => <span className="font-medium">{row.original.name}</span> },
      { accessorKey: "tax_id", header: common("taxId"), cell: ({ row }) => row.original.tax_id ?? common("noTaxId") },
      {
        id: "types",
        enableSorting: false,
        header: t("types"),
        cell: ({ row }) => (
          <div className="flex flex-wrap gap-1">
            {row.original.types.map((type) => (
              <Badge key={type} variant="secondary">
                {isType(type) ? t(`type.${type}`) : type}
              </Badge>
            ))}
          </div>
        ),
      },
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
      <div className="flex flex-wrap items-end gap-3">
        <div className="w-48 space-y-1.5">
          <Label htmlFor="cp-kind">{t("typeFilter")}</Label>
          <NativeSelect id="cp-kind" value={kind} onChange={(event) => setKind(event.target.value)}>
            <option value="">{common("all")}</option>
            {COUNTERPARTY_TYPES.map((type) => (
              <option key={type} value={type}>
                {t(`type.${type}`)}
              </option>
            ))}
          </NativeSelect>
        </div>
        <div className="w-64 space-y-1.5">
          <Label htmlFor="cp-search">{t("searchLabel")}</Label>
          <Input
            id="cp-search"
            type="search"
            placeholder={t("searchPlaceholder")}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
      </div>
      <QueryState loading={query.isPending} error={query.error} onRetry={() => void query.refetch()}>
        <DataTable columns={columns} data={query.data ?? []} getRowId={(row) => row.id} empty={t("empty")} />
      </QueryState>
      {editor ? <CounterpartyModal entity={editor.entity} onClose={() => setEditor(null)} /> : null}
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

function CounterpartyModal({ entity, onClose }: { entity: Counterparty | null; onClose: () => void }) {
  const t = useTranslations("Settings.counterparties");
  const common = useTranslations("Settings.common");
  const queryClient = useQueryClient();
  const [name, setName] = useState(entity?.name ?? "");
  const [taxId, setTaxId] = useState(entity?.tax_id ?? "");
  const [types, setTypes] = useState<string[]>(entity?.types ?? []);
  const [key] = useState(newIdempotencyKey);

  const save = useMutation({
    mutationFn: (body: { name: string; tax_id: string | null; types: string[] }) =>
      entity
        ? api.patch<Counterparty>(`${BASE}/${entity.id}`, body)
        : api.post<Counterparty>(BASE, body, key),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: COUNTERPARTIES_KEY });
      onClose();
    },
  });

  function toggle(type: string) {
    setTypes((current) => (current.includes(type) ? current.filter((item) => item !== type) : [...current, type]));
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (types.length === 0) return;
    save.mutate({ name, tax_id: taxId.trim() || null, types });
  }

  return (
    <Modal title={entity ? t("editTitle") : t("createTitle")} onClose={onClose}>
      <form method="post" onSubmit={submit} className="space-y-4">
        <Field id="cp-name" label={common("name")}>
          <Input id="cp-name" required maxLength={200} autoFocus value={name} onChange={(event) => setName(event.target.value)} />
        </Field>
        <Field id="cp-tax" label={common("taxId")} hint={common("taxIdHint")}>
          <Input id="cp-tax" maxLength={50} value={taxId} onChange={(event) => setTaxId(event.target.value)} aria-describedby="cp-tax-hint" />
        </Field>
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium">{t("types")}</legend>
          <div className="grid grid-cols-2 gap-2">
            {COUNTERPARTY_TYPES.map((type) => (
              <label key={type} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  className="size-4 accent-primary"
                  checked={types.includes(type)}
                  onChange={() => toggle(type)}
                />
                {t(`type.${type}`)}
              </label>
            ))}
          </div>
          <p className={types.length === 0 ? "text-sm text-destructive" : "text-sm text-muted-foreground"}>
            {t("typesHint")}
          </p>
        </fieldset>
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
