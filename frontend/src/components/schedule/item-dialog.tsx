"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useCallback, useMemo, useState, type FormEvent } from "react";

import { Field } from "@/components/auth/form-parts";
import { SearchPicker } from "@/components/schedule/search-picker";
import { ConfirmModal, FormActions, Modal, PendingIcon } from "@/components/settings/dialogs";
import { FormMessage } from "@/components/settings/shared";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, newIdempotencyKey } from "@/lib/api";
import { API, WORKS_KEY } from "@/lib/projects/keys";
import type { Work } from "@/lib/projects/types";
import { formatShortDate } from "@/lib/schedule/dates";
import { dependenciesKey } from "@/lib/schedule/keys";
import { invalidateProgress } from "@/lib/schedule/queries";
import type { NodeInfo, ScheduleRow } from "@/lib/schedule/rows";
import type { Dependency, ScheduleItem } from "@/lib/schedule/types";

const MATCHES = 20;

type Props = {
  projectId: string;
  nodes: Map<string, NodeInfo>;
  items: ScheduleRow[];
  dependencies: Dependency[];
  // null - новая строка; presetNodeId подставляется в форму создания (например, из фильтра).
  item: ScheduleRow | null;
  presetNodeId?: string;
  onClose: () => void;
};

function includes(haystack: string, needle: string): boolean {
  return haystack.toLowerCase().includes(needle.toLowerCase());
}

