"use client";

import { useTranslations } from "next-intl";
import {
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { ChartDetails } from "@/components/dashboards/chart-details";
import { formatChartPercent, formatPercent } from "@/lib/dashboards/format";
import type { NodeTile } from "@/lib/dashboards/types";
import { useMediaQuery } from "@/lib/use-media-query";

const VALUE_LABEL_LIMIT = 6;
const NAME_LIMIT = 24;
const ROW_HEIGHT = 44;
const AXIS_TICK = { fill: "var(--muted-foreground)", fontSize: 12 };
const TOOLTIP_STYLE = {
  background: "var(--card)",
  border: "1px solid var(--border)",
  borderRadius: 8,
  color: "var(--card-foreground)",
  fontSize: 12,
};

function NodeTick({ x, y, payload }: { x?: number; y?: number; payload?: { value?: string } }) {
  const full = payload?.value ?? "";
  const short = full.length > NAME_LIMIT ? `${full.slice(0, NAME_LIMIT - 1)}…` : full;
  return (
    <text x={x} y={y} dy={4} textAnchor="end" fontSize={12} fill="var(--muted-foreground)">
      <title>{full}</title>
      {short}
    </text>
  );
}

// Названия узлов повторяются (два «Корпус 1» в разных стадиях), а категория оси должна быть уникальной.
function uniqueLabels(nodes: NodeTile[]): string[] {
  const seen = new Map<string, number>();
  return nodes.map((node) => {
    const count = (seen.get(node.name) ?? 0) + 1;
    seen.set(node.name, count);
    return count === 1 ? node.name : `${node.name} (${count})`;
  });
}

export function NodeBars({ nodes }: { nodes: NodeTile[] }) {
  const t = useTranslations("Dashboard");
  const wide = useMediaQuery("(min-width: 768px)");
  const labels = uniqueLabels(nodes);
  const rows = nodes.map((node, index) => ({
    name: labels[index],
    plan: Number(node.progress.planned_percent),
    fact: Number(node.progress.percent),
  }));
  const longest = Math.min(NAME_LIMIT, Math.max(...rows.map((row) => row.name.length)));
  const axisWidth = Math.min(180, Math.max(96, longest * 7 + 12));
  const height = Math.max(wide ? 320 : 220, rows.length * ROW_HEIGHT + 56);

  return (
    <>
      <div className="w-full min-w-0" style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={rows}
            layout="vertical"
            margin={{ top: 28, right: 44, bottom: 0, left: 0 }}
            barGap={2}
            barCategoryGap="30%"
            accessibilityLayer
          >
            <CartesianGrid horizontal={false} stroke="var(--border)" />
            <XAxis
              type="number"
              domain={[0, 100]}
              ticks={[0, 25, 50, 75, 100]}
              tickFormatter={(value: number) => `${value}%`}
              tick={AXIS_TICK}
              tickLine={false}
              axisLine={false}
            />
            <YAxis
              type="category"
              dataKey="name"
              width={axisWidth}
              interval={0}
              tick={<NodeTick />}
              tickLine={false}
              axisLine={false}
            />
            <Tooltip
              cursor={{ fill: "var(--muted)" }}
              contentStyle={TOOLTIP_STYLE}
              formatter={(value, name) => [formatChartPercent(value), name]}
            />
            <Legend verticalAlign="top" align="left" iconType="square" />
            <Bar
              dataKey="plan"
              name={t("nodes.plan")}
              fill="var(--muted-foreground)"
              radius={[0, 4, 4, 0]}
              maxBarSize={24}
              isAnimationActive={false}
            />
            <Bar
              dataKey="fact"
              name={t("nodes.fact")}
              fill="var(--chart-1)"
              radius={[0, 4, 4, 0]}
              maxBarSize={24}
              isAnimationActive={false}
            >
              {rows.length <= VALUE_LABEL_LIMIT ? (
                <LabelList
                  dataKey="fact"
                  position="right"
                  formatter={formatChartPercent}
                  style={{ fill: "var(--foreground)", fontSize: 12 }}
                />
              ) : null}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <ChartDetails>
        <table className="w-full text-sm">
          <caption className="sr-only">{t("nodes.title")}</caption>
          <thead>
            <tr className="border-b text-left">
              <th scope="col" className="py-2 font-medium">
                {t("nodes.node")}
              </th>
              <th scope="col" className="py-2 text-right font-medium">
                {t("nodes.planPercent")}
              </th>
              <th scope="col" className="py-2 text-right font-medium">
                {t("nodes.factPercent")}
              </th>
            </tr>
          </thead>
          <tbody>
            {nodes.map((node) => (
              <tr key={node.node_id} className="border-b last:border-0">
                <td className="py-2">{node.name}</td>
                <td className="py-2 text-right tabular-nums">
                  {formatPercent(node.progress.planned_percent)}
                </td>
                <td className="py-2 text-right tabular-nums">
                  {formatPercent(node.progress.percent)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </ChartDetails>
    </>
  );
}
