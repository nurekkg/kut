import { expect, test } from "../helpers/test";

import { apiLogin, get, projectId } from "../helpers/api";
import { WEST } from "../helpers/env";
import { desktopContext, shot, signIn } from "../helpers/ui";

// Сценарий 3: субподрядчик не видит очередь подтверждения и чужой объект.
test("субподрядчик не видит /approvals и чужой объект", async ({ browser, request }) => {
  const techdir = await apiLogin(request, "techdir");
  const west = await projectId(request, techdir, WEST);

  const desk = await desktopContext(browser);
  const page = await desk.newPage();
  await signIn(page, "sub_a");

  // В навигации нет раздела «На согласовании», на самой странице - отказ.
  await expect(page.getByRole("link", { name: "На согласовании" })).toHaveCount(0);
  await page.goto("/approvals");
  await expect(page.getByTestId("approvals-forbidden")).toBeVisible();
  await expect(page.getByTestId("approval")).toHaveCount(0);
  await shot(page, "access-1-approvals-forbidden");

  // Чужой объект (у субподрядчика А только «Восток»): страница и график отвечают «не найден».
  await page.goto(`/projects/${west}`);
  await expect(page.getByText("Объект не найден или у вас нет к нему доступа.")).toBeVisible();
  await shot(page, "access-2-foreign-object");
  await page.goto(`/projects/${west}/schedule`);
  await expect(page.getByText("Объект не найден или у вас нет к нему доступа.")).toBeVisible();

  // График своего объекта субподрядчик читает: без веса работ и без правки (вес после S9 раскроет
  // смету, tz-changes §6).
  const own = await projectId(request, techdir, "ЖК «Восток» (dev)");
  await page.goto(`/projects/${own}/schedule`);
  await expect(page.getByTestId("schedule-row").first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("columnheader", { name: "Работа" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Вес" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Добавить работу" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Изменить: / })).toHaveCount(0);
  await shot(page, "access-3-subcontractor-schedule");

  // То же на уровне API: чужие данные не отдаются ни чтением, ни действием.
  const sub = await apiLogin(request, "sub_a");
  for (const path of [
    `/api/v1/projects/${west}/schedule`,
    `/api/v1/projects/${west}/work-actuals`,
    `/api/v1/projects/${west}/readiness`,
  ]) {
    const response = await request.get(path, { headers: { Authorization: `Bearer ${sub.token}` } });
    expect(response.status(), path).toBe(404);
  }
  // Свой объект открыт только на чтение: подтверждать факт и править ГПР субподрядчик не может.
  const facts = await get<{ id: string; status: string }[]>(
    request,
    sub,
    `/api/v1/projects/${own}/work-actuals`,
    { limit: 5 },
  );
  expect(facts.every((fact) => fact.status === "approved")).toBeTruthy();
  const approve = await request.post(`/api/v1/projects/${own}/work-actuals/${facts[0].id}/approve`, {
    headers: { Authorization: `Bearer ${sub.token}` },
  });
  expect(approve.status()).toBe(403);
  const photos = await request.get(`/api/v1/projects/${own}/photos`, {
    headers: { Authorization: `Bearer ${sub.token}` },
  });
  expect(photos.status()).toBe(403);

  await desk.close();
});
