"use client";

import {
  Circle,
  CircleAlert,
  CircleCheck,
  CircleDashed,
  Clock,
  Hammer,
  TrendingDown,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";

import { DataField } from "@/components/dashboards/data-field";
import { EmptyNote } from "@/components/dashboards/section";
import { DASH, formatDays, formatPercent } from "@/lib/dashboards/format";
import type { ProblemWork } from "@/lib/dashboards/types";
import { useMediaQuery } from "@/lib/use-media-query";

type StateKey =
  | "done"
  | "in_progress"
  | "lagging"
  | "not_started"
  | "late_start"
  | "overdue"
  | "overdue_no_fact"
  | "unknown";

// «В работе» и «Отстаёт» различает отставание: у работы в процессе с отставанием от lag_days это «Отстаёт».
function stateView(state: string, lagging: boolean): { key: StateKey; icon: LucideIcon } {
  switch (state) {
    case "done":
      return { key: "done", icon: CircleCheck };
    case "overdue":
      return { key: "overdue", icon: TriangleAlert };
    case "overdue_no_fact":
      return { key: "overdue_no_fact", icon: CircleAlert };
    case "late_start":
      return { key: "late_start", icon: Clock };
    case "not_started":
      return { key: "not_started", icon: Circle };
    case "in_progress":
      return lagging ? { key: "lagging", icon: TrendingDown } : { key: "in_progress", icon: Hammer };
    default:
      return { key: "unknown", icon: CircleDashed };
  }
}

// Состояние всегда текстом с иконкой: цвет без подписи ничего не говорит.
function StateLabel({ problem, lagDays }: { problem: ProblemWork; lagDays: number }) {
  const t = useTranslations("Dashboard.states");
  const view = stateView(problem.state, Number(problem.delay_days) >= lagDays);
  const Icon = view.icon;
  return (
    <span className="inline-flex items-center gap-1.5">
      <Icon className="size-4 shrink-0" aria-hidden />
      {t(view.key)}
    </span>
  );
}

function workLabel(problem: ProblemWork): string {
  const name = problem.work_name ?? DASH;
  return problem.unit ? `${name}, ${problem.unit}` : name;
}

function NodeLink({ projectId, problem }: { projectId: string; problem: ProblemWork }) {
  return (
    <Link
      data-testid="problem-link"
      href={`/projects/${projectId}?node=${problem.node_id}`}
      className="rounded-sm break-words underline-offset-4 outline-none hover:underline focus-visible:ring-3 focus-visible:ring-ring/50"
    >
      {problem.node_name}
    </Link>
  );
}

export function ProblemsTable({
  projectId,
  problems,
  lagDays,
}: {
  projectId: string;
  problems: ProblemWork[];
  lagDays: number;
}) {
  const t = useTranslations("Dashboard.problems");
  const wide = useMediaQuery("(min-width: 768px)");

  if (!wide) {
    return (
      <div data-testid="problems-table">
        {problems.length === 0 ? (
          <EmptyNote>{t("empty")}</EmptyNote>
        ) : (
          <ul className="space-y-3">
            {problems.map((problem) => (
              <li
                key={problem.item_id}
                data-testid="problem-row"
                className="rounded-xl bg-card p-4 ring-1 ring-foreground/10"
              >
                <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
                  <div className="col-span-2">
                    <DataField label={t("work")}>{workLabel(problem)}</DataField>
                  </div>
                  <DataField label={t("node")}>
                    <NodeLink projectId={projectId} problem={problem} />
                  </DataField>
                  <DataField label={t("percent")}>{formatPercent(problem.percent)}</DataField>
                  <DataField label={t("lag")}>{formatDays(problem.delay_days)}</DataField>
                  <DataField label={t("overdue")}>{formatDays(problem.overdue_days)}</DataField>
                  <div className="col-span-2">
                    <DataField label={t("state")}>
                      <StateLabel problem={problem} lagDays={lagDays} />
                    </DataField>
                  </div>
                </dl>
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-xl ring-1 ring-foreground/10">
      <table data-testid="problems-table" className="w-full text-sm">
        <caption className="sr-only">{t("title")}</caption>
        <thead>
          <tr className="border-b text-left">
            <th scope="col" className="px-3 py-2.5 font-medium">
              {t("work")}
            </th>
            <th scope="col" className="px-3 py-2.5 font-medium">
              {t("node")}
            </th>
            <th scope="col" className="px-3 py-2.5 text-right font-medium">
              {t("lag")}
            </th>
            <th scope="col" className="px-3 py-2.5 text-right font-medium">
              {t("overdue")}
            </th>
            <th scope="col" className="px-3 py-2.5 font-medium">
              {t("state")}
            </th>
            <th scope="col" className="px-3 py-2.5 text-right font-medium">
              {t("percent")}
            </th>
          </tr>
        </thead>
        <tbody>
          {problems.length === 0 ? (
            <tr>
              <td colSpan={6} className="px-3 py-6 text-center text-muted-foreground">
                {t("empty")}
              </td>
            </tr>
          ) : (
            problems.map((problem) => (
              <tr
                key={problem.item_id}
                data-testid="problem-row"
                className="border-b align-top last:border-0"
              >
                <td className="px-3 py-2.5 break-words">{workLabel(problem)}</td>
                <td className="px-3 py-2.5">
                  <NodeLink projectId={projectId} problem={problem} />
                </td>
                <td className="px-3 py-2.5 text-right tabular-nums">
                  {formatDays(problem.delay_days)}
                </td>
                <td className="px-3 py-2.5 text-right tabular-nums">
                  {formatDays(problem.overdue_days)}
                </td>
                <td className="px-3 py-2.5">
                  <StateLabel problem={problem} lagDays={lagDays} />
                </td>
                <td className="px-3 py-2.5 text-right tabular-nums">
                  {formatPercent(problem.percent)}
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
