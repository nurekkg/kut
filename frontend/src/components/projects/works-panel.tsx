"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useMemo, useState, type FormEvent } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { Field } from "@/components/auth/form-parts";
import { ConfirmModal, FormActions, Modal } from "@/components/settings/dialogs";
import { DataTable, PanelHeader, QueryState, useDebounced } from "@/components/settings/shared";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { api, newIdempotencyKey } from "@/lib/api";
import { API, WORK_CATEGORIES_KEY, WORKS_KEY } from "@/lib/projects/keys";
import type { Work } from "@/lib/projects/types";

type Editor = { work: Work | null } | null;

export function WorksPanel() {
  const t = useTranslations("Works");
  const common = useTranslations("Settings.common");
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const canWrite = user?.permissions.includes("works:write") ?? false;
  const [category, setCategory] = useState("");
  const [search, setSearch] = useState("");
  const q = useDebounced(search.trim());
  const [editor, setEditor] = useState<Editor>(null);
  const [removing, setRemoving] = useState<Work | null>(null);

  const works = useQuery({
    queryKey: [...WORKS_KEY, { category, q }],
    queryFn: () => api.get<Work[]>(`${API}/works`, { category, q, limit: 500 }),
  });
  const categories = useQuery({
    queryKey: WORK_CATEGORIES_KEY,
    queryFn: () => api.get<string[]>(`${API}/works/categories`),
  });

  const remove = useMutation({
    mutationFn: (work: Work) => api.delete(`${API}/works/${work.id}`),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: WORKS_KEY });
      setRemoving(null);
    },
  });

  const columns = useMemo<ColumnDef<Work, unknown>[]>(
    () => [
      { accessorKey: "code", header: t("code"), cell: ({ row }) => <span className="font-mono text-xs">{row.original.code}</span> },
      { accessorKey: "name", header: t("name"), cell: ({ row }) => <span className="font-medium">{row.original.name}</span> },
      { accessorKey: "unit", header: t("unit") },
      { accessorKey: "category", header: t("category") },
      ...(canWrite
        ? [
            {
              id: "actions",
              enableSorting: false,
              header: () => <span className="sr-only">{common("edit")}</span>,
              cell: ({ row }) => (
                <div className="flex justify-end gap-1">
                  <Button variant="ghost" size="sm" onClick={() => setEditor({ work: row.original })} aria-label={`${common("edit")}: ${row.original.name}`}>
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
            } satisfies ColumnDef<Work, unknown>,
          ]
        : []),
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps -- remove.reset стабилен, остальное — переводы
    [common, t, canWrite],
  );

  return (
    <section className="space-y-4">
      <PanelHeader
        title={t("title")}
        description={t("subtitle")}
        action={
          canWrite ? (
            <Button size="lg" onClick={() => setEditor({ work: null })}>
              <Plus data-icon="inline-start" aria-hidden />
              {t("add")}
            </Button>
          ) : undefined
        }
      />
      {canWrite ? null : <p className="text-sm text-muted-foreground">{t("readOnly")}</p>}
      <div className="flex flex-wrap items-end gap-3">
        <div className="w-full space-y-1.5 sm:w-64">
          <Label htmlFor="w-category">{t("category")}</Label>
          <NativeSelect id="w-category" className="h-10 md:h-9" value={category} onChange={(event) => setCategory(event.target.value)}>
            <option value="">{t("allCategories")}</option>
            {(categories.data ?? []).map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </NativeSelect>
        </div>
        <div className="w-full space-y-1.5 sm:w-64">
          <Label htmlFor="w-search">{t("search")}</Label>
          <Input id="w-search" type="search" className="h-10 md:h-9" placeholder={t("searchPlaceholder")} value={search} onChange={(event) => setSearch(event.target.value)} />
        </div>
      </div>
      <QueryState loading={works.isPending} error={works.error} onRetry={() => void works.refetch()}>
        <DataTable columns={columns} data={works.data ?? []} getRowId={(row) => row.id} empty={t("empty")} />
      </QueryState>
      {editor ? <WorkModal work={editor.work} categories={categories.data ?? []} onClose={() => setEditor(null)} /> : null}
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

function WorkModal({
  work,
  categories,
  onClose,
}: {
  work: Work | null;
  categories: string[];
  onClose: () => void;
}) {
  const t = useTranslations("Works");
  const common = useTranslations("Settings.common");
  const queryClient = useQueryClient();
  const [code, setCode] = useState(work?.code ?? "");
  const [name, setName] = useState(work?.name ?? "");
  const [unit, setUnit] = useState(work?.unit ?? "");
  const [category, setCategory] = useState(work?.category ?? "");
  const [key] = useState(newIdempotencyKey);

  const save = useMutation({
    mutationFn: (body: Record<string, string>) =>
      work ? api.patch<Work>(`${API}/works/${work.id}`, body) : api.post<Work>(`${API}/works`, body, key),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: WORKS_KEY });
      onClose();
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    save.mutate({ code, name, unit, category });
  }

  return (
    <Modal title={work ? t("editTitle") : t("createTitle")} onClose={onClose}>
      <form method="post" onSubmit={submit} className="space-y-4">
        <Field id="w-code" label={t("code")}>
          <Input id="w-code" required autoFocus maxLength={50} className="h-10 md:h-9" value={code} onChange={(event) => setCode(event.target.value)} />
        </Field>
        <Field id="w-name" label={t("name")}>
          <Input id="w-name" required maxLength={300} className="h-10 md:h-9" value={name} onChange={(event) => setName(event.target.value)} />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field id="w-unit" label={t("unit")} hint={t("unitHint")}>
            <Input id="w-unit" required maxLength={30} className="h-10 md:h-9" value={unit} onChange={(event) => setUnit(event.target.value)} aria-describedby="w-unit-hint" />
          </Field>
          <Field id="w-cat" label={t("category")}>
            <Input id="w-cat" required maxLength={100} list="w-categories" className="h-10 md:h-9" value={category} onChange={(event) => setCategory(event.target.value)} />
            <datalist id="w-categories">
              {categories.map((item) => (
                <option key={item} value={item} />
              ))}
            </datalist>
          </Field>
        </div>
        <FormActions
          pending={save.isPending}
          submitLabel={work ? common("save") : common("create")}
          pendingLabel={work ? common("saving") : common("creating")}
          onCancel={onClose}
          error={save.error}
        />
      </form>
    </Modal>
  );
}
