import { expect, type APIRequestContext, type APIResponse } from "@playwright/test";
import { randomUUID } from "node:crypto";
import fs from "node:fs";

import { EAST, GEO, PASSWORD, today } from "./env";

// Обрыв соединения и ответы 5xx на слабой машине бывают, пока PostgreSQL восстанавливается после
// сбоя: запрос повторяется несколько раз (создающие запросы несут один и тот же Idempotency-Key).
async function resilient(label: string, send: () => Promise<APIResponse>): Promise<APIResponse> {
  let last = "";
  for (let attempt = 0; attempt < 6; attempt += 1) {
    try {
      const response = await send();
      if (response.status() < 500) return response;
      last = `${response.status()} ${await response.text()}`;
    } catch (error) {
      last = String(error);
    }
    await new Promise((resolve) => setTimeout(resolve, 5000));
  }
  throw new Error(`${label}: нет ответа после повторов: ${last}`);
}

// Подготовка данных и проверка итогов идут через тот же прокси веба (/api/v1), что и браузер.
export type Session = { token: string };

export async function apiLogin(request: APIRequestContext, user: string): Promise<Session> {
  const response = await resilient("вход", () =>
    request.post("/api/v1/auth/login", {
      data: { identifier: `${user}@kut.test`, password: PASSWORD },
    }),
  );
  expect(response.ok(), `вход ${user}: ${await response.text()}`).toBeTruthy();
  const body = (await response.json()) as { status: string; access_token?: string };
  expect(body.status, `у ${user} включена 2FA`).toBe("ok");
  return { token: body.access_token ?? "" };
}

// Чей это токен: поле sub в JWT (подпись не проверяем, это сверка в тестах, а не защита).
export function jwtSubject(token: string): string {
  const payload = (token.split(".")[1] ?? "").replace(/-/g, "+").replace(/_/g, "/");
  const body = JSON.parse(Buffer.from(payload, "base64").toString("utf8")) as { sub?: string };
  return body.sub ?? "";
}

function headers(session: Session, key?: string): Record<string, string> {
  return { Authorization: `Bearer ${session.token}`, ...(key ? { "Idempotency-Key": key } : {}) };
}

export async function get<T>(
  request: APIRequestContext,
  session: Session,
  path: string,
  params?: Record<string, string | number | boolean>,
): Promise<T> {
  const response = await resilient(`GET ${path}`, () =>
    request.get(path, { headers: headers(session), params }),
  );
  expect(response.ok(), `GET ${path}: ${response.status()} ${await response.text()}`).toBeTruthy();
  return (await response.json()) as T;
}

export async function post<T>(
  request: APIRequestContext,
  session: Session,
  path: string,
  data: unknown,
): Promise<T> {
  const key = randomUUID();
  const response = await resilient(`POST ${path}`, () =>
    request.post(path, { headers: headers(session, key), data }),
  );
  expect(response.ok(), `POST ${path}: ${response.status()} ${await response.text()}`).toBeTruthy();
  return (await response.json()) as T;
}

type Named = { id: string; name: string; type?: string; parent_id?: string | null };

export async function projectId(
  request: APIRequestContext,
  session: Session,
  name: string,
): Promise<string> {
  const list = await get<Named[]>(request, session, "/api/v1/projects");
  const found = list.find((project) => project.name === name);
  expect(found, `объект ${name} не найден`).toBeTruthy();
  return found?.id ?? "";
}

export type Plan = {
  projectId: string;
  nodeId: string;
  nodeName: string;
  workId: string;
  workName: string;
  itemId: string;
};

