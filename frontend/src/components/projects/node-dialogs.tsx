"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useMemo, useState, type FormEvent } from "react";

import { Field } from "@/components/auth/form-parts";
import { ConfirmModal, FormActions, Modal, PendingIcon } from "@/components/settings/dialogs";
import { FormMessage, useDebounced } from "@/components/settings/shared";
import { Button } from "@/components/ui/button";
import { DialogFooter } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { api, newIdempotencyKey } from "@/lib/api";
import { API, nodeKey, treeKey, USERS_REF_KEY } from "@/lib/projects/keys";
import { pathLabel, type TreeIndex } from "@/lib/projects/tree";
import {
  allowedChildren,
  isNodeType,
  type NodeDetail,
  type NodeType,
  type TreeNode,
  type UserRef,
} from "@/lib/projects/types";

function useNodeInvalidation(projectId: string) {
  const queryClient = useQueryClient();
  return async (nodeId?: string) => {
    await queryClient.invalidateQueries({ queryKey: treeKey(projectId) });
    if (nodeId) await queryClient.invalidateQueries({ queryKey: nodeKey(projectId, nodeId) });
  };
}

function parentType(index: TreeIndex, parentId: string | null): NodeType | null {
  const type = parentId ? index.byId.get(parentId)?.type : undefined;
  return type && isNodeType(type) ? type : null;
}

type FormProps = {
  projectId: string;
  index: TreeIndex;
  onClose: () => void;
  onSaved: (nodeId: string) => void;
} & (
  | { mode: "create"; parent: TreeNode | null }
  | { mode: "edit"; node: NodeDetail }
);

