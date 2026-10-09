"use client";

import { useTranslations } from "next-intl";
import { useMemo } from "react";

import { AggregateCard } from "@/components/projects/node-readiness";
import { ProgressBar } from "@/components/schedule/progress-bar";
import { StateBadge } from "@/components/schedule/state-badge";
import { QueryState } from "@/components/settings/shared";
import type { TreeIndex } from "@/lib/projects/tree";
import type { TreeNode } from "@/lib/projects/types";
import { formatShortDate } from "@/lib/schedule/dates";
import { formatDelay, toNumber } from "@/lib/schedule/format";
import { useNodeReadiness } from "@/lib/schedule/queries";
import { cn } from "@/lib/utils";

function Signed({ value, unit, invert }: { value: number; unit: string; invert?: boolean }) {
  // Для отклонения плюс - хорошо (впереди плана), для отставания в днях плюс - плохо.
  const bad = invert ? value > 0 : value < 0;
  const good = invert ? value < 0 : value > 0;
  return (
    <span
      className={cn(
        "tabular-nums",
        bad && "text-red-600 dark:text-red-400",
        good && "text-emerald-600 dark:text-emerald-400",
      )}
    >
      {formatDelay(value)} {unit}
    </span>
  );
}

// Вкладка «Сроки»: где поддерево отстаёт от линейного плана. Сверху итог, ниже вложенные узлы
// от худшего отклонения и собственные работы узла с просрочкой.
export function NodeDatesTab({
  projectId,
  node,
  index,
  onSelect,
}: {
  projectId: string;
  node: TreeNode;
  index: TreeIndex;
  onSelect: (id: string) => void;
}) {
  const t = useTranslations("NodeDates");
  const readiness = useNodeReadiness(projectId, node.id);
  const detail = readiness.data;

  const children = useMemo(
    () =>
      [...(detail?.children ?? [])].sort(
        (a, b) => toNumber(a.subtree.deviation) - toNumber(b.subtree.deviation),
      ),
    [detail?.children],
  );
  const problems = useMemo(
    () =>
      [...(detail?.items ?? [])]
        .filter((item) => item.state !== "done" && item.state !== "not_started")
        .sort((a, b) => toNumber(b.delay_days) - toNumber(a.delay_days)),
    [detail?.items],
  );

  return (
    <QueryState
      loading={readiness.isPending}
      error={readiness.error}
      onRetry={() => void readiness.refetch()}
    >
      {!detail?.subtree ? (
        <p className="text-sm text-muted-foreground">{t("noWorks")}</p>
      ) : (
        <div className="space-y-5">
          <AggregateCard aggregate={detail.subtree} title={t("summary")} />
          <p className="text-xs text-muted-foreground">{t("asOf", { date: formatShortDate(detail.as_of) })}</p>

          <section aria-labelledby="dates-children" className="space-y-2">
            <h3 id="dates-children" className="text-sm font-semibold">
              {t("children")}
            </h3>
            {children.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t("noChildren")}</p>
            ) : (
              <div className="overflow-x-auto rounded-xl border">
                <table className="w-full min-w-[40rem] text-sm">
                  <thead>
                    <tr className="border-b text-left text-xs text-muted-foreground">
                      <th scope="col" className="px-3 py-2 font-medium">{t("node")}</th>
                      <th scope="col" className="px-3 py-2 font-medium">{t("percent")}</th>
                      <th scope="col" className="px-3 py-2 text-right font-medium">{t("deviation")}</th>
                      <th scope="col" className="px-3 py-2 text-right font-medium">{t("delay")}</th>
                      <th scope="col" className="px-3 py-2 text-right font-medium">{t("overdue")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {children.slice(0, 200).map((child) => (
                      <tr key={child.node_id} className="border-b last:border-0 hover:bg-muted/40">
                        <td className="px-3 py-1.5">
                          <button
                            type="button"
                            className="min-h-9 rounded-sm text-left underline-offset-4 outline-none hover:underline focus-visible:ring-3 focus-visible:ring-ring/50"
                            onClick={() => onSelect(child.node_id)}
                          >
                            {index.byId.get(child.node_id)?.name ?? "…"}
                          </button>
                        </td>
                        <td className="px-3 py-1.5">
                          <ProgressBar
                            percent={child.subtree.percent}
                            planned={child.subtree.planned_percent}
                          />
                        </td>
                        <td className="px-3 py-1.5 text-right">
                          <Signed value={toNumber(child.subtree.deviation)} unit={t("pp")} />
                        </td>
                        <td className="px-3 py-1.5 text-right">
                          <Signed value={toNumber(child.subtree.delay_days)} unit={t("days")} invert />
                        </td>
                        <td className="px-3 py-1.5 text-right tabular-nums">{child.subtree.overdue}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section aria-labelledby="dates-items" className="space-y-2">
            <h3 id="dates-items" className="text-sm font-semibold">
              {t("items")}
            </h3>
            {problems.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t("noProblems")}</p>
            ) : (
              <ul className="divide-y rounded-xl border text-sm">
                {problems.slice(0, 100).map((item) => (
                  <li key={item.item_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
                    <span className="min-w-0 flex-1 break-words">
                      <span className="font-mono text-xs text-muted-foreground">{item.work_code}</span>{" "}
                      {item.work_name}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {formatShortDate(item.plan_start)} — {formatShortDate(item.plan_end)}
                    </span>
                    <ProgressBar percent={item.percent} planned={item.planned_percent} />
                    <Signed value={toNumber(item.delay_days)} unit={t("days")} invert />
                    <StateBadge state={item.state} overdueDays={item.overdue_days} />
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      )}
    </QueryState>
  );
}
