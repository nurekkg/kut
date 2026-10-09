"use client";

import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { ProjectFormModal } from "@/components/projects/project-form-modal";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError } from "@/lib/auth/api";
import { api } from "@/lib/api";
import { formatAmount, formatDate } from "@/lib/format";
import { API, PROJECTS_KEY } from "@/lib/projects/keys";
import { isStage, isStatus, type Project } from "@/lib/projects/types";

export function ProjectList() {
  const t = useTranslations("Projects");
  const router = useRouter();
  const { user } = useAuth();
  const [creating, setCreating] = useState(false);
  const canCreate = (user?.permissions.includes("objects:write") ?? false) && user?.scope === "group";
  const { data, error, isPending, isFetching, refetch } = useQuery({
    queryKey: PROJECTS_KEY,
    queryFn: () => api.get<Project[]>(`${API}/projects`),
  });

  if (isPending) {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        {t("loading")}
      </p>
    );
  }

  if (error) {
    const forbidden = error instanceof ApiError && error.status === 403;
    return (
      <Card>
        <CardContent className="space-y-3">
          <p role="alert" className="text-sm">
            {forbidden ? t("forbidden") : t("error")}
          </p>
          {forbidden ? null : (
            <Button variant="outline" onClick={() => void refetch()} disabled={isFetching}>
              {t("retry")}
            </Button>
          )}
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      {canCreate ? (
        <div className="flex justify-end">
          <Button size="lg" className="h-10" onClick={() => setCreating(true)}>
            <Plus data-icon="inline-start" aria-hidden />
            {t("add")}
          </Button>
        </div>
      ) : null}
      {data.length === 0 ? (
        <Card>
          <CardContent>
            <p className="text-sm text-muted-foreground">{t("empty")}</p>
          </CardContent>
        </Card>
      ) : (
        <ul className="grid gap-3 md:grid-cols-2">
          {data.map((project) => {
            const start = formatDate(project.planned_start);
            const end = formatDate(project.planned_end);
            const amount = formatAmount(project.planned_budget);
            return (
              <li key={project.id}>
                <Link
                  href={`/projects/${project.id}`}
                  className="block rounded-xl outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
                >
                  <Card size="sm" className="transition-colors hover:bg-muted/40">
                    <CardHeader>
                      <CardTitle>{project.name}</CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-2">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <Badge variant={project.status === "active" ? "secondary" : "outline"}>
                          {isStatus(project.status) ? t(`status.${project.status}`) : project.status}
                        </Badge>
                        {isStage(project.lifecycle_stage) ? (
                          <Badge variant="outline">{t(`stage.${project.lifecycle_stage}`)}</Badge>
                        ) : null}
                      </div>
                      <dl className="grid gap-1 text-sm">
                        {start || end ? (
                          <div className="flex gap-2">
                            <dt className="text-muted-foreground">{t("period")}</dt>
                            <dd>
                              {start ?? "—"} — {end ?? "—"}
                            </dd>
                          </div>
                        ) : null}
                        {amount ? (
                          <div className="flex gap-2">
                            <dt className="text-muted-foreground">{t("budget")}</dt>
                            <dd>{t("amountKgs", { amount })}</dd>
                          </div>
                        ) : null}
                        {project.node_count !== null ? (
                          <div className="text-muted-foreground">{t("nodeCount", { count: project.node_count })}</div>
                        ) : null}
                      </dl>
                    </CardContent>
                  </Card>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
      {creating ? (
        <ProjectFormModal
          project={null}
          onClose={() => setCreating(false)}
          onSaved={(created) => {
            setCreating(false);
            router.push(`/projects/${created.id}`);
          }}
        />
      ) : null}
    </div>
  );
}
