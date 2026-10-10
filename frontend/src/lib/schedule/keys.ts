export const scheduleKey = (projectId: string) => ["projects", projectId, "schedule"] as const;
export const dependenciesKey = (projectId: string) =>
  ["projects", projectId, "dependencies"] as const;
export const readinessKey = (projectId: string) => ["projects", projectId, "readiness"] as const;
export const nodeReadinessKey = (projectId: string, nodeId: string) =>
  ["projects", projectId, "readiness", nodeId] as const;
export const nodeScheduleKey = (projectId: string, nodeId: string) =>
  ["projects", projectId, "node-schedule", nodeId] as const;
export const actualsKey = (projectId: string, scope: string) =>
  ["projects", projectId, "actuals", scope] as const;
export const photosKey = (projectId: string, scope: string) =>
  ["projects", projectId, "photos", scope] as const;

// Любое подтверждение, правка факта или ГПР меняет проценты: сбрасываем всё, что от них зависит.
export const PROGRESS_PREFIXES = [
  "schedule",
  "dependencies",
  "readiness",
  "node-schedule",
  "actuals",
  "photos",
  "dashboard",
] as const;
