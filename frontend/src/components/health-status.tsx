"use client";

import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { CHECK_NAMES, fetchReadiness } from "@/lib/health";

const STATE_BADGE = {
  ok: "secondary",
  fail: "destructive",
  unknown: "outline",
} as const satisfies Record<string, "secondary" | "destructive" | "outline">;

export function HealthStatus() {
  const t = useTranslations("HealthStatus");
  const locale = useLocale();
  const { status, data, isFetching, refetch, dataUpdatedAt } = useQuery({
    queryKey: ["health", "ready"],
    queryFn: fetchReadiness,
    refetchInterval: 10_000,
  });

  const overall =
    status === "error" ? "unreachable" : status === "pending" ? "checking" : data.status;

  const updated =
    dataUpdatedAt > 0
      ? t("updatedAt", { time: new Date(dataUpdatedAt).toLocaleTimeString(locale) })
      : t("notUpdated");

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("title")}</CardTitle>
        <CardDescription role="status" aria-live="polite">
          {t(`overall.${overall}`)}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <ul className="divide-y">
          {CHECK_NAMES.map((name) => {
            const state =
              status === "success" ? (data.checks[name] ?? "unknown") : "unknown";
            return (
              <li
                key={name}
                className="flex items-center justify-between gap-4 py-2.5 first:pt-0 last:pb-0"
              >
                <span>{t(`checks.${name}`)}</span>
                <Badge variant={STATE_BADGE[state]}>{t(`state.${state}`)}</Badge>
              </li>
            );
          })}
        </ul>
      </CardContent>
      <CardFooter className="justify-between gap-3">
        <span className="text-xs text-muted-foreground">{updated}</span>
        <Button
          variant="outline"
          size="sm"
          onClick={() => void refetch()}
          disabled={isFetching}
        >
          <RefreshCw
            data-icon="inline-start"
            className="motion-safe:data-[fetching=true]:animate-spin"
            data-fetching={isFetching}
          />
          {t("refresh")}
        </Button>
      </CardFooter>
    </Card>
  );
}
