"use client";

import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import { isItemState, type ItemState } from "@/lib/schedule/types";
import { cn } from "@/lib/utils";

const TONE: Record<ItemState, string> = {
  done: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  in_progress: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300",
  not_started: "bg-secondary text-muted-foreground",
  late_start: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  overdue: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
  overdue_no_fact: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
};

export function StateBadge({ state, overdueDays }: { state: string; overdueDays?: number }) {
  const t = useTranslations("Schedule.state");
  if (!isItemState(state)) return <Badge variant="outline">{state}</Badge>;
  const title =
    overdueDays && overdueDays > 0 ? t("overdueDays", { count: overdueDays }) : undefined;
  return (
    <Badge variant="secondary" className={cn(TONE[state])} title={title}>
      {t(state)}
    </Badge>
  );
}
