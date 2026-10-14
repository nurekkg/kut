"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, CalendarRange, ChevronDown, FileText, Pencil, Trash2, Truck } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { UserMenu } from "@/components/auth/user-menu";
import { ObjectDashboard } from "@/components/dashboards/object-dashboard";
import { ObjectViewSwitch } from "@/components/dashboards/view-switch";
import { AppNav } from "@/components/projects/app-nav";
import { NodeCard } from "@/components/projects/node-card";
import { DeleteNodeModal, MoveNodeModal, NodeFormModal } from "@/components/projects/node-dialogs";
import { NodeTree } from "@/components/projects/node-tree";
import { ProjectOverview, ProjectSummary } from "@/components/projects/project-overview";
import { ProjectFormModal } from "@/components/projects/project-form-modal";
import { ConfirmModal } from "@/components/settings/dialogs";
import { QueryState } from "@/components/settings/shared";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { NativeSelect } from "@/components/ui/native-select";
import { api } from "@/lib/api";
import { ApiError } from "@/lib/auth/api";
import { DASHBOARDS_ROOT, dashboardProjectKey } from "@/lib/dashboards/keys";
import { API, PROJECTS_KEY, projectKey, treeKey } from "@/lib/projects/keys";
import { buildIndex, siblingsOf } from "@/lib/projects/tree";
import { isStatus, type NodeDetail, type Project, type TreeNode } from "@/lib/projects/types";

type Dialog =
  | { kind: "create"; parent: TreeNode | null }
  | { kind: "edit" }
  | { kind: "move" }
  | { kind: "delete" }
  | { kind: "project-edit" }
  | { kind: "project-delete" }
  | null;

