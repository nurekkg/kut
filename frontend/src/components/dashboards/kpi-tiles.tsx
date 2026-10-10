"use client";

import { TriangleAlert } from "lucide-react";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import {
  DASH,
  formatDays,
  formatDeviation,
  formatMoney,
  formatPercent,
} from "@/lib/dashboards/format";
import type { ObjectDashboardData } from "@/lib/dashboards/types";
import { cn } from "@/lib/utils";

export function KpiTile({
  testId,
  label,
  value,
  hero = false,
  children,
}: {
  testId: string;
  label: string;
  value: string;
  hero?: boolean;
  children?: ReactNode;
}) {
  return (
    <div
      data-testid={testId}
      className={cn(
        "min-w-0 rounded-xl bg-card p-4 ring-1 ring-foreground/10",
        hero && "col-span-2 md:col-span-1",
      )}
    >
      <p className="text-sm text-muted-foreground">{label}</p>
      <p
        className={cn(
          "mt-1 font-semibold tracking-tight break-words",
          hero ? "text-5xl" : "text-2xl",
        )}
      >
        {value}
      </p>
      {children ? <div className="mt-1 text-sm text-muted-foreground">{children}</div> : null}
    </div>
  );
}

export function DeviationTile({
  testId,
  deviation,
}: {
  testId: string;
  deviation: string | null;
}) {
  const t = useTranslations("Dashboard.kpi");
  let note: string | null = null;
  if (deviation !== null) {
    const value = Number(deviation);
    note = value < 0 ? t("behind") : value > 0 ? t("ahead") : t("onPlan");
  }
  return (
    <KpiTile testId={testId} label={t("deviation")} value={formatDeviation(deviation)}>
      {note}
    </KpiTile>
  );
}

export function KpiTiles({ dashboard }: { dashboard: ObjectDashboardData }) {
  const t = useTranslations("Dashboard.kpi");
  const { progress, counts, facts, budget, thresholds } = dashboard;
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
      <KpiTile
        testId="kpi-fact"
        label={t("fact")}
        value={formatPercent(progress?.percent ?? null)}
        hero
      />
      <KpiTile testId="kpi-plan" label={t("plan")} value={formatPercent(progress?.planned_percent ?? null)} />
      <DeviationTile testId="kpi-deviation" deviation={progress?.deviation ?? null} />
      <KpiTile testId="kpi-lag" label={t("lag")} value={formatDays(progress?.delay_days ?? null)} />
      <KpiTile testId="kpi-problems" label={t("problems")} value={counts ? String(counts.problems) : DASH} />
      <KpiTile
        testId="kpi-approval"
        label={t("approval")}
        value={facts ? String(facts.awaiting_approval) : DASH}
      >
        {facts && facts.awaiting_approval_stale > 0 ? (
          <span className="inline-flex items-center gap-1 font-medium text-destructive">
            <TriangleAlert className="size-4 shrink-0" aria-hidden />
            {t("approvalStale", {
              count: facts.awaiting_approval_stale,
              days: thresholds.approval_stale_days,
            })}
          </span>
        ) : null}
      </KpiTile>
      <KpiTile
        testId="kpi-recent"
        label={t("recent", { days: thresholds.recent_days })}
        value={facts ? String(facts.recent) : DASH}
      />
      <KpiTile testId="kpi-cost" label={t("cost")} value={DASH}>
        {t("costNote")}
      </KpiTile>
      {budget ? (
        <KpiTile
          testId="budget-tile"
          label={t("budget")}
          value={formatMoney(budget.planned_budget, budget.currency)}
        />
      ) : null}
    </div>
  );
}
