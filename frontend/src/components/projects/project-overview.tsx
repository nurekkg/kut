"use client";

import { Map as MapIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { formatAmount, formatDate } from "@/lib/format";
import { isStage, isStatus, type Project } from "@/lib/projects/types";

function Item({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-0.5 sm:grid-cols-[11rem_1fr] sm:gap-3">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="text-sm break-words">{children}</dd>
    </div>
  );
}

export function ProjectSummary({ project }: { project: Project }) {
  const t = useTranslations("Workspace.summary");
  const projects = useTranslations("Projects");
  const dash = t("empty");
  const start = formatDate(project.planned_start);
  const end = formatDate(project.planned_end);
  const amount = formatAmount(project.planned_budget);
  return (
    <dl className="space-y-3">
      <Item label={t("stage")}>
        {isStage(project.lifecycle_stage) ? projects(`stage.${project.lifecycle_stage}`) : dash}
      </Item>
      <Item label={t("status")}>
        <Badge variant={project.status === "active" ? "secondary" : "outline"}>
          {isStatus(project.status) ? projects(`status.${project.status}`) : project.status}
        </Badge>
      </Item>
      <Item label={t("period")}>{start || end ? `${start ?? dash} — ${end ?? dash}` : dash}</Item>
      <Item label={t("budget")}>{amount ? projects("amountKgs", { amount }) : dash}</Item>
      <Item label={t("customer")}>{project.customer_name ?? dash}</Item>
      <Item label={t("address")}>{project.address ?? dash}</Item>
      <Item label={t("warranty")}>{formatDate(project.warranty_until) ?? dash}</Item>
      {project.node_count !== null ? <Item label={t("nodes")}>{project.node_count}</Item> : null}
    </dl>
  );
}

// Место под интерактивную карту объекта (Konva - позже): полигоны узлов уже хранятся в БД.
export function MapPlaceholder() {
  const t = useTranslations("Workspace.map");
  return (
    <section
      aria-labelledby="map-title"
      className="flex min-h-48 flex-col items-center justify-center gap-2 rounded-xl border border-dashed p-6 text-center"
    >
      <MapIcon className="size-8 text-muted-foreground" aria-hidden />
      <h3 id="map-title" className="text-sm font-medium">
        {t("title")}
      </h3>
      <p className="max-w-md text-sm text-muted-foreground">{t("text")}</p>
    </section>
  );
}

export function ProjectOverview({ project }: { project: Project }) {
  const t = useTranslations("Workspace");
  return (
    <div className="space-y-5">
      <h2 className="text-lg font-semibold tracking-tight">{t("overview")}</h2>
      <ProjectSummary project={project} />
      <MapPlaceholder />
    </div>
  );
}
