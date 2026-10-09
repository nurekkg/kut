"use client";

import { ExternalLink } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useMemo } from "react";

import { AggregateCard } from "@/components/projects/node-readiness";
import { useScheduleColumns } from "@/components/schedule/schedule-columns";
import { ScheduleTable, useScheduleTable } from "@/components/schedule/schedule-table";
import { QueryState } from "@/components/settings/shared";
import type { TreeIndex } from "@/lib/projects/tree";
import type { TreeNode } from "@/lib/projects/types";
import { formatVolume } from "@/lib/schedule/format";
import { useNodeReadiness, useNodeSchedule } from "@/lib/schedule/queries";
import { buildNodeInfo, toRows } from "@/lib/schedule/rows";

const LIMIT = 1000;

// Вкладка «Этапы работ»: ГПР поддерева узла с готовностью по каждой работе и факт без плана.
export function NodeWorksTab({
  projectId,
  node,
  index,
  showWeight,
}: {
  projectId: string;
  node: TreeNode;
  index: TreeIndex;
  showWeight: boolean;
}) {
  const t = useTranslations("NodeWorks");
  const items = useNodeSchedule(projectId, node.id);
  const readiness = useNodeReadiness(projectId, node.id);
  const nodes = useMemo(() => buildNodeInfo(index), [index]);
  const rows = useMemo(() => toRows(items.data ?? [], nodes), [items.data, nodes]);
  const columns = useScheduleColumns({ showWeight, compact: true });
  const table = useScheduleTable(rows, columns);
  const unplanned = readiness.data?.unplanned ?? [];
  const subtree = readiness.data?.subtree ?? null;

  return (
    <div className="space-y-4">
      <QueryState loading={items.isPending} error={items.error} onRetry={() => void items.refetch()}>
        {subtree ? <AggregateCard aggregate={subtree} title={t("summary")} /> : null}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm text-muted-foreground" data-testid="node-works-count">
            {t("count", { count: rows.length })}
            {rows.length >= LIMIT ? ` · ${t("truncated", { count: LIMIT })}` : ""}
          </p>
          <Link
            href={`/projects/${projectId}/schedule?node=${node.id}`}
            className="inline-flex min-h-10 items-center gap-1.5 rounded-md text-sm text-primary underline-offset-4 outline-none hover:underline focus-visible:ring-3 focus-visible:ring-ring/50"
          >
            <ExternalLink className="size-4" aria-hidden />
            {t("openSchedule")}
          </Link>
        </div>
        <ScheduleTable
          table={table}
          nodes={nodes}
          empty={t("empty")}
          className="h-[min(58dvh,30rem)]"
        />
        {unplanned.length > 0 ? (
          <section aria-labelledby="node-unplanned" className="space-y-2">
            <div className="space-y-0.5">
              <h3 id="node-unplanned" className="text-sm font-semibold">
                {t("unplannedTitle")}
              </h3>
              <p className="text-xs text-muted-foreground">{t("unplannedHint")}</p>
            </div>
            <ul className="divide-y rounded-lg border text-sm">
              {unplanned.map((entry) => (
                <li key={entry.work_id} className="flex flex-wrap items-center gap-x-3 px-3 py-2">
                  <span className="min-w-0 flex-1 break-words">{entry.work_name ?? "—"}</span>
                  <span className="tabular-nums">
                    {formatVolume(entry.volume)} {entry.unit ?? ""}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {t("facts", { count: entry.facts })}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        ) : null}
      </QueryState>
    </div>
  );
}
