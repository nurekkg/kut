import type { TreeIndex } from "@/lib/projects/tree";

import { toNumber } from "./format";
import type { ScheduleItem } from "./types";

export type NodeInfo = {
  id: string;
  name: string;
  label: string;
  depth: number;
  order: number;
};

// Узлы в порядке показа структуры (обход в глубину) и с готовой подписью «Корпус 1 › Секция А».
// По этому порядку идут группы ГПР: таблица читается так же, как дерево слева.
export function buildNodeInfo(index: TreeIndex): Map<string, NodeInfo> {
  const info = new Map<string, NodeInfo>();
  let order = 0;
  const walk = (parent: string | null, prefix: string, depth: number) => {
    for (const node of index.children.get(parent) ?? []) {
      const label = prefix ? `${prefix} › ${node.name}` : node.name;
      info.set(node.id, { id: node.id, name: node.name, label, depth, order: order++ });
      walk(node.id, label, depth + 1);
    }
  };
  walk(null, "", 0);
  return info;
}

export type ScheduleRow = ScheduleItem & { node_order: number };

export function toRows(items: ScheduleItem[], info: Map<string, NodeInfo>): ScheduleRow[] {
  return items
    .map((item) => ({
      ...item,
      node_order: info.get(item.node_id)?.order ?? Number.MAX_SAFE_INTEGER,
    }))
    .sort(
      (a, b) =>
        a.node_order - b.node_order ||
        a.plan_start.localeCompare(b.plan_start) ||
        a.work_code.localeCompare(b.work_code, "ru", { numeric: true }) ||
        a.id.localeCompare(b.id),
    );
}

export const LAG_FILTERS = ["all", "late", "overdue", "ahead", "done"] as const;
export type LagFilter = (typeof LAG_FILTERS)[number];

export type ScheduleFilters = {
  nodeId: string;
  workId: string;
  lag: LagFilter;
  query: string;
};

export const NO_FILTERS: ScheduleFilters = { nodeId: "", workId: "", lag: "all", query: "" };

export function hasFilters(filters: ScheduleFilters): boolean {
  return (
    filters.nodeId !== "" ||
    filters.workId !== "" ||
    filters.lag !== "all" ||
    filters.query.trim() !== ""
  );
}

export function matchesLag(item: ScheduleItem, lag: LagFilter): boolean {
  if (lag === "all") return true;
  const progress = item.progress;
  if (!progress) return false;
  switch (lag) {
    case "late":
      return progress.state !== "done" && toNumber(progress.delay_days) > 0;
    case "overdue":
      return progress.state === "overdue" || progress.state === "overdue_no_fact";
    case "ahead":
      return progress.state !== "done" && toNumber(progress.delay_days) < 0;
    case "done":
      return progress.state === "done";
  }
}

export function filterRows(
  rows: ScheduleRow[],
  filters: ScheduleFilters,
  index: TreeIndex,
): ScheduleRow[] {
  if (!hasFilters(filters)) return rows;
  const query = filters.query.trim().toLowerCase();
  return rows.filter((row) => {
    if (filters.workId && row.work_id !== filters.workId) return false;
    if (filters.nodeId) {
      const node = index.byId.get(row.node_id);
      if (!node || !node.path.includes(filters.nodeId)) return false;
    }
    if (!matchesLag(row, filters.lag)) return false;
    if (query && !`${row.work_code} ${row.work_name}`.toLowerCase().includes(query)) return false;
    return true;
  });
}

// Виды работ, которые есть в ГПР: список для фильтра по работе.
export function workOptions(rows: ScheduleRow[]): { id: string; label: string }[] {
  const seen = new Map<string, string>();
  for (const row of rows) {
    if (!seen.has(row.work_id)) seen.set(row.work_id, `${row.work_code} ${row.work_name}`);
  }
  return [...seen.entries()]
    .map(([id, label]) => ({ id, label }))
    .sort((a, b) => a.label.localeCompare(b.label, "ru", { numeric: true }));
}
