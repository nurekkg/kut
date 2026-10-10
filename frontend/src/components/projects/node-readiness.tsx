"use client";

import { useTranslations } from "next-intl";

import { ProgressBar } from "@/components/schedule/progress-bar";
import { ApiError } from "@/lib/auth/api";
import { formatDelay, formatPercent, toNumber } from "@/lib/schedule/format";
import { useNodeReadiness } from "@/lib/schedule/queries";
import type { Aggregate } from "@/lib/schedule/types";
import { cn } from "@/lib/utils";

function Metric({
  label,
  value,
  tone,
  testId,
}: {
  label: string;
  value: string;
  tone?: "good" | "bad";
  testId?: string;
}) {
  return (
    <div className="space-y-0.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd
        data-testid={testId}
        className={cn(
          "text-sm font-medium tabular-nums",
          tone === "bad" && "text-red-600 dark:text-red-400",
          tone === "good" && "text-emerald-600 dark:text-emerald-400",
        )}
      >
        {value}
      </dd>
    </div>
  );
}

// Итог готовности поддерева: процент, что должно быть по плану, отклонение и просрочка.
export function AggregateCard({ aggregate, title }: { aggregate: Aggregate; title: string }) {
  const t = useTranslations("NodeReadiness");
  const deviation = toNumber(aggregate.deviation);
  const delay = toNumber(aggregate.delay_days);
  return (
    <section
      aria-label={title}
      className="space-y-3 rounded-xl border p-4"
      data-testid="node-readiness"
    >
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-3xl font-semibold tabular-nums" data-testid="node-readiness-percent">
          {formatPercent(aggregate.percent)}
        </span>
        <span className="text-sm text-muted-foreground">
          {t("planned", { percent: formatPercent(aggregate.planned_percent) })}
        </span>
      </div>
      <ProgressBar
        percent={aggregate.percent}
        planned={aggregate.planned_percent}
        label={title}
        className="[&>span]:sr-only"
        barClassName="h-2.5 w-full"
      />
      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3 lg:grid-cols-6">
        <Metric
          label={t("deviation")}
          value={`${formatDelay(deviation)} ${t("pp")}`}
          tone={deviation < 0 ? "bad" : deviation > 0 ? "good" : undefined}
        />
        <Metric
          label={t("delay")}
          value={`${formatDelay(delay)} ${t("days")}`}
          tone={delay > 0 ? "bad" : delay < 0 ? "good" : undefined}
        />
        <Metric label={t("works")} value={String(aggregate.items)} />
        <Metric label={t("completed")} value={String(aggregate.completed)} />
        <Metric
          label={t("overdue")}
          value={String(aggregate.overdue)}
          tone={aggregate.overdue > 0 ? "bad" : undefined}
          testId="node-overdue"
        />
        <Metric
          label={t("overdueNoFact")}
          value={String(aggregate.overdue_no_fact)}
          tone={aggregate.overdue_no_fact > 0 ? "bad" : undefined}
        />
      </dl>
    </section>
  );
}

// Блок «Обзора» узла: готовность поддерева. Нет работ в ГПР или нет права на ГПР - блока нет.
export function NodeReadinessSummary({ projectId, nodeId }: { projectId: string; nodeId: string }) {
  const t = useTranslations("NodeReadiness");
  const readiness = useNodeReadiness(projectId, nodeId);
  if (readiness.error instanceof ApiError && [403, 404].includes(readiness.error.status)) {
    return null;
  }
  if (readiness.isPending) {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        {t("loading")}
      </p>
    );
  }
  if (readiness.error) {
    return (
      <p role="alert" className="text-sm text-destructive">
        {t("error")}
      </p>
    );
  }
  const subtree = readiness.data?.subtree ?? null;
  if (!subtree) {
    return <p className="text-sm text-muted-foreground">{t("noWorks")}</p>;
  }
  return <AggregateCard aggregate={subtree} title={t("title")} />;
}
