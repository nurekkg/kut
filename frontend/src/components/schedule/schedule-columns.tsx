"use client";

import type { AggregationFn, ColumnDef } from "@tanstack/react-table";
import { Pencil } from "lucide-react";
import { useTranslations } from "next-intl";
import { useMemo } from "react";

import { ProgressBar } from "@/components/schedule/progress-bar";
import { StateBadge } from "@/components/schedule/state-badge";
import { Button } from "@/components/ui/button";
import { formatShortDate } from "@/lib/schedule/dates";
import { formatDelay, formatVolume, toNumber } from "@/lib/schedule/format";
import type { ScheduleRow } from "@/lib/schedule/rows";
import type { ItemProgress } from "@/lib/schedule/types";
import { cn } from "@/lib/utils";

// Колонка, по которой таблица группирует строки: узел структуры, к которому относится работа.
export const GROUP_COLUMN = "node";
export const RIGHT_ALIGNED = new Set(["plan_volume", "weight", "confirmed", "delay"]);

// Итог группы по весам работ (так же считает сервер: сумма вес x значение / сумма весов).
function weightedMean(read: (row: ScheduleRow) => number): AggregationFn<ScheduleRow> {
  return (_columnId, leafRows) => {
    let sum = 0;
    let weight = 0;
    for (const leaf of leafRows) {
      const share = toNumber(leaf.original.progress?.weight);
      sum += share * read(leaf.original);
      weight += share;
    }
    return weight > 0 ? sum / weight : 0;
  };
}

function DelayCell({ progress }: { progress: ItemProgress | null }) {
  if (!progress || progress.state === "done") {
    return <span className="text-muted-foreground">—</span>;
  }
  const days = toNumber(progress.delay_days);
  const tone =
    days > 0
      ? "text-red-600 dark:text-red-400"
      : days < 0
        ? "text-emerald-600 dark:text-emerald-400"
        : "text-muted-foreground";
  return <span className={cn("tabular-nums", tone)}>{formatDelay(days)}</span>;
}

type Options = {
  showWeight: boolean;
  compact: boolean;
  onEdit?: (row: ScheduleRow) => void;
};

export function useScheduleColumns({
  showWeight,
  compact,
  onEdit,
}: Options): ColumnDef<ScheduleRow, unknown>[] {
  const t = useTranslations("Schedule.columns");
  return useMemo(() => {
    // В карточке узла места меньше, чем на странице графика: колонки уже, без веса.
    const columns: ColumnDef<ScheduleRow, unknown>[] = [
      {
        id: GROUP_COLUMN,
        accessorFn: (row) => row.node_id,
        header: () => t("node"),
        enableSorting: false,
      },
      {
        id: "work",
        header: () => t("work"),
        size: compact ? 220 : 330,
        enableSorting: false,
        accessorFn: (row) => row.work_name,
        cell: ({ row }) => (
          <span className="flex min-w-0 items-baseline gap-2">
            <span className="shrink-0 font-mono text-xs text-muted-foreground">
              {row.original.work_code}
            </span>
            <span className="truncate" title={row.original.work_name}>
              {row.original.work_name}
            </span>
          </span>
        ),
      },
    ];
    if (!compact) {
      columns.push({
        id: "unit",
        header: () => t("unit"),
        size: 64,
        enableSorting: false,
        accessorFn: (row) => row.unit,
      });
    }
    columns.push(
      {
        id: "plan_start",
        header: () => t("planStart"),
        size: compact ? 84 : 92,
        accessorFn: (row) => row.plan_start,
        aggregationFn: "min",
        cell: ({ row }) => formatShortDate(row.original.plan_start),
      },
      {
        id: "plan_end",
        header: () => t("planEnd"),
        size: compact ? 84 : 92,
        accessorFn: (row) => row.plan_end,
        aggregationFn: "max",
        cell: ({ row }) => formatShortDate(row.original.plan_end),
      },
      {
        id: "plan_volume",
        header: () => t("planVolume"),
        size: compact ? 76 : 104,
        enableSorting: false,
        accessorFn: (row) => toNumber(row.plan_volume),
        cell: ({ row }) => (
          <span className="tabular-nums">{formatVolume(row.original.plan_volume)}</span>
        ),
      },
    );
    if (showWeight && !compact) {
      columns.push({
        id: "weight",
        header: () => t("weight"),
        size: 76,
        enableSorting: false,
        accessorFn: (row) => toNumber(row.progress?.weight ?? row.weight),
        cell: ({ row }) => (
          <span
            className={cn("tabular-nums", row.original.weight === null && "text-muted-foreground")}
            title={row.original.weight === null ? t("weightAuto") : undefined}
          >
            {formatVolume(row.original.progress?.weight ?? row.original.weight)}
          </span>
        ),
      });
    }
    columns.push(
      {
        id: "confirmed",
        header: () => t("confirmed"),
        size: compact ? 100 : 104,
        enableSorting: false,
        accessorFn: (row) => toNumber(row.progress?.confirmed_volume),
        cell: ({ row }) => (
          <span className="tabular-nums">
            {formatVolume(row.original.progress?.confirmed_volume)}
          </span>
        ),
      },
      {
        id: "percent",
        header: () => t("percent"),
        size: compact ? 140 : 156,
        accessorFn: (row) => toNumber(row.progress?.percent),
        aggregationFn: weightedMean((row) => toNumber(row.progress?.percent)),
        cell: ({ row }) =>
          row.original.progress ? (
            <ProgressBar
              percent={row.original.progress.percent}
              planned={row.original.progress.planned_percent}
            />
          ) : (
            <span className="text-muted-foreground">—</span>
          ),
      },
      {
        id: "delay",
        header: () => t("delay"),
        size: compact ? 112 : 124,
        accessorFn: (row) => toNumber(row.progress?.delay_days),
        aggregationFn: weightedMean((row) => toNumber(row.progress?.delay_days)),
        cell: ({ row }) => <DelayCell progress={row.original.progress} />,
      },
      {
        id: "state",
        header: () => t("state"),
        size: compact ? 130 : 138,
        enableSorting: false,
        accessorFn: (row) => row.progress?.state ?? "",
        cell: ({ row }) =>
          row.original.progress ? (
            <StateBadge
              state={row.original.progress.state}
              overdueDays={row.original.progress.overdue_days}
            />
          ) : null,
      },
    );
    if (onEdit) {
      columns.push({
        id: "actions",
        header: () => <span className="sr-only">{t("actions")}</span>,
        size: 56,
        enableSorting: false,
        cell: ({ row }) => (
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            onClick={() => onEdit(row.original)}
            aria-label={t("edit", { name: row.original.work_name })}
          >
            <Pencil aria-hidden />
          </Button>
        ),
      });
    }
    return columns;
  }, [t, showWeight, compact, onEdit]);
}
