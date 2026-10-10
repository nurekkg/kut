import { useQuery, type QueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { API } from "@/lib/projects/keys";

import {
  dependenciesKey,
  nodeReadinessKey,
  nodeScheduleKey,
  PROGRESS_PREFIXES,
  readinessKey,
  scheduleKey,
} from "./keys";
import type {
  Dependency,
  NodeReadinessDetail,
  ProjectReadiness,
  ScheduleItem,
} from "./types";

// Объект целиком: до 10 000 строк одним ответом (на «Востоке» около 4 800, ответ сжимается).
export function useSchedule(projectId: string) {
  return useQuery({
    queryKey: scheduleKey(projectId),
    queryFn: () =>
      api.get<ScheduleItem[]>(`${API}/projects/${projectId}/schedule`, {
        with_progress: true,
        limit: 10_000,
      }),
    staleTime: 30_000,
  });
}

export function useDependencies(projectId: string) {
  return useQuery({
    queryKey: dependenciesKey(projectId),
    queryFn: () =>
      api.get<Dependency[]>(`${API}/projects/${projectId}/schedule/dependencies`, {
        limit: 20_000,
      }),
    staleTime: 30_000,
  });
}

export function useProjectReadiness(projectId: string) {
  return useQuery({
    queryKey: readinessKey(projectId),
    queryFn: () => api.get<ProjectReadiness>(`${API}/projects/${projectId}/readiness`),
    staleTime: 30_000,
  });
}

export function useNodeReadiness(projectId: string, nodeId: string) {
  return useQuery({
    queryKey: nodeReadinessKey(projectId, nodeId),
    queryFn: () =>
      api.get<NodeReadinessDetail>(`${API}/projects/${projectId}/nodes/${nodeId}/readiness`),
    staleTime: 15_000,
  });
}

// Работы узла и всех его потомков с готовностью: вкладка «Этапы работ» карточки узла.
export function useNodeSchedule(projectId: string, nodeId: string) {
  return useQuery({
    queryKey: nodeScheduleKey(projectId, nodeId),
    queryFn: () =>
      api.get<ScheduleItem[]>(`${API}/projects/${projectId}/schedule`, {
        node_id: nodeId,
        subtree: true,
        with_progress: true,
        limit: 1000,
      }),
    staleTime: 15_000,
  });
}

// Подтверждение, правка факта или ГПР меняют проценты на нескольких экранах сразу.
export function invalidateProgress(queryClient: QueryClient, projectId?: string): Promise<void> {
  const prefixes: readonly unknown[] = PROGRESS_PREFIXES;
  return queryClient.invalidateQueries({
    predicate: (query) =>
      query.queryKey[0] === "projects" &&
      (projectId === undefined || query.queryKey[1] === projectId) &&
      prefixes.includes(query.queryKey[2]),
  });
}

// Без объекта: все объекты сразу (очередь устройства не знает, какие экраны открыты).
export function invalidateAllProgress(queryClient: QueryClient): Promise<void> {
  return invalidateProgress(queryClient);
}
