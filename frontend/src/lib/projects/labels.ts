import { useQueries, type UseQueryResult } from "@tanstack/react-query";

import { api } from "@/lib/api";

import { API, treeKey } from "./keys";
import { buildIndex, pathLabel } from "./tree";
import type { TreeNode } from "./types";

// Подписи узлов «Корпус 1 › Секция А › Этаж 5» для набора объектов. Функция стабильна, поэтому
// карта пересобирается только при смене деревьев, а не на каждую отрисовку.
function combineLabels(results: UseQueryResult<TreeNode[], Error>[]): Map<string, string> {
  const map = new Map<string, string>();
  for (const result of results) {
    const index = buildIndex(result.data ?? []);
    for (const node of index.byId.values()) map.set(node.id, pathLabel(index, node));
  }
  return map;
}

// Дерево каждого объекта уже в кэше после экрана объекта или мастера: лишних запросов нет.
export function useNodeLabels(projectIds: string[]): Map<string, string> {
  return useQueries({
    queries: projectIds.map((id) => ({
      queryKey: treeKey(id),
      queryFn: () => api.get<TreeNode[]>(`${API}/projects/${id}/tree`),
      staleTime: 5 * 60_000,
    })),
    combine: combineLabels,
  });
}