export function NodeFormModal(props: FormProps) {
  const { projectId, index, onClose, onSaved } = props;
  const t = useTranslations("NodeForm");
  const common = useTranslations("Settings.common");
  const types = useTranslations("NodeType");
  const invalidate = useNodeInvalidation(projectId);
  const edit = props.mode === "edit" ? props.node : null;
  const parentId = props.mode === "create" ? (props.parent?.id ?? null) : (props.node.parent_id ?? null);
  const options = allowedChildren(parentType(index, parentId));
  const [type, setType] = useState<NodeType>(() => {
    if (edit && isNodeType(edit.type)) return edit.type;
    return options[0] ?? "building";
  });
  const [name, setName] = useState(edit?.name ?? "");
  const [code, setCode] = useState(edit?.code ?? "");
  const [start, setStart] = useState(edit?.planned_start ?? "");
  const [end, setEnd] = useState(edit?.planned_end ?? "");
  const [foreman, setForeman] = useState(edit?.foreman_user_id ?? "");
  const [master, setMaster] = useState(edit?.master_user_id ?? "");
  const [key] = useState(newIdempotencyKey);

  const users = useQuery({
    queryKey: USERS_REF_KEY,
    queryFn: () => api.get<UserRef[]>(`${API}/users`),
  });

  const save = useMutation({
    mutationFn: async (body: Record<string, unknown>) => {
      if (props.mode === "edit") {
        return api.patch<NodeDetail>(`${API}/projects/${projectId}/nodes/${props.node.id}`, body);
      }
      return api.post<NodeDetail>(
        `${API}/projects/${projectId}/nodes`,
        { ...body, parent_id: parentId },
        key,
      );
    },
    onSuccess: async (saved) => {
      await invalidate(saved.id);
      onSaved(saved.id);
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    save.mutate({
      type,
      name,
      code: code.trim() || null,
      planned_start: start || null,
      planned_end: end || null,
      foreman_user_id: foreman || null,
      master_user_id: master || null,
    });
  }

  const people = (role: string) => (users.data ?? []).filter((user) => user.role === role);
  const typeChoices = isNodeType(type) && !options.includes(type) ? [type, ...options] : options;
  const context =
    props.mode === "create"
      ? props.parent
        ? t("createUnder", { name: props.parent.name })
        : t("createRoot")
      : undefined;

  if (typeChoices.length === 0) {
    return (
      <Modal title={t("createTitle")} description={context} onClose={onClose}>
        <p className="text-sm text-muted-foreground">{t("noTypes")}</p>
        <DialogFooter>
          <Button type="button" variant="outline" size="lg" onClick={onClose}>
            {common("close")}
          </Button>
        </DialogFooter>
      </Modal>
    );
  }

  return (
    <Modal
      title={props.mode === "create" ? t("createTitle") : t("editTitle")}
      description={context}
      onClose={onClose}
    >
      <form method="post" onSubmit={submit} className="space-y-4">
        <Field id="node-type" label={t("type")}>
          <NativeSelect
            id="node-type"
            className="h-10 md:h-9"
            value={type}
            onChange={(event) => {
              const next = event.target.value;
              if (isNodeType(next)) setType(next);
            }}
          >
            {typeChoices.map((item) => (
              <option key={item} value={item}>
                {types(item)}
              </option>
            ))}
          </NativeSelect>
        </Field>
        <Field id="node-name" label={t("name")}>
          <Input id="node-name" required autoFocus maxLength={200} className="h-10 md:h-9" value={name} onChange={(event) => setName(event.target.value)} />
        </Field>
        <Field id="node-code" label={t("code")} hint={t("codeHint")}>
          <Input id="node-code" maxLength={50} className="h-10 md:h-9" value={code} onChange={(event) => setCode(event.target.value)} aria-describedby="node-code-hint" />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field id="node-start" label={t("plannedStart")}>
            <Input id="node-start" type="date" className="h-10 md:h-9" value={start} onChange={(event) => setStart(event.target.value)} />
          </Field>
          <Field id="node-end" label={t("plannedEnd")}>
            <Input id="node-end" type="date" className="h-10 md:h-9" value={end} min={start || undefined} onChange={(event) => setEnd(event.target.value)} />
          </Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field id="node-foreman" label={t("foreman")}>
            <NativeSelect id="node-foreman" className="h-10 md:h-9" value={foreman} onChange={(event) => setForeman(event.target.value)}>
              <option value="">{t("none")}</option>
              {people("foreman").map((user) => (
                <option key={user.id} value={user.id}>
                  {user.full_name}
                </option>
              ))}
            </NativeSelect>
          </Field>
          <Field id="node-master" label={t("master")}>
            <NativeSelect id="node-master" className="h-10 md:h-9" value={master} onChange={(event) => setMaster(event.target.value)}>
              <option value="">{t("none")}</option>
              {people("master").map((user) => (
                <option key={user.id} value={user.id}>
                  {user.full_name}
                </option>
              ))}
            </NativeSelect>
          </Field>
        </div>
        <p className="-mt-2 text-sm text-muted-foreground">{t("responsibleHint")}</p>
        <FormActions
          pending={save.isPending}
          submitLabel={props.mode === "create" ? common("create") : common("save")}
          pendingLabel={props.mode === "create" ? common("creating") : common("saving")}
          onCancel={onClose}
          error={save.error}
        />
      </form>
    </Modal>
  );
}

const MAX_CANDIDATES = 50;

export function MoveNodeModal({
  projectId,
  node,
  index,
  onClose,
  onMoved,
}: {
  projectId: string;
  node: TreeNode;
  index: TreeIndex;
  onClose: () => void;
  onMoved: (nodeId: string) => void;
}) {
  const t = useTranslations("NodeMove");
  const common = useTranslations("Settings.common");
  const invalidate = useNodeInvalidation(projectId);
  const [query, setQuery] = useState("");
  const debounced = useDebounced(query.trim().toLowerCase(), 200);
  const [picked, setPicked] = useState<TreeNode | "root" | null>(null);
  const [position, setPosition] = useState("");
  const kind = isNodeType(node.type) ? node.type : "building";

  const candidates = useMemo(() => {
    const found: TreeNode[] = [];
    for (const candidate of index.byId.values()) {
      if (candidate.id === node.id || candidate.path.includes(node.id)) continue;
      if (!isNodeType(candidate.type) || !allowedChildren(candidate.type).includes(kind)) continue;
      if (debounced && !`${candidate.name} ${candidate.code ?? ""}`.toLowerCase().includes(debounced)) continue;
      found.push(candidate);
    }
    found.sort((a, b) => pathLabel(index, a).localeCompare(pathLabel(index, b), "ru", { numeric: true }));
    return found;
  }, [index, node, kind, debounced]);
  const canBeRoot = allowedChildren(null).includes(kind);

  const move = useMutation({
    mutationFn: () => {
      const number = Number.parseInt(position, 10);
      return api.post<NodeDetail>(`${API}/projects/${projectId}/nodes/${node.id}/move`, {
        parent_id: picked === "root" || picked === null ? null : picked.id,
        index: Number.isFinite(number) && number > 0 ? number - 1 : null,
      });
    },
    onSuccess: async (moved) => {
      await invalidate(moved.id);
      onMoved(moved.id);
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (picked !== null) move.mutate();
  }

  const choices = candidates.slice(0, MAX_CANDIDATES);
  const pickedLabel = picked === "root" ? t("root") : picked ? pathLabel(index, picked) : "";

  return (
    <Modal title={t("title")} description={t("text", { name: node.name })} onClose={onClose}>
      <form method="post" onSubmit={submit} className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="move-search">{t("search")}</Label>
          <Input id="move-search" type="search" className="h-10 md:h-9" placeholder={t("searchPlaceholder")} value={query} onChange={(event) => setQuery(event.target.value)} />
        </div>
        <div role="listbox" aria-label={t("search")} className="max-h-56 space-y-0.5 overflow-y-auto rounded-lg border p-1">
          {canBeRoot ? (
            <button
              type="button"
              role="option"
              aria-selected={picked === "root"}
              className={`flex min-h-11 w-full items-center rounded-md px-2 text-left text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50 md:min-h-9 ${picked === "root" ? "bg-muted font-medium" : "hover:bg-muted/60"}`}
              onClick={() => setPicked("root")}
            >
              {t("root")}
            </button>
          ) : null}
          {choices.map((candidate) => (
            <button
              key={candidate.id}
              type="button"
              role="option"
              aria-selected={picked !== "root" && picked?.id === candidate.id}
              className={`flex min-h-11 w-full items-center rounded-md px-2 text-left text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50 md:min-h-9 ${picked !== "root" && picked?.id === candidate.id ? "bg-muted font-medium" : "hover:bg-muted/60"}`}
              onClick={() => setPicked(candidate)}
            >
              <span className="truncate">{pathLabel(index, candidate)}</span>
            </button>
          ))}
          {choices.length === 0 && !canBeRoot ? (
            <p className="px-2 py-3 text-sm text-muted-foreground">{t("noCandidates")}</p>
          ) : null}
        </div>
        {candidates.length > MAX_CANDIDATES ? (
          <p className="text-xs text-muted-foreground">{t("moreCandidates", { count: MAX_CANDIDATES })}</p>
        ) : null}
        {picked !== null ? <p className="text-sm font-medium">{t("selected", { name: pickedLabel })}</p> : null}
        <Field id="move-position" label={t("position")} hint={t("positionHint")}>
          <Input id="move-position" inputMode="numeric" pattern="[0-9]*" className="h-10 md:h-9" value={position} onChange={(event) => setPosition(event.target.value.replace(/[^0-9]/g, ""))} aria-describedby="move-position-hint" />
        </Field>
        <FormMessage error={move.error} />
        <DialogFooter>
          <Button type="button" variant="outline" size="lg" onClick={onClose}>
            {common("cancel")}
          </Button>
          <Button type="submit" size="lg" disabled={move.isPending || picked === null}>
            <PendingIcon pending={move.isPending} />
            {move.isPending ? t("submitting") : t("submit")}
          </Button>
        </DialogFooter>
      </form>
    </Modal>
  );
}

export function DeleteNodeModal({
  projectId,
  node,
  onClose,
  onDeleted,
}: {
  projectId: string;
  node: TreeNode;
  onClose: () => void;
  onDeleted: (parentId: string | null) => void;
}) {
  const t = useTranslations("NodeForm");
  const common = useTranslations("Settings.common");
  const invalidate = useNodeInvalidation(projectId);
  const remove = useMutation({
    mutationFn: () => api.delete(`${API}/projects/${projectId}/nodes/${node.id}`),
    onSuccess: async () => {
      await invalidate();
      onDeleted(node.parent_id);
    },
  });
  return (
    <ConfirmModal
      title={t("deleteTitle")}
      text={t("deleteText", { name: node.name })}
      confirmLabel={common("delete")}
      pendingLabel={common("deleting")}
      pending={remove.isPending}
      error={remove.error}
      onConfirm={() => remove.mutate()}
      onClose={onClose}
    />
  );
}
