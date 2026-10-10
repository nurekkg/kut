import { expect, test } from "../helpers/test";

import type { APIRequestContext } from "@playwright/test";
import { randomUUID } from "node:crypto";

import { apiLogin, get, post, remove, type Session } from "../helpers/api";
import { emptyProject } from "../helpers/dashboards";
import { today } from "../helpers/env";
import { desktopContext, shot, signIn, watchErrors } from "../helpers/ui";

const PREFIX = "E2E Гант";

// Даты ГПР держит БД (ревизия a1efb2b3d84b): вне 2000-2100 и длиннее 10 лет сервер не принимает.
// Клиентская защита Ганта остаётся на случай данных, которые придут в обход (старый сервер, ошибка):
// шкала ограничена окном вокруг «сегодня», полоса упирается в край, над диаграммой предупреждение.
// Свой пустой объект на каждый прогон: на «Западе» теперь демо-ГПР и факт (сиды S4), график должен
// видеть только свои строки. Объект остаётся в тестовой базе, как и прочие данные сценариев.
async function ownNode(request: APIRequestContext, techdir: Session) {
  const west = await emptyProject(request, techdir, `${PREFIX} ${Date.now()}`);
  const base = `/api/v1/projects/${west}`;
  const node = await post<{ id: string }>(request, techdir, `${base}/nodes`, {
    type: "building",
    name: `${PREFIX} ${Date.now()}`,
  });
  const works = await get<{ id: string }[]>(request, techdir, "/api/v1/works", { limit: 2 });
  return { west, base, node, works };
}

test("сервер не принимает даты ГПР вне границ и отвечает понятным кодом", async ({ request }) => {
  const techdir = await apiLogin(request, "techdir");
  const { base, node, works } = await ownNode(request, techdir);
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
  const { west, base, node, works } = await ownNode(request, techdir);
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
