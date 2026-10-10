import { useQueries, useQuery, type UseQueryResult } from "@tanstack/react-query";
import { useMemo } from "react";

import { api } from "@/lib/api";
import { APPROVAL_LIMIT } from "@/lib/approvals-count";
import type { Actual } from "@/lib/field/types";
import { API, PROJECTS_KEY } from "@/lib/projects/keys";
import type { Project } from "@/lib/projects/types";
import { actualsKey } from "@/lib/schedule/keys";

// Лимит выборки на один объект (200) задан в approvals-count: счётчик и строка «всего» показывают
// «N+», если хотя бы один объект упёрся в него.
export { APPROVAL_LIMIT };

function combineQueues(results: UseQueryResult<Actual[], Error>[]) {
  return {
    facts: results
      .flatMap((result) => result.data ?? [])
      .sort((a, b) => a.created_at.localeCompare(b.created_at)),
    perProject: results.map((result) => result.data?.length ?? 0),
    pending: results.some((result) => result.isPending),
    error: results.find((result) => result.error)?.error ?? null,
  };
}

// Отправленные на проверку факты всех объектов человека. Очередь и счётчик в навигации читают
// одни и те же запросы: кэш общий, лишних обращений к серверу нет.
export function useApprovalQueue(enabled: boolean) {
  const projects = useQuery({
    queryKey: PROJECTS_KEY,
    queryFn: () => api.get<Project[]>(`${API}/projects`),
    enabled,
  });
  const ids = useMemo(() => (projects.data ?? []).map((project) => project.id), [projects.data]);
  const queue = useQueries({
    queries: ids.map((id) => ({
      queryKey: actualsKey(id, "submitted"),
      queryFn: () =>
        api.get<Actual[]>(`${API}/projects/${id}/work-actuals`, {
          status: "submitted",
          limit: APPROVAL_LIMIT,
        }),
      enabled,
      staleTime: 15_000,
      refetchInterval: 60_000,
    })),
    combine: combineQueues,
  });
  return {
    projects,
    ids,
    facts: queue.facts,
    perProject: queue.perProject,
    pending: enabled && (projects.isPending || queue.pending),
    error: projects.error ?? queue.error,
  };
}
