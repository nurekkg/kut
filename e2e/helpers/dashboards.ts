import { expect, type APIRequestContext, type Locator } from "@playwright/test";

import { approvalTotal, APPROVAL_LIMIT } from "../../frontend/src/lib/approvals-count";
import { get, post, type Session } from "./api";
import { EAST } from "./env";

// Ответы дашбордов S4 (поля, которыми пользуются сценарии): проценты приходят строками.
export type Progress = { percent: string; planned_percent: string; deviation: string };
export type NodeTile = { node_id: string; name: string; progress: Progress };
export type ObjectDashboard = { progress: Progress | null; nodes: NodeTile[] };
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

// Ключи плана в ответе заказчика: те же подстроки, что в backend/tests/test_dashboards_api.py.
const PLAN_KEY_MARKERS = ["plan", "deviation", "delay", "lag", "overdue", "weight", "budget", "problem"];

export function planKeysOf(value: unknown): string[] {
  const found: string[] = [];
  const walk = (node: unknown): void => {
    if (Array.isArray(node)) {
      node.forEach(walk);
    } else if (node !== null && typeof node === "object") {
      for (const [key, inner] of Object.entries(node)) {
        if (PLAN_KEY_MARKERS.some((marker) => key.includes(marker))) found.push(key);
        walk(inner);
      }
    }
  };
  walk(value);
  return found;
}

// Очередь «На согласовании» по всем объектам, видимым сессии: та же сумма, что у счётчика в навигации.
export async function approvalsTotal(
  request: APIRequestContext,
  session: Session,
): Promise<{ total: number; capped: boolean }> {
  const projects = await get<{ id: string }[]>(request, session, "/api/v1/projects");
  const counts: number[] = [];
  for (const project of projects) {
    const facts = await get<unknown[]>(
      request,
      session,
      `/api/v1/projects/${project.id}/work-actuals`,
      { status: "submitted", limit: APPROVAL_LIMIT },
    );
    counts.push(facts.length);
  }
  return approvalTotal(counts, APPROVAL_LIMIT);
}

// Число из подписи вида «61,96 %» (русская запятая, неразрывные пробелы).
export function numberIn(text: string): number {
  const match = text.replace(/\s/g, "").replace(",", ".").match(/-?\d+(\.\d+)?/);
  expect(match, `число в подписи «${text}»`).toBeTruthy();
  return Number(match?.[0]);
}

// Полосы на одной шкале: ширина пропорциональна значению. Узлы с нулём не сравниваем.
export async function expectProportional(rects: Locator, values: number[]): Promise<void> {
  const widths = await rects.evaluateAll((nodes) =>
    nodes.map((node) => node.getBoundingClientRect().width),
  );
  const ratios = values
    .map((value, index) => (value > 0 ? widths[index] / value : null))
    .filter((ratio): ratio is number => ratio !== null);
  if (ratios.length < 2) return;
  expect(Math.max(...ratios) / Math.min(...ratios)).toBeLessThan(1.05);
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
