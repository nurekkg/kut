"use client";

import { useTranslations } from "next-intl";
import { useRef, useState } from "react";
import { TriangleAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { NativeSelect } from "@/components/ui/native-select";
import {
  CALENDAR_HEADER_HEIGHT,
  CALENDAR_ROW_HEIGHT,
  CELL_WIDTH,
  LANE_HEIGHT,
  calendarWindow,
  dayColumns,
  type DayColumn,
  hoursByDay,
  hoursLevel,
  layoutBars,
} from "@/lib/equipment/calendar";
import { useCalendar, useEquipmentTypes } from "@/lib/equipment/queries";
import type { CalendarUnit } from "@/lib/equipment/types";
import { formatShortDate, todayIso } from "@/lib/schedule/dates";
import { useRowWindow } from "@/lib/use-row-window";
import { cn } from "@/lib/utils";

const LABEL_WIDTH = 176;

const BAR_CLASS = {
  assignment: "border-primary/40 bg-primary/15 text-foreground",
  repair: "border-destructive/40 bg-destructive/15 text-destructive",
  to: "border-amber-600/50 bg-amber-500/20 text-amber-950 dark:text-amber-200",
} as const;

// Календарь: единицы по строкам, дни по колонкам (±4 недели). Назначения и ТО - полоски в двух
// дорожках; строки рисуются окном (как в ГПР), прокрутка - внутри блока, страница не сдвигается.
export function CalendarView() {
  const t = useTranslations("Equipment.calendar");
  const weekday = useTranslations("Equipment.calendar.weekday");
  const weekdayName = (index: number) => weekday(WEEKDAY_KEYS[index] ?? "sun");
  const [today] = useState(todayIso);
  const [shift, setShift] = useState(0);
  const [typeId, setTypeId] = useState("");
  const { from, to } = calendarWindow(today, shift);
  const days = dayColumns(from, today);
  const calendar = useCalendar(from, to, typeId || undefined);
  const types = useEquipmentTypes();
  const units = calendar.data?.units ?? [];
  const scroller = useRef<HTMLDivElement>(null);
  const { start, end } = useRowWindow(scroller, units.length, CALENDAR_ROW_HEIGHT, {
    offset: CALENDAR_HEADER_HEIGHT,
  });
  const width = days.length * CELL_WIDTH;

  return (
    <section className="flex flex-col gap-4" aria-labelledby="calendar-title">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 id="calendar-title" className="text-xl font-semibold tracking-tight">
            {t("title")}
          </h1>
          <p className="text-sm tabular-nums text-muted-foreground" data-testid="calendar-window">
            {t("window", { from: formatShortDate(from), to: formatShortDate(to) })}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => setShift(shift - 1)} data-testid="calendar-prev">
            {t("prevWeek")}
          </Button>
          <Button variant="outline" size="sm" onClick={() => setShift(0)} data-testid="calendar-today">
            {t("today")}
          </Button>
          <Button variant="outline" size="sm" onClick={() => setShift(shift + 1)} data-testid="calendar-next">
            {t("nextWeek")}
          </Button>
          <NativeSelect
            aria-label={t("typeLabel")}
            className="w-auto min-w-40"
            value={typeId}
            onChange={(event) => setTypeId(event.target.value)}
            data-testid="calendar-type"
          >
            <option value="">{t("allTypes")}</option>
            {(types.data ?? []).map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </NativeSelect>
        </div>
      </div>

      <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground" aria-label={t("legend")}>
        <li className="flex items-center gap-1.5">
          <span className={cn("inline-block h-3 w-5 rounded border", BAR_CLASS.assignment)} />
          {t("legendAssignment")}
        </li>
        <li className="flex items-center gap-1.5">
          <span className={cn("inline-block h-3 w-5 rounded border", BAR_CLASS.repair)} />
          {t("legendRepair")}
        </li>
        <li className="flex items-center gap-1.5">
          <span className={cn("inline-block h-3 w-5 rounded border", BAR_CLASS.to)} />
          {t("legendTo")}
        </li>
        <li className="flex items-center gap-1.5">
          <span className="inline-block h-3 w-5 rounded border bg-amber-500/10" />
          {t("legendFull")}
        </li>
        <li className="flex items-center gap-1.5">
          <span className="inline-block h-3 w-5 rounded border bg-destructive/15" />
          {t("legendOver")}
        </li>
      </ul>

      {calendar.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {t("loadFailed")}
        </p>
      ) : null}

      {calendar.isSuccess && units.length === 0 ? (
        <p className="rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">
          {t("empty")}
        </p>
      ) : null}

      {units.length > 0 ? (
        <div
          ref={scroller}
          data-testid="calendar-grid"
          className="relative h-[min(70dvh,46rem)] min-h-80 overflow-auto rounded-xl border bg-background"
        >
          <div style={{ width: LABEL_WIDTH + width }} className="relative">
            <div className="sticky top-0 z-20 flex border-b bg-background" style={{ height: CALENDAR_HEADER_HEIGHT }}>
              <div className="sticky left-0 z-30 flex shrink-0 items-center border-r bg-background px-3 text-xs font-medium text-muted-foreground" style={{ width: LABEL_WIDTH }}>
                {t("unitColumn")}
              </div>
              {days.map((column) => (
                <DayHeader key={column.iso} column={column} weekdayName={weekdayName} />
              ))}
            </div>
            {start > 0 ? <div aria-hidden style={{ height: start * CALENDAR_ROW_HEIGHT }} /> : null}
            {units.slice(start, end).map((unit) => (
              <CalendarRow key={unit.id} unit={unit} days={days} from={from} to={to} />
            ))}
            {end < units.length ? <div aria-hidden style={{ height: (units.length - end) * CALENDAR_ROW_HEIGHT }} /> : null}
          </div>
        </div>
      ) : null}
    </section>
  );
}

