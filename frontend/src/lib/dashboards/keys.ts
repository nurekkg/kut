export const DASHBOARDS_ROOT = ["dashboards"] as const;
export const groupDashboardKey = ["dashboards", "group"] as const;
export const objectDashboardKey = (projectId: string) =>
  ["projects", projectId, "dashboard", "object"] as const;
export const portalDashboardKey = (projectId: string) =>
  ["projects", projectId, "dashboard", "portal"] as const;
export const dashboardProjectKey = (projectId: string) =>
  ["projects", projectId, "dashboard"] as const;
export const photoUrlKey = (projectId: string, photoId: string) =>
  ["projects", projectId, "photo-url", photoId] as const;
