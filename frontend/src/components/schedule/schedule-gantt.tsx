"use client";

import type { Row } from "@tanstack/react-table";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useMemo, useRef } from "react";

import { dayNumber, formatShortDate } from "@/lib/schedule/dates";
import { formatDelay, formatPercent, formatVolume, toNumber } from "@/lib/schedule/format";
import {
  arrowPath,
  BAR_CLASSES,
  computeDomain,
  monthTicks,
  PX_PER_DAY,
  WINDOW_YEARS,
  weekTicks,
  xEnd,
  xOfDay,
  xStart,
  type GanttScale,
} from "@/lib/schedule/gantt";
import { GANTT_HEADER_HEIGHT, GANTT_LABEL_WIDTH, ROW_HEIGHT } from "@/lib/schedule/layout";
import type { NodeInfo, ScheduleRow } from "@/lib/schedule/rows";
import { isItemState, type Dependency } from "@/lib/schedule/types";
import { useRowWindow } from "@/lib/use-row-window";
import { cn } from "@/lib/utils";

const PLAN_TOP = 6;
const PLAN_HEIGHT = 11;
const FACT_TOP = 19;
const FACT_HEIGHT = 7;
const MIN_BAR = 3;

type Props = {
  // Видимые строки таблицы (с группами) и все отфильтрованные работы: границы шкалы считаются по
  // работам, а не по видимым строкам, чтобы диаграмма не прыгала при сворачивании групп.
  rows: Row<ScheduleRow>[];
  data: ScheduleRow[];
  dependencies: Dependency[];
  nodes: Map<string, NodeInfo>;
  scale: GanttScale;
  today: string;
  onOpen?: (item: ScheduleRow) => void;
  className?: string;
};

