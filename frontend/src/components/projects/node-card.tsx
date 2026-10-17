"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowLeft, ArrowUp, FolderTree, Pencil, Plus, Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { NodeDocumentsTab } from "@/components/documents/node-documents-tab";
import { NodeDatesTab } from "@/components/projects/node-dates-tab";
import { NodeIcon } from "@/components/projects/node-icon";
import { NodeMediaTab } from "@/components/projects/node-media-tab";
import { NodeReadinessSummary } from "@/components/projects/node-readiness";
import { NodeWorksTab } from "@/components/projects/node-works-tab";
import { QueryState } from "@/components/settings/shared";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { API, nodeKey } from "@/lib/projects/keys";
import { ancestors, siblingsOf, type TreeIndex } from "@/lib/projects/tree";
import { allowedChildren, isNodeType, type NodeDetail, type TreeNode } from "@/lib/projects/types";

// Порядок вкладок по ТЗ §8.2. «Расходы» и «Подрядчики» остаются заглушками (S5+).
const TABS = ["costs", "works", "contractors", "dates", "documents", "media"] as const;
const STUB_TABS: readonly string[] = ["costs", "contractors"];

export type NodeActions = {
  onBack: () => void;
  onSelect: (id: string) => void;
  onAddChild: () => void;
  onEdit: () => void;
  onMove: () => void;
  onDelete: () => void;
  onReorder: (direction: -1 | 1) => void;
};

type Props = {
  projectId: string;
  node: TreeNode;
  index: TreeIndex;
  internal: boolean;
  canWrite: boolean;
  actions: NodeActions;
};

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-0.5 sm:grid-cols-[11rem_1fr] sm:gap-3">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="text-sm break-words">{children}</dd>
    </div>
  );
}

