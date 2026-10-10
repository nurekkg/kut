import { expect, type APIRequestContext } from "@playwright/test";

import { get, post, type Session } from "./api";
import { EAST } from "./env";

// Ответы дашбордов S4 (поля, которыми пользуются сценарии): проценты приходят строками.
export type Progress = { percent: string; planned_percent: string; deviation: string };
export type ObjectDashboard = { progress: Progress | null };
export type GroupRow = { project_id: string; name: string; progress: Progress | null };

export function percentOf(progress: Progress | null | undefined): number | null {
  return progress ? Number(progress.percent) : null;
}

export async function objectDashboard(
  request: APIRequestContext,
  session: Session,
  projectId: string,
): Promise<ObjectDashboard> {
  return get<ObjectDashboard>(request, session, `/api/v1/dashboards/object/${projectId}`);
}

export async function groupRows(request: APIRequestContext, session: Session): Promise<GroupRow[]> {
  const body = await get<{ rows: GroupRow[] }>(request, session, "/api/v1/dashboards/group");
  return body.rows;
}

// Пустой объект того же юрлица, что и «Восток»: для сценариев, которым нельзя мешать ГПР демо-данных.
export async function emptyProject(
  request: APIRequestContext,
  session: Session,
  name: string,
): Promise<string> {
  const projects = await get<{ name: string; legal_entity_id: string }[]>(
    request,
    session,
    "/api/v1/projects",
  );
  const east = projects.find((project) => project.name === EAST);
  expect(east, `объект ${EAST} найден`).toBeTruthy();
  const created = await post<{ id: string }>(request, session, "/api/v1/projects", {
    name,
    legal_entity_id: east?.legal_entity_id,
  });
  return created.id;
}
