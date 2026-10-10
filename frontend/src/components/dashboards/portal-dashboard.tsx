"use client";

import { useTranslations } from "next-intl";

import { DashboardStatus } from "@/components/dashboards/dashboard-status";
import { DeviationTile, KpiTile } from "@/components/dashboards/kpi-tiles";
import { SCurve } from "@/components/dashboards/s-curve";
import { EmptyNote, Section } from "@/components/dashboards/section";
import { Badge } from "@/components/ui/badge";
import { formatDay, formatPercent } from "@/lib/dashboards/format";
import { usePortalDashboard } from "@/lib/dashboards/queries";
import { isStatus } from "@/lib/projects/types";

// Вид заказчика и субподрядчика: только процент, план, отклонение и кривая. Деньги, фото, работы,
// узлы и имена сотрудников здесь не выводятся: сервер их и не отдаёт.
export function PortalDashboard({ projectId }: { projectId: string }) {
  const t = useTranslations("Dashboard");
  const projects = useTranslations("Projects");
  const query = usePortalDashboard(projectId);
  const data = query.data;

  if (!data) {
    return (
      <DashboardStatus
        loading={query.isPending}
        error={query.error}
        onRetry={() => void query.refetch()}
      />
    );
  }

  const progress = data.progress;
  return (
    <div data-testid="portal-dashboard" className="space-y-6">
      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="min-w-0 text-2xl font-semibold tracking-tight break-words">{data.name}</h1>
          <Badge variant={data.status === "active" ? "secondary" : "outline"}>
            {isStatus(data.status) ? projects(`status.${data.status}`) : data.status}
          </Badge>
        </div>
        <p className="text-sm text-muted-foreground">
          {t("plannedPeriod")}: {formatDay(data.planned_start)} — {formatDay(data.planned_end)} ·{" "}
          {t("asOf", { date: formatDay(data.as_of) })}
        </p>
      </header>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
        <KpiTile
          testId="kpi-fact"
          label={t("kpi.fact")}
          value={formatPercent(progress?.percent ?? null)}
          hero
        />
        <KpiTile
          testId="kpi-plan"
          label={t("kpi.plan")}
          value={formatPercent(progress?.planned_percent ?? null)}
        />
        <DeviationTile testId="kpi-deviation" deviation={progress?.deviation ?? null} />
      </div>

      <Section testId="s-curve" title={t("series.title")} level="h2">
        {data.series.length > 0 ? (
          <SCurve series={data.series} asOf={data.as_of} />
        ) : (
          <EmptyNote>{t("series.empty")}</EmptyNote>
        )}
      </Section>
    </div>
  );
}
