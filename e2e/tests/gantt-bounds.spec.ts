import { expect, test } from "../helpers/test";

import { apiLogin, get, post, projectId, remove } from "../helpers/api";
import { WEST, today } from "../helpers/env";
import { desktopContext, shot, signIn, watchErrors } from "../helpers/ui";

// Сервер принимает любые даты ГПР (до 9999-12-31). Одна такая строка не должна растягивать шкалу
// Ганта на миллионы суток и подвешивать вкладку у всех, кто откроет диаграмму: шкала ограничена
// окном вокруг «сегодня», полоса упирается в край, над диаграммой стоит предупреждение.
// «Запад» в демо-данных пуст: строки заводятся тут же и удаляются в конце.
test("Гант с датой из другого века: шкала ограничена, вкладка не виснет", async ({
  browser,
  request,
}) => {
  const techdir = await apiLogin(request, "techdir");
  const west = await projectId(request, techdir, WEST);
  const base = `/api/v1/projects/${west}`;
  const node = await post<{ id: string }>(request, techdir, `${base}/nodes`, {
    type: "building",
    name: `E2E Гант ${Date.now()}`,
  });
  const works = await get<{ id: string }[]>(request, techdir, "/api/v1/works", { limit: 2 });
  const created: string[] = [];
  try {
    for (const [index, dates] of [
      { plan_start: today(-5), plan_end: today(20) },
      { plan_start: "1900-01-01", plan_end: "9999-12-31" },
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