const WEEKDAY_KEYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"] as const;

function DayHeader({ column, weekdayName }: { column: DayColumn; weekdayName: (index: number) => string }) {
  return (
    <div
      className={cn(
        "flex shrink-0 flex-col items-center justify-center border-r text-xs",
        column.weekend && "bg-muted/50",
        column.today && "font-semibold text-primary",
      )}
      style={{ width: CELL_WIDTH }}
      data-today={column.today || undefined}
    >
      <span className="tabular-nums">{column.day}</span>
      <span className="text-[10px] text-muted-foreground">{weekdayName(column.weekday)}</span>
    </div>
  );
}

function CalendarRow({
  unit,
  days,
  from,
  to,
}: {
  unit: CalendarUnit;
  days: DayColumn[];
  from: string;
  to: string;
}) {
  const t = useTranslations("Equipment.calendar");
  const byDay = hoursByDay(unit.days);
  const { bars, hidden } = layoutBars(from, to, unit.assignments, unit.maintenance, {
    assignment: (item) => t("barAssignment", { project: item.project_name, hours: Number(item.planned_hours_per_day) }),
    maintenance: (item) =>
      t("barMaintenance", {
        kind: item.kind === "repair" ? t("kindRepair") : t("kindTo"),
        title: item.title,
      }),
  });
  const warningText = unit.warnings
    .map((item) => `${t(`documentKind.${item.kind}`)} · ${formatShortDate(item.valid_until)}`)
    .join("; ");
  return (
    <div className="flex border-b" style={{ height: CALENDAR_ROW_HEIGHT }} data-testid="calendar-row" data-unit={unit.registry_number}>
      <div
        className="sticky left-0 z-10 flex shrink-0 flex-col justify-center gap-0.5 border-r bg-background px-3"
        style={{ width: LABEL_WIDTH }}
      >
        <span className="truncate text-sm font-medium">{unit.registry_number}</span>
        <span className="truncate text-xs text-muted-foreground">{unit.type_code}</span>
        {unit.warnings.length > 0 ? (
          <span className="flex items-center gap-1 truncate text-xs text-amber-800 dark:text-amber-300" title={warningText} data-testid="calendar-warning">
            <TriangleAlert className="size-3.5 shrink-0" aria-hidden />
            <span className="truncate">{t("warnings", { count: unit.warnings.length })}</span>
          </span>
        ) : null}
      </div>
      <div className="relative flex shrink-0" style={{ width: days.length * CELL_WIDTH }}>
        {days.map((column) => {
          const hours = byDay.get(column.iso) ?? 0;
          const level = hoursLevel(hours);
          return (
            <div
              key={column.iso}
              className={cn(
                "h-full shrink-0 border-r",
                column.weekend && "bg-muted/40",
                level === "full" && "bg-amber-500/10",
                level === "over" && "bg-destructive/15",
                column.today && "ring-1 ring-inset ring-primary/40",
              )}
              style={{ width: CELL_WIDTH }}
              title={hours > 0 ? t("cellHours", { hours }) : undefined}
            />
          );
        })}
        {bars.map((bar) => (
          <div
            key={bar.key}
            className={cn(
              "absolute truncate rounded-md border px-1.5 text-[11px] leading-none",
              BAR_CLASS[bar.kind],
            )}
            style={{
              left: bar.start * CELL_WIDTH + 2,
              width: (bar.end - bar.start + 1) * CELL_WIDTH - 4,
              top: bar.lane * LANE_HEIGHT + 6,
              height: LANE_HEIGHT - 3,
              lineHeight: `${LANE_HEIGHT - 5}px`,
            }}
            title={bar.label}
            data-kind={bar.kind}
          >
            {bar.label}
          </div>
        ))}
        {hidden > 0 ? (
          <span className="absolute right-1 bottom-0.5 rounded bg-background/90 px-1 text-[11px] text-muted-foreground">
            +{hidden}
          </span>
        ) : null}
      </div>
    </div>
  );
}