export function ProjectWorkspace({ projectId }: { projectId: string }) {
  const t = useTranslations("Workspace");
  const projects = useTranslations("Projects");
  const common = useTranslations("Settings.common");
  const form = useTranslations("ProjectForm");
  const { user } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const queryClient = useQueryClient();
  const searchParams = useSearchParams();
  const selectedParam = searchParams.get("node");
  const cardView = searchParams.get("view") === "card";
  const [dialog, setDialog] = useState<Dialog>(null);

  const internal = user?.is_internal ?? false;
  const canWrite = user?.permissions.includes("objects:write") ?? false;
  const canEditCard = canWrite && user?.scope === "group";
  const canSeeSchedule = user?.permissions.includes("gpr:read") ?? false;
  const canSeeEquipment = user?.permissions.includes("equipment:read") ?? false;
  const canSeeDocuments = user?.permissions.includes("documents:read") ?? false;
  const equipmentLabel = useTranslations("ProjectEquipment");

  const project = useQuery({
    queryKey: projectKey(projectId),
    queryFn: () => api.get<Project>(`${API}/projects/${projectId}`),
  });
  const list = useQuery({ queryKey: PROJECTS_KEY, queryFn: () => api.get<Project[]>(`${API}/projects`) });
  const tree = useQuery({
    queryKey: treeKey(projectId),
    queryFn: () => api.get<TreeNode[]>(`${API}/projects/${projectId}/tree`),
  });
  const index = useMemo(() => buildIndex(tree.data ?? []), [tree.data]);
  const selected = selectedParam ? (index.byId.get(selectedParam) ?? null) : null;

  const select = useCallback(
    (id: string | null) => {
      router.push(id ? `${pathname}?node=${id}` : pathname, { scroll: false });
    },
    [router, pathname],
  );

  // Ссылка на удалённый или чужой узел: возвращаем обзор объекта, а не пустую карточку.
  useEffect(() => {
    if (tree.data && selectedParam && !index.byId.has(selectedParam)) router.replace(pathname);
  }, [tree.data, selectedParam, index, router, pathname]);

  const reorder = useMutation({
    mutationFn: ({ node, direction }: { node: TreeNode; direction: -1 | 1 }) => {
      const position = siblingsOf(index, node).findIndex((item) => item.id === node.id);
      return api.post<NodeDetail>(`${API}/projects/${projectId}/nodes/${node.id}/move`, {
        parent_id: node.parent_id,
        index: Math.max(position + direction, 0),
      });
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: treeKey(projectId) }),
  });

  const removeProject = useMutation({
    mutationFn: () => api.delete(`${API}/projects/${projectId}`),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: PROJECTS_KEY });
      router.replace("/projects");
    },
  });

  const close = () => setDialog(null);
  // Чужой или несуществующий объект сервер называет одинаково (404), 403 - нет права на раздел.
  const unreachable =
    project.error instanceof ApiError && (project.error.status === 404 || project.error.status === 403);

  return (
    <main className="mx-auto flex w-full max-w-7xl flex-1 flex-col gap-4 px-4 py-4">
      <UserMenu />
      <AppNav />
      {unreachable ? (
        <div role="alert" className="space-y-3 text-sm">
          <p>{t("notFound")}</p>
          <Link href="/projects" className="text-primary underline-offset-4 hover:underline">
            {t("back")}
          </Link>
        </div>
      ) : (
      <QueryState loading={project.isPending} error={project.error} onRetry={() => void project.refetch()}>
        {project.data ? (
          <>
            <header className="space-y-3">
              <div className="flex flex-wrap items-center gap-3">
                <Link
                  href="/projects"
                  className="inline-flex min-h-10 items-center gap-1.5 rounded-md text-sm text-muted-foreground outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50"
                >
                  <ArrowLeft className="size-4" aria-hidden />
                  {t("back")}
                </Link>
                {(list.data ?? []).length > 1 ? (
                  <div className="ml-auto w-full max-w-64 sm:w-64">
                    <label htmlFor="project-switch" className="sr-only">
                      {t("switchProject")}
                    </label>
                    <NativeSelect
                      id="project-switch"
                      className="h-10 md:h-9"
                      value={projectId}
                      onChange={(event) => router.push(`/projects/${event.target.value}`)}
                    >
                      {(list.data ?? []).map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.name}
                        </option>
                      ))}
                    </NativeSelect>
                  </div>
                ) : null}
              </div>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex min-w-0 flex-wrap items-center gap-2">
                  <h1 className="text-2xl font-semibold tracking-tight break-words">{project.data.name}</h1>
                  <Badge variant={project.data.status === "active" ? "secondary" : "outline"}>
                    {isStatus(project.data.status) ? projects(`status.${project.data.status}`) : project.data.status}
                  </Badge>
                </div>
                <div className="flex flex-wrap gap-2">
                  {canSeeEquipment ? (
                    <Link
                      href={`/projects/${projectId}/equipment`}
                      className={buttonVariants({ variant: "outline", size: "lg", className: "h-10" })}
                      data-testid="project-equipment-link"
                    >
                      <Truck data-icon="inline-start" aria-hidden />
                      {equipmentLabel("tab")}
                    </Link>
                  ) : null}
                  {canSeeDocuments ? (
                    <Link
                      href={`/projects/${projectId}/documents`}
                      className={buttonVariants({ variant: "outline", size: "lg", className: "h-10" })}
                      data-testid="project-documents-link"
                    >
                      <FileText data-icon="inline-start" aria-hidden />
                      {t("documents")}
                    </Link>
                  ) : null}
                  {canSeeSchedule ? (
                    <Link
                      href={`/projects/${projectId}/schedule`}
                      className={buttonVariants({ variant: "outline", size: "lg", className: "h-10" })}
                    >
                      <CalendarRange data-icon="inline-start" aria-hidden />
                      {t("schedule")}
                    </Link>
                  ) : null}
                  {canEditCard ? (
                    <>
                      <Button type="button" variant="outline" size="lg" className="h-10" onClick={() => setDialog({ kind: "project-edit" })}>
                        <Pencil data-icon="inline-start" aria-hidden />
                        {form("edit")}
                      </Button>
                      <Button type="button" variant="destructive" size="lg" className="h-10" onClick={() => { removeProject.reset(); setDialog({ kind: "project-delete" }); }}>
                        <Trash2 data-icon="inline-start" aria-hidden />
                        {form("delete")}
                      </Button>
                    </>
                  ) : null}
                </div>
              </div>
            </header>

            <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[22rem_minmax(0,1fr)]">
              <aside
                aria-label={t("structure")}
                className={`flex min-h-0 flex-col gap-3 rounded-xl border p-3 lg:h-[calc(100dvh-15rem)] lg:min-h-[28rem] ${selected ? "hidden lg:flex" : "flex"}`}
              >
                {/* На телефоне обзор объекта свёрнут: дерево, ради которого заходят, остаётся на виду */}
                <details className="group rounded-lg border px-3 lg:hidden">
                  <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between text-sm font-medium [&::-webkit-details-marker]:hidden">
                    {t("overview")}
                    <ChevronDown className="size-4 transition-transform group-open:rotate-180" aria-hidden />
                  </summary>
                  <div className="pb-3 pt-1">
                    <ProjectSummary project={project.data} />
                  </div>
                </details>
                <QueryState loading={tree.isPending} error={tree.error} onRetry={() => void tree.refetch()}>
                  <NodeTree
                    index={index}
                    selectedId={selected?.id ?? null}
                    onSelect={select}
                    onAddRoot={canWrite ? () => setDialog({ kind: "create", parent: null }) : undefined}
                  />
                </QueryState>
              </aside>

              <section className="order-first min-w-0 lg:order-none">
                {selected ? (
                  <NodeCard
                    key={selected.id}
                    projectId={projectId}
                    node={selected}
                    index={index}
                    internal={internal}
                    canWrite={canWrite}
                    actions={{
                      onBack: () => select(null),
                      onSelect: select,
                      onAddChild: () => setDialog({ kind: "create", parent: selected }),
                      onEdit: () => setDialog({ kind: "edit" }),
                      onMove: () => setDialog({ kind: "move" }),
                      onDelete: () => setDialog({ kind: "delete" }),
                      onReorder: (direction) => reorder.mutate({ node: selected, direction }),
                    }}
                  />
                ) : internal ? (
                  <div className="space-y-5">
                    <ObjectViewSwitch view={cardView ? "card" : "dashboard"} />
                    {cardView ? (
                      <ProjectOverview project={project.data} />
                    ) : (
                      <ObjectDashboard projectId={projectId} />
                    )}
                  </div>
                ) : (
                  <ProjectOverview project={project.data} />
                )}
              </section>
            </div>
          </>
        ) : null}
      </QueryState>
      )}

      {dialog?.kind === "create" ? (
        <NodeFormModal
          mode="create"
          projectId={projectId}
          index={index}
          parent={dialog.parent}
          onClose={close}
          onSaved={(id) => {
            close();
            select(id);
          }}
        />
      ) : null}
      {dialog?.kind === "edit" && selected ? (
        <NodeFormModal
          mode="edit"
          projectId={projectId}
          index={index}
          node={{ ...selected, foreman_name: null, master_name: null }}
          onClose={close}
          onSaved={close}
        />
      ) : null}
      {dialog?.kind === "move" && selected ? (
        <MoveNodeModal
          projectId={projectId}
          node={selected}
          index={index}
          onClose={close}
          onMoved={() => close()}
        />
      ) : null}
      {dialog?.kind === "delete" && selected ? (
        <DeleteNodeModal
          projectId={projectId}
          node={selected}
          onClose={close}
          onDeleted={(parentId) => {
            close();
            select(parentId);
          }}
        />
      ) : null}
      {dialog?.kind === "project-edit" && project.data ? (
        <ProjectFormModal
          project={project.data}
          onClose={close}
          onSaved={() => {
            close();
            void queryClient.invalidateQueries({ queryKey: dashboardProjectKey(projectId) });
            void queryClient.invalidateQueries({ queryKey: DASHBOARDS_ROOT });
          }}
        />
      ) : null}
      {dialog?.kind === "project-delete" && project.data ? (
        <ConfirmModal
          title={form("deleteTitle")}
          text={form("deleteText", { name: project.data.name })}
          confirmLabel={common("delete")}
          pendingLabel={common("deleting")}
          pending={removeProject.isPending}
          error={removeProject.error}
          onConfirm={() => removeProject.mutate()}
          onClose={close}
        />
      ) : null}
    </main>
  );
}
