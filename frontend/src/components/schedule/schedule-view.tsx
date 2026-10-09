"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ChevronsDownUp, ChevronsUpDown, Plus } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useMemo, useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { UserMenu } from "@/components/auth/user-menu";
import { AppNav } from "@/components/projects/app-nav";
import { ItemDialog } from "@/components/schedule/item-dialog";
import { ScheduleGantt, GanttLegend } from "@/components/schedule/schedule-gantt";
import { useScheduleColumns } from "@/components/schedule/schedule-columns";
import { ScheduleTable, useScheduleTable } from "@/components/schedule/schedule-table";
import { SearchPicker } from "@/components/schedule/search-picker";
import { QueryState } from "@/components/settings/shared";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { api } from "@/lib/api";
import { ApiError } from "@/lib/auth/api";
import { API, projectKey, treeKey } from "@/lib/projects/keys";
import { buildIndex } from "@/lib/projects/tree";
import type { Project, TreeNode } from "@/lib/projects/types";
import { todayIso } from "@/lib/schedule/dates";
import { formatDelay, formatPercent } from "@/lib/schedule/format";
import { GANTT_SCALES, type GanttScale } from "@/lib/schedule/gantt";
import {
  buildNodeInfo,
  filterRows,
  hasFilters,
  LAG_FILTERS,
  NO_FILTERS,
  toRows,
  workOptions,
  type NodeInfo,
  type ScheduleFilters,
  type ScheduleRow,
} from "@/lib/schedule/rows";
import { useDependencies, useProjectReadiness, useSchedule } from "@/lib/schedule/queries";
import { cn } from "@/lib/utils";

type View = "table" | "gantt";
const VIEWS: View[] = ["table", "gantt"];

