import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { DownloadUrl } from "@/lib/field/types";
import { API } from "@/lib/projects/keys";

import { groupDashboardKey, objectDashboardKey, photoUrlKey, portalDashboardKey } from "./keys";
import type { GroupDashboardData, ObjectDashboardData, PortalDashboardData } from "./types";

const DASHBOARD_FRESH_MS = 15_000;
// Подписанная ссылка на фото живёт около двух минут: держим её минуту, дальше запрашиваем новую.
const PHOTO_URL_FRESH_MS = 60_000;

export function useObjectDashboard(projectId: string) {
  return useQuery({
    queryKey: objectDashboardKey(projectId),
    queryFn: () => api.get<ObjectDashboardData>(`${API}/dashboards/object/${projectId}`),
    staleTime: DASHBOARD_FRESH_MS,
  });
}

export function usePortalDashboard(projectId: string) {
  return useQuery({
    queryKey: portalDashboardKey(projectId),
    queryFn: () => api.get<PortalDashboardData>(`${API}/dashboards/portal/${projectId}`),
    staleTime: DASHBOARD_FRESH_MS,
  });
}

export function useGroupDashboard() {
  return useQuery({
    queryKey: groupDashboardKey,
    queryFn: () => api.get<GroupDashboardData>(`${API}/dashboards/group`),
    staleTime: DASHBOARD_FRESH_MS,
  });
}

export function usePhotoUrl(projectId: string, photoId: string) {
  return useQuery({
    queryKey: photoUrlKey(projectId, photoId),
    queryFn: () =>
      api.get<DownloadUrl>(`${API}/projects/${projectId}/photos/${photoId}/download-url`),
    staleTime: PHOTO_URL_FRESH_MS,
  });
}