// Диаграмма Ганта на своём SVG: полоса плана, под ней полоса факта (от первого до последнего
// подтверждённого объёма), стрелки «окончание - начало» и линия «сегодня». Строки те же, что в
// таблице (группы, раскрытие, фильтры, сортировка), и так же рисуются окном: в DOM только видимые.
export function ScheduleGantt({
  rows,
  data,
  dependencies,
  nodes,
  scale,
  today,
  onOpen,
  className,
}: Props) {
  const t = useTranslations("Schedule");
  const scroller = useRef<HTMLDivElement>(null);
  const anchoredFor = useRef<string | null>(null);
  const { start, end } = useRowWindow(scroller, rows.length, ROW_HEIGHT, {
    offset: GANTT_HEADER_HEIGHT,
  });

  const domain = useMemo(() => {
    const spans: { start: string; end: string }[] = [];
    for (const item of data) {
      spans.push({ start: item.plan_start, end: item.plan_end });
      const progress = item.progress;
      if (progress?.first_fact_date) {
        spans.push({
          start: progress.first_fact_date,
          end: progress.last_fact_date ?? progress.first_fact_date,
        });
      }
    }
    return computeDomain(spans, today);
  }, [data, today]);

  const timelineWidth = domain.days * PX_PER_DAY[scale];
  const totalWidth = GANTT_LABEL_WIDTH + timelineWidth;
  const months = useMemo(() => monthTicks(domain, scale), [domain, scale]);
  const weeks = useMemo(() => (scale === "week" ? weekTicks(domain, scale) : []), [domain, scale]);
  const gridlines = scale === "week" ? weeks : months;
  const todayX = xOfDay(domain, scale, dayNumber(today)) + PX_PER_DAY[scale] / 2;

  const rowOf = useMemo(() => {
    const map = new Map<string, number>();
    rows.forEach((row, index) => {
      if (!row.getIsGrouped()) map.set(row.original.id, index);
    });
    return map;
  }, [rows]);

  const arrows = useMemo(() => {
    const result: { id: string; from: number; to: number }[] = [];
    for (const dependency of dependencies) {
      const from = rowOf.get(dependency.predecessor_id);
      const to = rowOf.get(dependency.successor_id);
      if (from === undefined || to === undefined) continue;
      if (Math.max(from, to) < start || Math.min(from, to) >= end) continue;
      result.push({ id: dependency.id, from, to });
    }
    return result;
  }, [dependencies, rowOf, start, end]);

  // Начальное положение шкалы (при открытии, смене масштаба и фильтров, но не при сворачивании
  // групп): если «сегодня» попадает в период работ первых строк - по центру на «сегодня», иначе
  // с начала этих работ, чтобы полосы были в кадре, а не за левым краем.
  const anchor = `${scale}:${domain.startDay}:${domain.endDay}`;
  useEffect(() => {
    const element = scroller.current;
    if (!element || anchoredFor.current === anchor) return;
    anchoredFor.current = anchor;
    let first = Number.POSITIVE_INFINITY;
    let last = Number.NEGATIVE_INFINITY;
    for (const row of rows.slice(0, 40)) {
      if (row.getIsGrouped()) continue;
      first = Math.min(first, dayNumber(row.original.plan_start));
      last = Math.max(last, dayNumber(row.original.plan_end));
    }
    const todayDay = dayNumber(today);
    const visibleWidth = element.clientWidth - GANTT_LABEL_WIDTH;
    const target =
      Number.isFinite(first) && (todayDay < first || todayDay > last)
        ? xOfDay(domain, scale, first) - 24
        : todayX - visibleWidth / 2;
    element.scrollLeft = Math.max(0, target);
  }, [anchor, rows, domain, scale, today, todayX]);

  const visible = rows.slice(start, end);
  const windowHeight = (end - start) * ROW_HEIGHT;
  const rowY = (index: number) => (index - start) * ROW_HEIGHT;

  return (
    <>
      {domain.clamped ? (
        <p
          role="status"
          data-testid="gantt-clamped"
          className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900 dark:bg-amber-950/40 dark:text-amber-200"
        >
          {t("gantt.clamped", { years: WINDOW_YEARS })}
        </p>
      ) : null}
      <div
        ref={scroller}
        data-testid="gantt-scroller"
        role="group"
        aria-label={t("gantt.label")}
        className={cn(
          "relative h-[min(68dvh,46rem)] min-h-80 overflow-auto rounded-xl border bg-background",
          className,
        )}
      >
        <div className="relative" style={{ width: totalWidth, height: GANTT_HEADER_HEIGHT + rows.length * ROW_HEIGHT }}>
          <div
            className="sticky top-0 z-20 flex border-b bg-background"
            style={{ width: totalWidth, height: GANTT_HEADER_HEIGHT }}
          >
            <div
              className="sticky left-0 z-30 flex shrink-0 items-end border-r bg-background px-2 pb-1.5 text-xs font-medium text-muted-foreground"
              style={{ width: GANTT_LABEL_WIDTH }}
            >
              {t("columns.work")}
            </div>
            <svg width={timelineWidth} height={GANTT_HEADER_HEIGHT} aria-hidden className="shrink-0">
              {months.map((tick) => (
                <g key={`m${tick.x}`}>
                  <line x1={tick.x} x2={tick.x} y1={0} y2={GANTT_HEADER_HEIGHT} className="stroke-border" />
                  <text x={tick.x + 4} y={16} className="fill-muted-foreground text-[11px]">
                    {tick.label}
                  </text>
                </g>
              ))}
              {weeks.map((tick) => (
                <text key={`w${tick.x}`} x={tick.x + 3} y={44} className="fill-muted-foreground text-[10px]">
                  {tick.label}
                </text>
              ))}
              <line
                x1={todayX}
                x2={todayX}
                y1={20}
                y2={GANTT_HEADER_HEIGHT}
                className="stroke-red-500"
                strokeWidth={1.5}
              />
              <text x={todayX + 4} y={29} className="fill-red-600 text-[10px] font-medium dark:fill-red-400">
                {t("gantt.today")}
              </text>
            </svg>
          </div>

          <div className="absolute left-0" style={{ top: GANTT_HEADER_HEIGHT + start * ROW_HEIGHT, width: totalWidth }}>
            {visible.map((row) => {
              const grouped = row.getIsGrouped();
              const node = grouped ? nodes.get(String(row.groupingValue)) : undefined;
              return (
                <div key={row.id} className="flex" style={{ height: ROW_HEIGHT }}>
                  <div
                    className={cn(
                      "sticky left-0 z-10 flex shrink-0 items-center gap-2 border-r border-b bg-background px-2 text-sm",
                      grouped && "bg-muted font-medium",
                    )}
                    style={{ width: GANTT_LABEL_WIDTH }}
                  >
                    {grouped ? (
                      <button
                        type="button"
                        onClick={row.getToggleExpandedHandler()}
                        aria-expanded={row.getIsExpanded()}
                        className="flex min-w-0 flex-1 items-center gap-1.5 text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
                      >
                        {row.getIsExpanded() ? (
                          <ChevronDown className="size-4 shrink-0" aria-hidden />
                        ) : (
                          <ChevronRight className="size-4 shrink-0" aria-hidden />
                        )}
                        <span className="truncate" title={node?.label}>
                          {node?.label ?? t("unknownNode")}
                        </span>
                      </button>
                    ) : (
                      <>
                        <span className="shrink-0 font-mono text-xs text-muted-foreground">
                          {row.original.work_code}
                        </span>
                        <span className="min-w-0 flex-1 truncate" title={row.original.work_name}>
                          {row.original.work_name}
                        </span>
                        {row.original.progress ? (
                          <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                            {formatPercent(row.original.progress.percent)}
                          </span>
                        ) : null}
                      </>
                    )}
                  </div>
                  <div className="shrink-0 border-b" style={{ width: timelineWidth }} />
                </div>
              );
            })}

            <svg
              width={timelineWidth}
              height={windowHeight}
              className="absolute top-0"
              style={{ left: GANTT_LABEL_WIDTH, pointerEvents: "none" }}
              data-testid="gantt-canvas"
            >
              <defs>
                <marker id="gantt-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
                  <path d="M0,0 L8,4 L0,8 z" className="fill-slate-500" />
                </marker>
              </defs>
              {gridlines.map((tick) => (
                <line key={`g${tick.x}`} x1={tick.x} x2={tick.x} y1={0} y2={windowHeight} className="stroke-border/70" />
              ))}
              <line x1={todayX} x2={todayX} y1={0} y2={windowHeight} className="stroke-red-500" strokeWidth={1.5} strokeDasharray="4 3" />

              {visible.map((row, offset) => {
                const top = offset * ROW_HEIGHT;
                if (row.getIsGrouped()) {
                  const first = row.getValue<string>("plan_start");
                  const last = row.getValue<string>("plan_end");
                  if (!first || !last) return null;
                  const x1 = xStart(domain, scale, first);
                  const x2 = xEnd(domain, scale, last);
                  return (
                    <rect
                      key={row.id}
                      x={x1}
                      y={top + 13}
                      width={Math.max(x2 - x1, MIN_BAR)}
                      height={6}
                      rx={2}
                      className="fill-foreground/35"
                    />
                  );
                }
                const item = row.original;
                const progress = item.progress;
                const state = progress && isItemState(progress.state) ? progress.state : "not_started";
                const x1 = xStart(domain, scale, item.plan_start);
                const x2 = xEnd(domain, scale, item.plan_end);
                const barWidth = Math.max(x2 - x1, MIN_BAR);
                const factStart = progress?.first_fact_date ?? null;
                const factEnd = progress?.last_fact_date ?? factStart;
                const fx1 = factStart ? xStart(domain, scale, factStart) : 0;
                const fx2 = factEnd ? xEnd(domain, scale, factEnd) : 0;
                const title = [
                  `${item.work_code} ${item.work_name}`,
                  `${t("gantt.plan")}: ${formatShortDate(item.plan_start)} — ${formatShortDate(item.plan_end)}, ${formatVolume(item.plan_volume)} ${item.unit}`,
                  progress
                    ? `${t("gantt.fact")}: ${formatVolume(progress.confirmed_volume)} ${item.unit} (${formatPercent(progress.percent)}), ${t("columns.delay")}: ${formatDelay(progress.delay_days)}`
                    : "",
                ]
                  .filter(Boolean)
                  .join("\n");
                return (
                  <g
                    key={row.id}
                    data-testid="gantt-bar"
                    style={{ pointerEvents: "auto", cursor: onOpen ? "pointer" : "default" }}
                    onClick={onOpen ? () => onOpen(item) : undefined}
                  >
                    <title>{title}</title>
                    <rect
                      x={x1}
                      y={top + PLAN_TOP}
                      width={barWidth}
                      height={PLAN_HEIGHT}
                      rx={3}
                      className={BAR_CLASSES[state].plan}
                    />
                    {factStart ? (
                      <rect
                        x={fx1}
                        y={top + FACT_TOP}
                        width={Math.max(fx2 - fx1, MIN_BAR)}
                        height={FACT_HEIGHT}
                        rx={2}
                        className={BAR_CLASSES[state].fact}
                      />
                    ) : null}
                    {progress && toNumber(progress.percent) > 0 ? (
                      <text
                        x={Math.max(x2, fx2) + 5}
                        y={top + 21}
                        className="fill-muted-foreground stroke-background text-[10px] [paint-order:stroke]"
                        strokeWidth={3}
                      >
                        {formatPercent(progress.percent)}
                      </text>
                    ) : null}
                  </g>
                );
              })}

              {arrows.map((arrow) => {
                const from = rows[arrow.from].original;
                const to = rows[arrow.to].original;
                const x1 = xEnd(domain, scale, from.plan_end);
                const y1 = rowY(arrow.from) + PLAN_TOP + PLAN_HEIGHT / 2;
                const x2 = xStart(domain, scale, to.plan_start);
                const y2 = rowY(arrow.to) + PLAN_TOP + PLAN_HEIGHT / 2;
                return (
                  <path
                    key={arrow.id}
                    d={arrowPath(x1, y1, x2, y2)}
                    fill="none"
                    className="stroke-slate-500"
                    strokeWidth={1}
                    markerEnd="url(#gantt-arrow)"
                    data-testid="gantt-arrow"
                  />
                );
              })}
            </svg>
          </div>
        </div>
        {rows.length === 0 ? (
          <p className="absolute inset-x-0 top-24 text-center text-sm text-muted-foreground">
            {t("empty")}
          </p>
        ) : null}
      </div>
    </>
  );
}

// Легенда под диаграммой: что значат полосы и цвета.
export function GanttLegend() {
  const t = useTranslations("Schedule.gantt");
  return (
    <ul className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
      <li className="flex items-center gap-1.5">
        <span className="h-2.5 w-6 rounded-sm border border-sky-600 bg-sky-500/25" aria-hidden />
        {t("legendPlan")}
      </li>
      <li className="flex items-center gap-1.5">
        <span className="h-1.5 w-6 rounded-sm bg-sky-600" aria-hidden />
        {t("legendFact")}
      </li>
      <li className="flex items-center gap-1.5">
        <span className="h-2.5 w-6 rounded-sm border border-red-600 bg-red-500/25" aria-hidden />
        {t("legendOverdue")}
      </li>
      <li className="flex items-center gap-1.5">
        <span className="h-3 w-px bg-red-500" aria-hidden />
        {t("today")}
      </li>
    </ul>
  );
}