export function NodeCard({ projectId, node, index, internal, canWrite, actions }: Props) {
  const t = useTranslations("NodeCard");
  const types = useTranslations("NodeType");
  const form = useTranslations("NodeForm");
  const { user } = useAuth();
  // ГПР и готовность видят роли с gpr:read; фото - только внутренние роли (внешним они не отдаются).
  const canSeeSchedule = user?.permissions.includes("gpr:read") ?? false;
  // Документы узла отдаёт сервер только внутренним: внешним вкладка была бы пустой.
  const canSeeDocuments = internal && (user?.permissions.includes("documents:read") ?? false);
  const tabs = TABS.filter((tab) => {
    if (tab === "works" || tab === "dates") return canSeeSchedule;
    if (tab === "documents") return canSeeDocuments;
    if (tab === "media") return internal;
    return true;
  });
  const parents = ancestors(index, node);
  const siblings = siblingsOf(index, node);
  const position = siblings.findIndex((item) => item.id === node.id);
  const children = index.children.get(node.id) ?? [];
  const canAddChild = isNodeType(node.type) && allowedChildren(node.type).length > 0;

  // Имена прораба и мастера приходят только во внутреннем ответе; внешним ролям они не выдаются.
  const detail = useQuery({
    queryKey: nodeKey(projectId, node.id),
    queryFn: () => api.get<NodeDetail>(`${API}/projects/${projectId}/nodes/${node.id}`),
    enabled: internal,
  });

  return (
    <article className="space-y-4" aria-labelledby="node-title">
      <div className="space-y-3">
        <button
          type="button"
          onClick={actions.onBack}
          className="inline-flex min-h-10 items-center gap-1.5 rounded-md text-sm text-muted-foreground outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 lg:hidden"
        >
          <ArrowLeft className="size-4" aria-hidden />
          {t("back")}
        </button>
        {parents.length > 0 ? (
          <nav aria-label={t("path")} className="flex flex-wrap items-center gap-x-1 text-sm text-muted-foreground">
            {parents.map((parent, position) => (
              <span key={parent.id} className="flex items-center gap-1">
                {position > 0 ? <span aria-hidden>›</span> : null}
                <button
                  type="button"
                  className="min-h-8 rounded-sm underline-offset-4 outline-none hover:text-foreground hover:underline focus-visible:ring-3 focus-visible:ring-ring/50"
                  onClick={() => actions.onSelect(parent.id)}
                >
                  {parent.name}
                </button>
              </span>
            ))}
          </nav>
        ) : null}
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2.5">
            <NodeIcon type={node.type} className="size-6 shrink-0 text-muted-foreground" />
            <div className="min-w-0">
              <h2 id="node-title" className="text-xl font-semibold tracking-tight break-words">
                {node.name}
              </h2>
              <p className="text-sm text-muted-foreground">
                {isNodeType(node.type) ? types(node.type) : node.type}
                {node.code ? ` · ${node.code}` : ""}
              </p>
            </div>
          </div>
          {canWrite ? (
            <div className="flex flex-wrap gap-2">
              {canAddChild ? (
                <Button type="button" size="lg" className="h-10" onClick={actions.onAddChild}>
                  <Plus data-icon="inline-start" aria-hidden />
                  {t("actions.addChild")}
                </Button>
              ) : null}
              <Button type="button" variant="outline" size="lg" className="h-10" onClick={actions.onEdit}>
                <Pencil data-icon="inline-start" aria-hidden />
                {t("actions.edit")}
              </Button>
              <Button type="button" variant="outline" size="lg" className="h-10" onClick={actions.onMove}>
                <FolderTree data-icon="inline-start" aria-hidden />
                {t("actions.move")}
              </Button>
              <Button
                type="button"
                variant="outline"
                size="icon-lg"
                className="size-10"
                aria-label={t("actions.up")}
                disabled={position <= 0}
                onClick={() => actions.onReorder(-1)}
              >
                <ArrowUp aria-hidden />
              </Button>
              <Button
                type="button"
                variant="outline"
                size="icon-lg"
                className="size-10"
                aria-label={t("actions.down")}
                disabled={position < 0 || position >= siblings.length - 1}
                onClick={() => actions.onReorder(1)}
              >
                <ArrowDown aria-hidden />
              </Button>
              <Button type="button" variant="destructive" size="lg" className="h-10" onClick={actions.onDelete}>
                <Trash2 data-icon="inline-start" aria-hidden />
                {t("actions.delete")}
              </Button>
            </div>
          ) : null}
        </div>
      </div>

      <Tabs defaultValue="overview">
        <TabsList aria-label={form("editTitle")}>
          <TabsTrigger value="overview">{t("tabs.overview")}</TabsTrigger>
          {tabs.map((tab) => (
            <TabsTrigger key={tab} value={tab}>
              {t(`tabs.${tab}`)}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value="overview" className="space-y-5 pt-1">
          {canSeeSchedule ? <NodeReadinessSummary projectId={projectId} nodeId={node.id} /> : null}
          <QueryState loading={internal && detail.isPending} error={detail.error} onRetry={() => void detail.refetch()}>
            <dl className="space-y-3">
              <Row label={t("type")}>{isNodeType(node.type) ? types(node.type) : node.type}</Row>
              <Row label={t("code")}>{node.code ?? t("notSet")}</Row>
              <Row label={t("foreman")}>
                {internal ? (detail.data?.foreman_name ?? t("notAssigned")) : t("notSet")}
              </Row>
              <Row label={t("master")}>
                {internal ? (detail.data?.master_name ?? t("notAssigned")) : t("notSet")}
              </Row>
              <Row label={t("plannedStart")}>{formatDate(node.planned_start) ?? t("notSet")}</Row>
              <Row label={t("plannedEnd")}>{formatDate(node.planned_end) ?? t("notSet")}</Row>
              <Row label={t("polygon")}>
                {node.polygon ? t("polygonYes", { count: node.polygon.length }) : t("polygonNo")}
              </Row>
            </dl>
          </QueryState>
          <section aria-labelledby="node-children" className="space-y-2">
            <h3 id="node-children" className="text-sm font-medium">
              {t("children")}
            </h3>
            {children.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t("noChildren")}</p>
            ) : (
              <ul className="grid gap-1.5 sm:grid-cols-2">
                {children.slice(0, 60).map((child) => (
                  <li key={child.id}>
                    <button
                      type="button"
                      className="flex min-h-11 w-full items-center gap-2 rounded-lg border px-3 text-left text-sm outline-none hover:bg-muted/60 focus-visible:ring-3 focus-visible:ring-ring/50 md:min-h-10"
                      onClick={() => actions.onSelect(child.id)}
                    >
                      <NodeIcon type={child.type} className="size-4 shrink-0 text-muted-foreground" />
                      <span className="min-w-0 truncate">{child.name}</span>
                      <Badge variant="outline" className="ml-auto shrink-0">
                        {isNodeType(child.type) ? types(child.type) : child.type}
                      </Badge>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </TabsContent>
        {tabs
          .filter((tab) => STUB_TABS.includes(tab))
          .map((tab) => (
            <TabsContent key={tab} value={tab} className="pt-1">
              <p className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">
                {t("stub")}
              </p>
            </TabsContent>
          ))}
        {canSeeSchedule ? (
          <>
            <TabsContent value="works" className="pt-1">
              <NodeWorksTab projectId={projectId} node={node} index={index} showWeight={internal} />
            </TabsContent>
            <TabsContent value="dates" className="pt-1">
              <NodeDatesTab
                projectId={projectId}
                node={node}
                index={index}
                onSelect={actions.onSelect}
              />
            </TabsContent>
          </>
        ) : null}
        {canSeeDocuments ? (
          <TabsContent value="documents" className="pt-1">
            <NodeDocumentsTab projectId={projectId} nodeId={node.id} />
          </TabsContent>
        ) : null}
        {internal ? (
          <TabsContent value="media" className="pt-1">
            <NodeMediaTab projectId={projectId} node={node} index={index} />
          </TabsContent>
        ) : null}
      </Tabs>
    </article>
  );
}