export function ItemDialog({ projectId, nodes, items, dependencies, item, presetNodeId, onClose }: Props) {
  const t = useTranslations("Schedule.dialog");
  const common = useTranslations("Settings.common");
  const queryClient = useQueryClient();
  const [key] = useState(newIdempotencyKey);
  const [node, setNode] = useState<NodeInfo | null>(
    () => (presetNodeId ? (nodes.get(presetNodeId) ?? null) : null),
  );
  const [work, setWork] = useState<Work | null>(null);
  const [start, setStart] = useState(item?.plan_start ?? "");
  const [end, setEnd] = useState(item?.plan_end ?? "");
  const [volume, setVolume] = useState(item?.plan_volume ?? "");
  const [weight, setWeight] = useState(item?.weight ?? "");
  const [removing, setRemoving] = useState(false);

  const catalog = useQuery({
    queryKey: [...WORKS_KEY, "all"],
    queryFn: () => api.get<Work[]>(`${API}/works`, { limit: 500 }),
    enabled: item === null,
    staleTime: 60_000,
  });

  const searchNodes = useCallback(
    (query: string) => {
      if (query === "") return [];
      const found: NodeInfo[] = [];
      for (const info of nodes.values()) {
        if (includes(info.label, query)) found.push(info);
        if (found.length >= MATCHES) break;
      }
      return found;
    },
    [nodes],
  );
  const searchWorks = useCallback(
    (query: string) =>
      query === ""
        ? []
        : (catalog.data ?? [])
            .filter((entry) => includes(`${entry.code} ${entry.name}`, query))
            .slice(0, MATCHES),
    [catalog.data],
  );

  const save = useMutation({
    mutationFn: async () => {
      if (item) {
        return api.patch<ScheduleItem>(`${API}/projects/${projectId}/schedule/${item.id}`, {
          plan_start: start,
          plan_end: end,
          plan_volume: volume,
          weight: weight.trim() === "" ? null : weight,
        });
      }
      return api.post<ScheduleItem>(
        `${API}/projects/${projectId}/schedule`,
        {
          node_id: node?.id,
          work_id: work?.id,
          plan_start: start,
          plan_end: end,
          plan_volume: volume,
          weight: weight.trim() === "" ? null : weight,
        },
        key,
      );
    },
    onSuccess: () => {
      // Таблица обновится сама; окно закрывается сразу, не дожидаясь перезагрузки 4 800 строк.
      void invalidateProgress(queryClient, projectId);
      onClose();
    },
  });

  const remove = useMutation({
    mutationFn: () => api.delete(`${API}/projects/${projectId}/schedule/${item?.id}`),
    onSuccess: () => {
      // Таблица обновится сама; окно закрывается сразу, не дожидаясь перезагрузки 4 800 строк.
      void invalidateProgress(queryClient, projectId);
      onClose();
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!item && (!node || !work)) return;
    save.mutate();
  }

  if (removing && item) {
    return (
      <ConfirmModal
        title={t("deleteTitle")}
        text={t("deleteText", { name: item.work_name })}
        confirmLabel={common("delete")}
        pendingLabel={common("deleting")}
        pending={remove.isPending}
        error={remove.error}
        onConfirm={() => remove.mutate()}
        onClose={() => setRemoving(false)}
      />
    );
  }

  const ready = item !== null || (node !== null && work !== null);

  return (
    <Modal title={item ? t("editTitle") : t("createTitle")} onClose={onClose} wide>
      <form method="post" onSubmit={submit} className="space-y-4">
        {item ? (
          <div className="space-y-0.5 rounded-lg bg-muted/40 px-3 py-2 text-sm">
            <p className="font-medium">
              <span className="font-mono text-xs text-muted-foreground">{item.work_code}</span>{" "}
              {item.work_name}
            </p>
            <p className="text-muted-foreground">{nodes.get(item.node_id)?.label}</p>
          </div>
        ) : (
          <>
            <SearchPicker<NodeInfo>
              id="item-node"
              label={t("node")}
              placeholder={t("nodePlaceholder")}
              selected={node}
              onSelect={setNode}
              search={searchNodes}
              keyOf={(entry) => entry.id}
              render={(entry) => <span className="break-words">{entry.label}</span>}
              emptyText={t("nothingFound")}
            />
            <SearchPicker<Work>
              id="item-work"
              label={t("work")}
              placeholder={t("workPlaceholder")}
              selected={work}
              onSelect={setWork}
              search={searchWorks}
              keyOf={(entry) => entry.id}
              render={(entry) => (
                <span className="break-words">
                  <span className="font-mono text-xs text-muted-foreground">{entry.code}</span>{" "}
                  {entry.name}{" "}
                  <span className="text-muted-foreground">({entry.unit})</span>
                </span>
              )}
              emptyText={t("nothingFound")}
            />
          </>
        )}

        <div className="grid gap-4 sm:grid-cols-2">
          <Field id="item-start" label={t("planStart")}>
            <Input
              id="item-start"
              type="date"
              required
              className="h-10 md:h-9"
              value={start}
              onChange={(event) => setStart(event.target.value)}
            />
          </Field>
          <Field id="item-end" label={t("planEnd")}>
            <Input
              id="item-end"
              type="date"
              required
              min={start || undefined}
              className="h-10 md:h-9"
              value={end}
              onChange={(event) => setEnd(event.target.value)}
            />
          </Field>
          <Field
            id="item-volume"
            label={t("planVolume", { unit: item?.unit ?? work?.unit ?? "" })}
          >
            <Input
              id="item-volume"
              type="number"
              inputMode="decimal"
              required
              min="0.001"
              step="0.001"
              className="h-10 md:h-9"
              value={volume}
              onChange={(event) => setVolume(event.target.value)}
            />
          </Field>
          <Field id="item-weight" label={t("weight")} hint={t("weightHint")}>
            <Input
              id="item-weight"
              type="number"
              inputMode="decimal"
              min="0.001"
              step="0.001"
              className="h-10 md:h-9"
              placeholder={t("weightPlaceholder")}
              value={weight}
              onChange={(event) => setWeight(event.target.value)}
              aria-describedby="item-weight-hint"
            />
          </Field>
        </div>

        {item ? (
          <DependencySections
            projectId={projectId}
            nodes={nodes}
            items={items}
            dependencies={dependencies}
            item={item}
          />
        ) : null}

        <FormActions
          pending={save.isPending}
          submitLabel={item ? common("save") : common("create")}
          pendingLabel={item ? common("saving") : common("creating")}
          onCancel={onClose}
          error={save.error}
          disabled={!ready}
        />
        {!ready ? <p className="text-xs text-muted-foreground">{t("pickFirst")}</p> : null}
        {item ? (
          <div className="border-t pt-3">
            <Button
              type="button"
              variant="destructive"
              size="lg"
              className="h-10"
              onClick={() => {
                remove.reset();
                setRemoving(true);
              }}
            >
              <Trash2 data-icon="inline-start" aria-hidden />
              {t("delete")}
            </Button>
          </div>
        ) : null}
      </form>
    </Modal>
  );
}

function DependencySections({
  projectId,
  nodes,
  items,
  dependencies,
  item,
}: {
  projectId: string;
  nodes: Map<string, NodeInfo>;
  items: ScheduleRow[];
  dependencies: Dependency[];
  item: ScheduleRow;
}) {
  const t = useTranslations("Schedule.dependencies");
  const byId = useMemo(() => new Map(items.map((row) => [row.id, row])), [items]);
  const before = dependencies.filter((dependency) => dependency.successor_id === item.id);
  const after = dependencies.filter((dependency) => dependency.predecessor_id === item.id);
  const linked = useMemo(
    () =>
      new Set([
        item.id,
        ...before.map((dependency) => dependency.predecessor_id),
        ...after.map((dependency) => dependency.successor_id),
      ]),
    [item.id, before, after],
  );

  const search = useCallback(
    (query: string) => {
      if (query === "") return [];
      const found: ScheduleRow[] = [];
      for (const row of items) {
        if (linked.has(row.id)) continue;
        const label = `${row.work_code} ${row.work_name} ${nodes.get(row.node_id)?.label ?? ""}`;
        if (includes(label, query)) found.push(row);
        if (found.length >= MATCHES) break;
      }
      return found;
    },
    [items, nodes, linked],
  );

  return (
    <section aria-labelledby="deps-title" className="space-y-4 border-t pt-4">
      <div className="space-y-1">
        <h3 id="deps-title" className="text-sm font-semibold">
          {t("title")}
        </h3>
        <p className="text-xs text-muted-foreground">{t("hint")}</p>
      </div>
      <DependencyList
        projectId={projectId}
        title={t("predecessors")}
        empty={t("noPredecessors")}
        links={before.map((dependency) => ({
          id: dependency.id,
          row: byId.get(dependency.predecessor_id),
        }))}
        nodes={nodes}
      />
      <AddDependency
        projectId={projectId}
        id="dep-predecessor"
        label={t("addPredecessor")}
        nodes={nodes}
        search={search}
        body={(other) => ({ predecessor_id: other.id, successor_id: item.id })}
      />
      <DependencyList
        projectId={projectId}
        title={t("successors")}
        empty={t("noSuccessors")}
        links={after.map((dependency) => ({
          id: dependency.id,
          row: byId.get(dependency.successor_id),
        }))}
        nodes={nodes}
      />
      <AddDependency
        projectId={projectId}
        id="dep-successor"
        label={t("addSuccessor")}
        nodes={nodes}
        search={search}
        body={(other) => ({ predecessor_id: item.id, successor_id: other.id })}
      />
    </section>
  );
}

function DependencyList({
  projectId,
  title,
  empty,
  links,
  nodes,
}: {
  projectId: string;
  title: string;
  empty: string;
  links: { id: string; row: ScheduleRow | undefined }[];
  nodes: Map<string, NodeInfo>;
}) {
  const t = useTranslations("Schedule.dependencies");
  const queryClient = useQueryClient();
  const detach = useMutation({
    mutationFn: (id: string) => api.delete(`${API}/projects/${projectId}/schedule/dependencies/${id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: dependenciesKey(projectId) }),
  });
  return (
    <div className="space-y-1.5">
      <p className="text-sm font-medium">{title}</p>
      {links.length === 0 ? (
        <p className="text-sm text-muted-foreground">{empty}</p>
      ) : (
        <ul className="divide-y rounded-lg border text-sm">
          {links.map((link) => (
            <li key={link.id} className="flex items-center gap-2 px-2.5 py-1.5">
              <div className="min-w-0 flex-1">
                {link.row ? (
                  <>
                    <p className="truncate">
                      <span className="font-mono text-xs text-muted-foreground">{link.row.work_code}</span>{" "}
                      {link.row.work_name}
                    </p>
                    <p className="truncate text-xs text-muted-foreground">
                      {nodes.get(link.row.node_id)?.label} · {formatShortDate(link.row.plan_start)} —{" "}
                      {formatShortDate(link.row.plan_end)}
                    </p>
                  </>
                ) : (
                  <p className="text-muted-foreground">{t("hidden")}</p>
                )}
              </div>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={detach.isPending}
                onClick={() => detach.mutate(link.id)}
                aria-label={t("remove", { name: link.row?.work_name ?? "" })}
              >
                {t("removeShort")}
              </Button>
            </li>
          ))}
        </ul>
      )}
      <FormMessage error={detach.error} />
    </div>
  );
}

function AddDependency({
  projectId,
  id,
  label,
  nodes,
  search,
  body,
}: {
  projectId: string;
  id: string;
  label: string;
  nodes: Map<string, NodeInfo>;
  search: (query: string) => ScheduleRow[];
  body: (other: ScheduleRow) => { predecessor_id: string; successor_id: string };
}) {
  const t = useTranslations("Schedule.dependencies");
  const queryClient = useQueryClient();
  const [other, setOther] = useState<ScheduleRow | null>(null);
  const attach = useMutation({
    mutationFn: (target: ScheduleRow) =>
      api.post<Dependency>(
        `${API}/projects/${projectId}/schedule/dependencies`,
        body(target),
        newIdempotencyKey(),
      ),
    onSuccess: async () => {
      setOther(null);
      await queryClient.invalidateQueries({ queryKey: dependenciesKey(projectId) });
    },
  });
  return (
    <div className="space-y-2">
      <SearchPicker<ScheduleRow>
        id={id}
        label={label}
        placeholder={t("searchPlaceholder")}
        selected={other}
        onSelect={setOther}
        search={search}
        keyOf={(row) => row.id}
        render={(row) => (
          <span className="break-words">
            <span className="font-mono text-xs text-muted-foreground">{row.work_code}</span>{" "}
            {row.work_name}
            <span className="block text-xs text-muted-foreground">
              {nodes.get(row.node_id)?.label}
            </span>
          </span>
        )}
        emptyText={t("nothingFound")}
      />
      {other ? (
        <Button type="button" size="lg" className="h-10" disabled={attach.isPending} onClick={() => attach.mutate(other)}>
          <PendingIcon pending={attach.isPending} />
          {t("add")}
        </Button>
      ) : null}
      <FormMessage error={attach.error} />
    </div>
  );
}