// Новое помещение в «Востоке» с одной работой ГПР на 100 м3: так процент узла после факта 40
// известен заранее (40%), а прежние данные демо-объекта не мешают.
export async function createPlan(request: APIRequestContext, session: Session): Promise<Plan> {
  const project = await projectId(request, session, EAST);
  const tree = await get<Named[]>(request, session, `/api/v1/projects/${project}/tree`);
  const floor = tree.find((node) => node.type === "floor");
  expect(floor, "в дереве «Востока» нет этажей").toBeTruthy();
  const nodeName = `E2E ${Date.now()}`;
  const node = await post<Named>(request, session, `/api/v1/projects/${project}/nodes`, {
    type: "room",
    name: nodeName,
    parent_id: floor?.id,
  });
  const works = await get<{ id: string; name: string }[]>(request, session, "/api/v1/works", {
    q: "БЖБ-004",
    limit: 5,
  });
  const work = works[0];
  const item = await post<{ id: string }>(request, session, `/api/v1/projects/${project}/schedule`, {
    node_id: node.id,
    work_id: work.id,
    plan_start: today(-2),
    plan_end: today(8),
    plan_volume: "100",
  });
  return {
    projectId: project,
    nodeId: node.id,
    nodeName,
    workId: work.id,
    workName: work.name,
    itemId: item.id,
  };
}

export type Readiness = {
  project: { percent: string } | null;
  nodes: { node_id: string; subtree: { percent: string } }[];
};

export async function readiness(
  request: APIRequestContext,
  session: Session,
  project: string,
): Promise<{ project: number; node: (nodeId: string) => number }> {
  const body = await get<Readiness>(request, session, `/api/v1/projects/${project}/readiness`);
  return {
    project: Number(body.project?.percent ?? 0),
    node: (nodeId) =>
      Number(body.nodes.find((entry) => entry.node_id === nodeId)?.subtree.percent ?? 0),
  };
}

export type Fact = {
  id: string;
  status: string;
  volume: string;
  reject_reason: string | null;
  photo_count: number | null;
};

export async function factsOfNode(
  request: APIRequestContext,
  session: Session,
  project: string,
  nodeId: string,
): Promise<Fact[]> {
  return get<Fact[]>(request, session, `/api/v1/projects/${project}/work-actuals`, {
    node_id: nodeId,
  });
}

export async function remove(
  request: APIRequestContext,
  session: Session,
  path: string,
): Promise<void> {
  const response = await resilient(`DELETE ${path}`, () =>
    request.delete(path, { headers: headers(session) }),
  );
  expect(response.status(), `DELETE ${path}: ${await response.text()}`).toBe(204);
}

// Отправленный факт с одним снимком целиком через API: черновик, ссылка на загрузку, PUT в S3,
// подтверждение снимка, отправка на проверку. Возвращает id факта.
export async function submitFactWithPhoto(
  request: APIRequestContext,
  session: Session,
  plan: Plan,
  photoPath: string,
  volume = "10",
): Promise<string> {
  const base = `/api/v1/projects/${plan.projectId}`;
  const fact = await post<{ id: string }>(request, session, `${base}/work-actuals`, {
    node_id: plan.nodeId,
    work_id: plan.workId,
    volume,
    work_date: today(),
    latitude: GEO.latitude.toFixed(6),
    longitude: GEO.longitude.toFixed(6),
    accuracy_m: String(GEO.accuracy),
    submit: false,
  });
  const bytes = fs.readFileSync(photoPath);
  const issued = await post<{
    items: { key: string; upload_url: string; headers: Record<string, string> }[];
  }>(request, session, `${base}/photos/presign`, {
    work_actual_id: fact.id,
    files: [{ content_type: "image/jpeg", size_bytes: bytes.length }],
  });
  const upload = issued.items[0];
  const stored = await request.put(upload.upload_url, { headers: upload.headers, data: bytes });
  expect(stored.ok(), `PUT в S3: ${stored.status()} ${await stored.text()}`).toBeTruthy();
  await post(request, session, `${base}/photos/confirm`, {
    work_actual_id: fact.id,
    items: [
      {
        key: upload.key,
        latitude: GEO.latitude.toFixed(6),
        longitude: GEO.longitude.toFixed(6),
        taken_at: new Date().toISOString(),
      },
    ],
  });
  await post(request, session, `${base}/work-actuals/${fact.id}/submit`, undefined);
  return fact.id;
}
