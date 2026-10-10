"use client";

import { useTranslations } from "next-intl";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { ChartDetails } from "@/components/dashboards/chart-details";
import {
  formatChartPercent,
  formatDay,
  formatDayMonth,
  formatPercent,
} from "@/lib/dashboards/format";
import type { FactPoint, SeriesPoint } from "@/lib/dashboards/types";
import { dayNumber, isoFromDay } from "@/lib/schedule/dates";

const TICK_LIMIT = 12;
const AXIS_TICK = { fill: "var(--muted-foreground)", fontSize: 12 };
const TOOLTIP_STYLE = {
  background: "var(--card)",
  border: "1px solid var(--border)",
  borderRadius: 8,
  color: "var(--card-foreground)",
  fontSize: 12,
};

type Row = { day: number; date: string; plan: number | null; fact: number | null };

// showPlan=false - вид заказчика: плановой линии и колонки плана нет, в данных их тоже нет.
export function SCurve({
  series,
  asOf,
  showPlan,
}: {
  series: Array<SeriesPoint | FactPoint>;
  asOf: string;
  showPlan: boolean;
}) {
  const t = useTranslations("Dashboard");
  const rows: Row[] = series.map((point) => ({
    day: dayNumber(point.date),
    date: point.date,
    plan: "plan_percent" in point ? Number(point.plan_percent) : null,
    fact: point.fact_percent === null ? null : Number(point.fact_percent),
  }));
  const step = Math.max(1, Math.ceil(rows.length / TICK_LIMIT));
  const ticks = rows.filter((_, index) => index % step === 0).map((row) => row.day);
  const end = rows.findLast((row): row is Row & { fact: number } => row.fact !== null);

  return (
    <>
      <div className="h-[220px] w-full min-w-0 md:h-[320px]">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart
            data={rows}
            margin={{ top: 28, right: 24, bottom: 4, left: 0 }}
            accessibilityLayer
          >
            <CartesianGrid vertical={false} stroke="var(--border)" />
            <XAxis
              dataKey="day"
              type="number"
              domain={["dataMin", "dataMax"]}
              ticks={ticks}
              tickFormatter={(value: number) => formatDayMonth(isoFromDay(value))}
              tick={AXIS_TICK}
              tickLine={false}
              axisLine={{ stroke: "var(--border)" }}
            />
            <YAxis
              domain={[0, 100]}
              ticks={[0, 25, 50, 75, 100]}
              tickFormatter={(value: number) => `${value}%`}
              tick={AXIS_TICK}
              tickLine={false}
              axisLine={false}
              width={44}
            />
            <Tooltip
              cursor={{ stroke: "var(--muted-foreground)", strokeWidth: 1 }}
              contentStyle={TOOLTIP_STYLE}
              labelFormatter={(value) => formatDay(isoFromDay(Number(value)))}
              formatter={(value, name) => [formatChartPercent(value), name]}
            />
            <Legend verticalAlign="top" align="left" iconType="plainline" />
            <ReferenceLine
              x={dayNumber(asOf)}
              stroke="var(--muted-foreground)"
              strokeWidth={1}
              label={{
                value: t("series.today"),
                position: "insideBottomRight",
                fill: "var(--muted-foreground)",
                fontSize: 12,
              }}
            />
            {showPlan ? (
              <Line
                type="linear"
                dataKey="plan"
                name={t("series.plan")}
                stroke="var(--muted-foreground)"
                strokeWidth={2}
                strokeDasharray="6 4"
                strokeLinecap="round"
                dot={false}
                isAnimationActive={false}
              />
            ) : null}
            <Line
              type="linear"
              dataKey="fact"
              name={t("series.fact")}
              stroke="var(--chart-1)"
              strokeWidth={2}
              strokeLinecap="round"
              connectNulls={false}
              dot={false}
              isAnimationActive={false}
            />
            {end ? (
              <ReferenceDot
                x={end.day}
                y={end.fact}
                r={4}
                fill="var(--chart-1)"
                stroke="var(--card)"
                strokeWidth={2}
                label={{
                  value: formatPercent(end.fact),
                  position: "top",
                  fill: "var(--foreground)",
                  fontSize: 12,
                  fontWeight: 600,
                }}
              />
            ) : null}
          </LineChart>
        </ResponsiveContainer>
      </div>
      <ChartDetails>
        <table className="w-full text-sm">
          <caption className="sr-only">
            {showPlan ? t("series.title") : t("series.factTitle")}
          </caption>
          <thead>
            <tr className="border-b text-left">
              <th scope="col" className="py-2 font-medium">
                {t("series.date")}
              </th>
              {showPlan ? (
                <th scope="col" className="py-2 text-right font-medium">
                  {t("series.planPercent")}
                </th>
              ) : null}
              <th scope="col" className="py-2 text-right font-medium">
                {t("series.factPercent")}
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.date} className="border-b last:border-0">
                <td className="py-2">{formatDay(row.date)}</td>
                {showPlan ? (
                  <td className="py-2 text-right tabular-nums">{formatPercent(row.plan)}</td>
                ) : null}
                <td className="py-2 text-right tabular-nums">{formatPercent(row.fact)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </ChartDetails>
    </>
  );
}
