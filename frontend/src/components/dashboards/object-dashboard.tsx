"use client";

import { useTranslations } from "next-intl";
import Link from "next/link";

import { DashboardStatus } from "@/components/dashboards/dashboard-status";
import { KpiTiles } from "@/components/dashboards/kpi-tiles";
import { NodeBars } from "@/components/dashboards/node-bars";
import { PhotoFeed } from "@/components/dashboards/photo-feed";
import { ProblemsTable } from "@/components/dashboards/problems-table";
import { SCurve } from "@/components/dashboards/s-curve";
import { EmptyNote, Section } from "@/components/dashboards/section";
import { Badge } from "@/components/ui/badge";
import { DASH, formatDay, formatDays, formatPercent } from "@/lib/dashboards/format";
import { useObjectDashboard } from "@/lib/dashboards/queries";
import type { NodeTile } from "@/lib/dashboards/types";
import { isNodeType, isStage, isStatus } from "@/lib/projects/types";

export function ObjectDashboard({ projectId }: { projectId: string }) {
  const t = useTranslations("Dashboard");
  const projects = useTranslations("Projects");
  const query = useObjectDashboard(projectId);
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

  const stage = isStage(data.lifecycle_stage) ? projects(`stage.${data.lifecycle_stage}`) : DASH;
  const status = isStatus(data.status) ? projects(`status.${data.status}`) : data.status;
  const lagDays = data.thresholds.lag_days;

  return (
    <div data-testid="object-dashboard" className="space-y-6">
      <header className="space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="min-w-0 text-xl font-semibold tracking-tight break-words">{data.name}</h2>
          <Badge variant={data.status === "active" ? "secondary" : "outline"}>{status}</Badge>
        </div>
        <p className="text-sm text-muted-foreground">
          {stage} · {t("plannedPeriod")}: {formatDay(data.planned_start)} —{" "}
          {formatDay(data.planned_end)} · {t("asOf", { date: formatDay(data.as_of) })}
        </p>
      </header>

      <KpiTiles dashboard={data} />

      <div className="grid gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <Section testId="s-curve" title={t("series.title")}>
          {data.series.length > 0 ? (
            <SCurve series={data.series} asOf={data.as_of} showPlan />
          ) : (
            <EmptyNote>{t("series.empty")}</EmptyNote>
          )}
        </Section>
        <Section testId="node-bars" title={t("nodes.title")}>
          {data.nodes.length > 0 ? (
            <NodeBars nodes={data.nodes} />
          ) : (
            <EmptyNote>{t("nodes.empty")}</EmptyNote>
          )}
        </Section>
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
        <Section testId="zones" title={t("zones.title")}>
          <ZonesList projectId={projectId} zones={data.zones} />
        </Section>
        <Section title={t("problems.title")}>
          <p className="text-sm text-muted-foreground">{t("problems.rule", { days: lagDays })}</p>
          <ProblemsTable projectId={projectId} problems={data.problems} lagDays={lagDays} />
        </Section>
      </div>

      {data.photos !== null ? (
        <Section testId="photo-feed" title={t("photos.title")}>
          <PhotoFeed projectId={projectId} photos={data.photos} />
        </Section>
      ) : null}
    </div>
  );
}

function ZonesList({ projectId, zones }: { projectId: string; zones: NodeTile[] }) {
  const t = useTranslations("Dashboard.zones");
  const types = useTranslations("NodeType");
  if (zones.length === 0) return <EmptyNote>{t("empty")}</EmptyNote>;
  return (
    <ul className="divide-y rounded-xl ring-1 ring-foreground/10">
      {zones.map((zone) => (
        <li
          key={zone.node_id}
          className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1 p-3 text-sm"
        >
          <div className="min-w-0">
            <Link
              href={`/projects/${projectId}?node=${zone.node_id}`}
              className="rounded-sm font-medium break-words underline-offset-4 outline-none hover:underline focus-visible:ring-3 focus-visible:ring-ring/50"
            >
              {zone.name}
            </Link>
            <p className="text-muted-foreground">
              {isNodeType(zone.type) ? types(zone.type) : zone.type}
            </p>
            {zone.overdue > 0 ? (
              <p className="text-muted-foreground">{t("overdue", { count: zone.overdue })}</p>
            ) : null}
          </div>
          <p className="text-right tabular-nums">
            {t("item", {
              percent: formatPercent(zone.progress.percent),
              lag: formatDays(zone.progress.delay_days),
            })}
          </p>
        </li>
      ))}
    </ul>
  );
}
