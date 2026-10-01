"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError } from "@/lib/auth/api";
import { apiFetch } from "@/lib/auth/session";
import type { Project } from "@/lib/auth/types";

const STATUSES = ["active", "suspended", "completed", "archived"] as const;
type Status = (typeof STATUSES)[number];

function isStatus(value: string): value is Status {
  return (STATUSES as readonly string[]).includes(value);
}

export function ProjectList() {
  const t = useTranslations("Projects");
  const { data, error, isPending, isFetching, refetch } = useQuery({
    queryKey: ["projects"],
    queryFn: () => apiFetch<Project[]>("/api/v1/projects"),
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

  if (data.length === 0) {
    return (
      <Card>
        <CardContent>
          <p className="text-sm text-muted-foreground">{t("empty")}</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <ul className="grid gap-3 sm:grid-cols-2">
      {data.map((project) => (
        <li key={project.id}>
          <Card size="sm">
            <CardHeader>
              <CardTitle>{project.name}</CardTitle>
            </CardHeader>
            <CardContent>
              <Badge variant={project.status === "active" ? "secondary" : "outline"}>
                {isStatus(project.status) ? t(`status.${project.status}`) : project.status}
              </Badge>
            </CardContent>
          </Card>
        </li>
      ))}
    </ul>
  );
}
