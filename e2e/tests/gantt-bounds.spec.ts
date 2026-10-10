import { expect, test } from "../helpers/test";

import type { APIRequestContext } from "@playwright/test";
import { randomUUID } from "node:crypto";

import { apiLogin, get, post, projectId, remove, type Session } from "../helpers/api";
import { WEST, today } from "../helpers/env";
import { desktopContext, shot, signIn, watchErrors } from "../helpers/ui";

const PREFIX = "E2E Гант";

// Остатки прерванных прогонов (после тайм-аута до `finally` дело не доходит) убираем перед началом:
// «Запад» должен быть пуст, иначе в графике окажутся чужие строки.
async function removeLeftovers(
  request: APIRequestContext,
  session: Session,
  base: string,
): Promise<void> {
  const tree = await get<{ id: string; name: string }[]>(request, session, `${base}/tree`);
  const stale = tree.filter((node) => node.name.startsWith(PREFIX));
  if (stale.length === 0) return;
  const nodes = new Set(stale.map((node) => node.id));
  const items = await get<{ id: string; node_id: string }[]>(request, session, `${base}/schedule`, {
    limit: 10_000,
  });
  for (const item of items.filter((row) => nodes.has(row.node_id))) {
    await remove(request, session, `${base}/schedule/${item.id}`);
  }
  for (const node of stale) await remove(request, session, `${base}/nodes/${node.id}`);
}

// Даты ГПР держит БД (ревизия a1efb2b3d84b): вне 2000-2100 и длиннее 10 лет сервер не принимает.
// Клиентская защита Ганта остаётся на случай данных, которые придут в обход (старый сервер, ошибка):
// шкала ограничена окном вокруг «сегодня», полоса упирается в край, над диаграммой предупреждение.
// «Запад» в демо-данных пуст: строки заводятся тут же и удаляются в конце.
async function westNode(request: APIRequestContext, techdir: Session) {
  const west = await projectId(request, techdir, WEST);
  const base = `/api/v1/projects/${west}`;
  await removeLeftovers(request, techdir, base);
  const node = await post<{ id: string }>(request, techdir, `${base}/nodes`, {
    type: "building",
    name: `${PREFIX} ${Date.now()}`,
  });
  const works = await get<{ id: string }[]>(request, techdir, "/api/v1/works", { limit: 2 });
  return { west, base, node, works };
}

test("сервер не принимает даты ГПР вне границ и отвечает понятным кодом", async ({ request }) => {
  const techdir = await apiLogin(request, "techdir");
  const { base, node, works } = await westNode(request, techdir);
  try {
    for (const dates of [
      { plan_start: "1900-01-01", plan_end: "9999-12-31" },
      { plan_start: "1999-12-31", plan_end: "2000-01-31" },
      { plan_start: today(), plan_end: "2101-01-01" },
      { plan_start: "2026-09-01", plan_end: "2036-09-02" },
    ]) {
      const response = await request.post(`${base}/schedule`, {
        headers: { Authorization: `Bearer ${techdir.token}`, "Idempotency-Key": randomUUID() },
        data: { node_id: node.id, work_id: works[0].id, plan_volume: "10", ...dates },
      });
      expect(response.status(), JSON.stringify(dates)).toBe(422);
      expect((await response.json()).detail).toBe("schedule_dates_out_of_range");
    }
  } finally {
    await remove(request, techdir, `${base}/nodes/${node.id}`);
  }
});

test("Гант с датой из другого века в ответе: шкала ограничена, вкладка не виснет", async ({
  browser,
  request,
}) => {
  const techdir = await apiLogin(request, "techdir");
  const { west, base, node, works } = await westNode(request, techdir);
  const created: string[] = [];
  try {
    for (const [index, dates] of [
      { plan_start: today(-5), plan_end: today(20) },
      { plan_start: today(-3), plan_end: today(30) },
    ].entries()) {
      const item = await post<{ id: string }>(request, techdir, `${base}/schedule`, {
        node_id: node.id,
        work_id: works[index].id,
        plan_volume: "10",
        ...dates,
      });
      created.push(item.id);
    }

    const desk = await desktopContext(browser);
    const page = await desk.newPage();
    const errors = watchErrors(page);
    await signIn(page, "techdir");
    // Ответ сервера «портим»: у второй строки даты 1900 и 9999 (такие строки БД уже не пустит).
    await page.route(/\/schedule\?/, async (route) => {
      const response = await route.fetch();
      const rows = (await response.json()) as { id: string; plan_start: string; plan_end: string }[];
      for (const row of rows) {
        if (row.id === created[1]) {
          row.plan_start = "1900-01-01";
          row.plan_end = "9999-12-31";
        }
      }
      await route.fulfill({ response, json: rows });
    });
    await page.goto(`/projects/${west}/schedule`);
    await expect(page.getByTestId("schedule-row")).toHaveCount(2, { timeout: 60_000 });
    const started = Date.now();
    await page.getByRole("button", { name: "Гант", exact: true }).click();
    await expect(page.getByTestId("gantt-bar")).toHaveCount(2, { timeout: 30_000 });
    const renderMs = Date.now() - started;

    await expect(page.getByTestId("gantt-clamped")).toBeVisible();
    const gantt = await page.evaluate(() => {
      const scroller = document.querySelector('[data-testid="gantt-scroller"]') as HTMLElement;
      return {
        widthPx: scroller.scrollWidth,
        svgNodes: scroller.querySelectorAll("svg *").length,
      };
    });
    console.log("GANTT_BOUNDS " + JSON.stringify({ ...gantt, renderMs }));
    // Окно шкалы: 10 лет в обе стороны, около 7 350 суток по 10 px; без него было бы 29 млн px.
    expect(gantt.widthPx).toBeLessThan(90_000);
    expect(gantt.svgNodes).toBeLessThan(6000);
    await shot(page, "gantt-bounds");

    // Страница откликается: переключение масштаба проходит.
    await page.getByRole("button", { name: "Месяц", exact: true }).click();
    await expect(page.getByTestId("gantt-bar")).toHaveCount(2);
    expect(errors).toEqual([]);
    await desk.close();
  } finally {
    for (const id of created) await remove(request, techdir, `${base}/schedule/${id}`);
    await remove(request, techdir, `${base}/nodes/${node.id}`);
  }
});