export function ScheduleView({ projectId }: { projectId: string }) {
  const t = useTranslations("Schedule");
  const { user } = useAuth();
  const presetNode = useSearchParams().get("node") ?? "";
  const internal = user?.is_internal ?? false;
  const canWrite = user?.permissions.includes("gpr:write") ?? false;

  const project = useQuery({
    queryKey: projectKey(projectId),
    queryFn: () => api.get<Project>(`${API}/projects/${projectId}`),
  });
  const tree = useQuery({
    queryKey: treeKey(projectId),
    queryFn: () => api.get<TreeNode[]>(`${API}/projects/${projectId}/tree`),
  });
  const schedule = useSchedule(projectId);
  const dependencies = useDependencies(projectId);
  const readiness = useProjectReadiness(projectId);

  const [today] = useState(todayIso);
  const [view, setView] = useState<View>("table");
  const [scale, setScale] = useState<GanttScale>("week");
  const [filters, setFilters] = useState<ScheduleFilters>({ ...NO_FILTERS, nodeId: presetNode });
  const [dialog, setDialog] = useState<{ item: ScheduleRow | null } | null>(null);

  const index = useMemo(() => buildIndex(tree.data ?? []), [tree.data]);
  const nodes = useMemo(() => buildNodeInfo(index), [index]);
  const allRows = useMemo(() => toRows(schedule.data ?? [], nodes), [schedule.data, nodes]);
  const rows = useMemo(() => filterRows(allRows, filters, index), [allRows, filters, index]);
  const works = useMemo(() => workOptions(allRows), [allRows]);

  const edit = useCallback((row: ScheduleRow) => setDialog({ item: row }), []);
  const columns = useScheduleColumns({
    showWeight: internal,
    compact: false,
    onEdit: canWrite ? edit : undefined,
  });
  const table = useScheduleTable(rows, columns);

  const searchNodes = useCallback(
    (query: string) => {
      if (query === "") return [];
      const needle = query.toLowerCase();
      const found: NodeInfo[] = [];
      for (const info of nodes.values()) {
        if (info.label.toLowerCase().includes(needle)) found.push(info);
        if (found.length >= 20) break;
      }
      return found;
    },
    [nodes],
  );

  const unreachable =
    project.error instanceof ApiError && (project.error.status === 404 || project.error.status === 403);
  const total = readiness.data?.project ?? null;
  const patch = (next: Partial<ScheduleFilters>) => setFilters((current) => ({ ...current, ...next }));

  return (
    <main className="mx-auto flex w-full max-w-[110rem] flex-1 flex-col gap-4 px-4 py-4">
      <UserMenu />
      <AppNav />
      {unreachable ? (
        <div role="alert" className="space-y-3 text-sm">
          <p>{t("notFound")}</p>
          <Link href="/projects" className="text-primary underline-offset-4 hover:underline">
            {t("backToProjects")}
          </Link>
        </div>
      ) : (
        <QueryState
          loading={project.isPending || tree.isPending || schedule.isPending}
          error={project.error ?? tree.error ?? schedule.error}
          onRetry={() => {
            void project.refetch();
            void tree.refetch();
            void schedule.refetch();
          }}
        >
          <header className="space-y-3">
            <Link
              href={`/projects/${projectId}`}
              className="inline-flex min-h-10 items-center gap-1.5 rounded-md text-sm text-muted-foreground outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50"
            >
              <ArrowLeft className="size-4" aria-hidden />
              {project.data?.name ?? t("back")}
            </Link>
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div className="space-y-1">
                <h1 className="text-2xl font-semibold tracking-tight">{t("title")}</h1>
                <p className="text-sm text-muted-foreground" data-testid="schedule-summary">
                  {t("summary", { count: allRows.length, shown: rows.length })}
                  {total ? (
                    <>
                      {" · "}
                      {t("summaryReadiness", {
                        percent: formatPercent(total.percent),
                        planned: formatPercent(total.planned_percent),
                        deviation: formatDelay(total.deviation),
                      })}
                    </>
                  ) : null}
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <div role="group" aria-label={t("viewLabel")} className="inline-flex rounded-lg border p-0.5">
                  {VIEWS.map((item) => (
                    <Button
                      key={item}
                      type="button"
                      size="lg"
                      variant={view === item ? "secondary" : "ghost"}
                      className="h-9"
                      aria-pressed={view === item}
                      onClick={() => setView(item)}
                    >
                      {t(`view.${item}`)}
                    </Button>
                  ))}
                </div>
                {view === "gantt" ? (
                  <div role="group" aria-label={t("scaleLabel")} className="inline-flex rounded-lg border p-0.5">
                    {GANTT_SCALES.map((item) => (
                      <Button
                        key={item}
                        type="button"
                        size="lg"
                        variant={scale === item ? "secondary" : "ghost"}
                        className="h-9"
                        aria-pressed={scale === item}
                        onClick={() => setScale(item)}
                      >
                        {t(`scale.${item}`)}
                      </Button>
                    ))}
                  </div>
                ) : null}
                <Button type="button" variant="outline" size="lg" className="h-10" onClick={() => table.setExpanded(true)}>
                  <ChevronsUpDown data-icon="inline-start" aria-hidden />
                  {t("expandAll")}
                </Button>
                <Button type="button" variant="outline" size="lg" className="h-10" onClick={() => table.setExpanded({})}>
                  <ChevronsDownUp data-icon="inline-start" aria-hidden />
                  {t("collapseAll")}
                </Button>
                {canWrite ? (
                  <Button type="button" size="lg" className="h-10" onClick={() => setDialog({ item: null })}>
                    <Plus data-icon="inline-start" aria-hidden />
                    {t("add")}
                  </Button>
                ) : null}
              </div>
            </div>
          </header>

          <section aria-label={t("filters.label")} className="grid gap-3 rounded-xl border p-3 md:grid-cols-2 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_12rem_minmax(0,1fr)_auto] xl:items-start">
            <SearchPicker<NodeInfo>
              id="filter-node"
              label={t("filters.node")}
              placeholder={t("filters.nodePlaceholder")}
              selected={filters.nodeId ? (nodes.get(filters.nodeId) ?? null) : null}
              onSelect={(info) => patch({ nodeId: info?.id ?? "" })}
              search={searchNodes}
              keyOf={(info) => info.id}
              render={(info) => <span className="break-words">{info.label}</span>}
              emptyText={t("filters.nothingFound")}
              hint={t("filters.nodeHint")}
            />
            <div className="space-y-1.5">
              <Label htmlFor="filter-work">{t("filters.work")}</Label>
              <NativeSelect
                id="filter-work"
                className="h-10 md:h-9"
                value={filters.workId}
                onChange={(event) => patch({ workId: event.target.value })}
              >
                <option value="">{t("filters.anyWork")}</option>
                {works.map((work) => (
                  <option key={work.id} value={work.id}>
                    {work.label}
                  </option>
                ))}
              </NativeSelect>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="filter-lag">{t("filters.lag")}</Label>
              <NativeSelect
                id="filter-lag"
                className="h-10 md:h-9"
                value={filters.lag}
                onChange={(event) => patch({ lag: event.target.value as ScheduleFilters["lag"] })}
              >
                {LAG_FILTERS.map((item) => (
                  <option key={item} value={item}>
                    {t(`lag.${item}`)}
                  </option>
                ))}
              </NativeSelect>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="filter-query">{t("filters.query")}</Label>
              <Input
                id="filter-query"
                type="search"
                className="h-10 md:h-9"
                placeholder={t("filters.queryPlaceholder")}
                value={filters.query}
                onChange={(event) => patch({ query: event.target.value })}
              />
            </div>
            <div className="flex items-end xl:pt-[1.625rem]">
              <Button
                type="button"
                variant="ghost"
                size="lg"
                className={cn("h-10", !hasFilters(filters) && "invisible")}
                onClick={() => setFilters(NO_FILTERS)}
              >
                {t("filters.reset")}
              </Button>
            </div>
          </section>

          {view === "table" ? (
            <ScheduleTable
              table={table}
              nodes={nodes}
              empty={allRows.length === 0 ? t("emptyAll") : t("empty")}
            />
          ) : (
            <>
              <ScheduleGantt
                rows={table.getRowModel().rows}
                data={rows}
                dependencies={dependencies.data ?? []}
                nodes={nodes}
                scale={scale}
                today={today}
                onOpen={canWrite ? edit : undefined}
              />
              <GanttLegend />
            </>
          )}
        </QueryState>
      )}

      {dialog ? (
        <ItemDialog
          projectId={projectId}
          nodes={nodes}
          items={allRows}
          dependencies={dependencies.data ?? []}
          item={dialog.item}
          presetNodeId={filters.nodeId || undefined}
          onClose={() => setDialog(null)}
        />
      ) : null}
    </main>
  );
}
