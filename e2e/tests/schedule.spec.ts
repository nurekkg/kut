import { expect, test } from "../helpers/test";

import { apiLogin, createPlan, get, projectId } from "../helpers/api";
import { EAST } from "../helpers/env";
import { shot, signIn, watchErrors } from "../helpers/ui";

const FULL = 4000;

// ГПР «Востока» на полном объёме (4 800 работ): таблица и Гант с виртуализацией строк, фильтры,
// правка строки и зависимость. Нужна нагрузочная загрузка:
//   docker compose run --rm dev-seed python -m app.modules.schedule.devseed_load
test("график работ на 4800 работ: прокрутка, Гант, правка строки и зависимость", async ({
  browser,
  request,
}, info) => {
  const foreman = await apiLogin(request, "foreman");
  const east = await projectId(request, foreman, EAST);
  const items = await get<{ id: string }[]>(request, foreman, `/api/v1/projects/${east}/schedule`, {
    limit: 10_000,
  });
  test.skip(items.length < FULL, `в графике ${items.length} работ, нужна нагрузочная загрузка`);

  const plan = await createPlan(request, foreman);
  const desk = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    locale: "ru-RU",
    timezoneId: "Asia/Bishkek",
  });
  const page = await desk.newPage();
  const errors = watchErrors(page);
  await signIn(page, "foreman");

  // --- таблица: время до первых строк и прокрутка ---
  const started = Date.now();
  await page.goto(`/projects/${east}/schedule`);
  await expect(page.getByTestId("schedule-row").first()).toBeVisible({ timeout: 120_000 });
  const firstRowsMs = Date.now() - started;
  await expect(page.getByTestId("schedule-summary")).toContainText("в графике");
  await shot(page, "schedule-1-table");

  const table = await page.evaluate(async () => {
    const scroller = document.querySelector('[data-testid="schedule-scroller"]') as HTMLElement;
    const total = scroller.scrollHeight - scroller.clientHeight;
    const frames: number[] = [];
    let last = performance.now();
    for (let step = 1; step <= 150; step += 1) {
      scroller.scrollTop = Math.round((total * step) / 150);
      await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
      const now = performance.now();
      frames.push(now - last);
      last = now;
    }
    frames.sort((a, b) => a - b);
    return {
      scrollHeightPx: scroller.scrollHeight,
      frames: frames.length,
      p50Ms: Math.round(frames[Math.floor(frames.length * 0.5)]),
      p95Ms: Math.round(frames[Math.floor(frames.length * 0.95)]),
      maxMs: Math.round(frames[frames.length - 1]),
      rowsInDom:
        document.querySelectorAll('[data-testid="schedule-row"]').length +
        document.querySelectorAll('[data-testid="schedule-group"]').length,
    };
  });
  expect(table.rowsInDom).toBeLessThan(120); // строк в DOM десятки, а не тысячи
  await shot(page, "schedule-2-table-scrolled");

  // --- Гант: переключение и прокрутка ---
  const switched = Date.now();
  await page.getByRole("button", { name: "Гант", exact: true }).click();
  await expect(page.getByTestId("gantt-bar").first()).toBeVisible({ timeout: 60_000 });
  const ganttFirstMs = Date.now() - switched;
  await shot(page, "schedule-3-gantt");
  const gantt = await page.evaluate(async () => {
    const scroller = document.querySelector('[data-testid="gantt-scroller"]') as HTMLElement;
    const total = scroller.scrollHeight - scroller.clientHeight;
    const frames: number[] = [];
    let last = performance.now();
    for (let step = 1; step <= 150; step += 1) {
      scroller.scrollTop = Math.round((total * step) / 150);
      await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
      const now = performance.now();
      frames.push(now - last);
      last = now;
    }
    frames.sort((a, b) => a - b);
    return {
      scrollHeightPx: scroller.scrollHeight,
      widthPx: scroller.scrollWidth,
      p50Ms: Math.round(frames[Math.floor(frames.length * 0.5)]),
      p95Ms: Math.round(frames[Math.floor(frames.length * 0.95)]),
      maxMs: Math.round(frames[frames.length - 1]),
      svgNodes: document.querySelectorAll('[data-testid="gantt-canvas"] *').length,
      bars: document.querySelectorAll('[data-testid="gantt-bar"]').length,
    };
  });
  expect(gantt.svgNodes).toBeLessThan(1500); // рисуются только видимые строки
  const metrics = { items: items.length, firstRowsMs, table, ganttFirstMs, gantt };
  console.log("SCHEDULE_METRICS " + JSON.stringify(metrics));
  await info.attach("schedule-metrics.json", {
    body: JSON.stringify(metrics, null, 2),
    contentType: "application/json",
  });
  await page.getByRole("button", { name: "Месяц", exact: true }).click();
  await shot(page, "schedule-4-gantt-month");
  await page.getByRole("button", { name: "Неделя", exact: true }).click();

  // --- фильтр по поддереву: этаж с цепочкой работ, стрелки зависимостей видны ---
  await page.getByLabel("Узел (с вложенными)", { exact: true }).fill("Секция А › Этаж 2");
  await page.getByRole("button", { name: /Корпус 1 › Секция А › Этаж 2/ }).first().click();
  await expect(page.getByTestId("gantt-bar").first()).toBeVisible();
  await expect(page.getByTestId("gantt-arrow").first()).toBeVisible();
  await shot(page, "schedule-5-gantt-arrows");
  await page.getByRole("button", { name: "Таблица", exact: true }).click();
  await shot(page, "schedule-6-table-filtered");

  // --- фильтр «просрочены»: в таблице только такие работы ---
  await page.getByRole("button", { name: "Сбросить" }).click();
  await page.getByLabel("Отставание").selectOption("overdue");
  await expect(page.getByTestId("schedule-row").first()).toBeVisible();
  const badges = await page.getByTestId("schedule-row").locator('[data-slot="badge"]').allInnerTexts();
  expect(badges.length).toBeGreaterThan(0);
  expect(badges.every((text) => text.startsWith("Просрочена"))).toBeTruthy();
  await shot(page, "schedule-7-table-overdue");
  await page.getByRole("button", { name: "Сбросить" }).click();

  // --- правка строки: новая работа плана 100 -> 120, затем связь «последователь» ---
  await page.getByLabel("Узел (с вложенными)", { exact: true }).fill(plan.nodeName);
  await page.getByRole("button", { name: new RegExp(plan.nodeName) }).first().click();
  await expect(page.getByTestId("schedule-row")).toHaveCount(1);
  await page.getByRole("button", { name: /Изменить: / }).first().click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel(/Плановый объём/).fill("120");
  await shot(page, "schedule-8-edit-dialog");
  await dialog.getByRole("button", { name: "Сохранить" }).click();
  await expect(dialog).toBeHidden({ timeout: 90_000 });
  const edited = await get<{ plan_volume: string }>(
    request,
    foreman,
    `/api/v1/projects/${east}/schedule/${plan.itemId}`,
  );
  expect(Number(edited.plan_volume)).toBe(120);

  await page.getByRole("button", { name: /Изменить: / }).first().click();
  await dialog.getByLabel("Добавить последователя", { exact: true }).fill("Кладка внутренних");
  await dialog.getByRole("button", { name: /Кладка внутренних перегородок/ }).first().click();
  await dialog.getByRole("button", { name: "Добавить связь" }).click();
  await expect(dialog.getByRole("button", { name: /Убрать связь/ })).toHaveCount(1);
  await shot(page, "schedule-9-dependency");
  const links = await get<{ predecessor_id: string }[]>(
    request,
    foreman,
    `/api/v1/projects/${east}/schedule/dependencies`,
    { limit: 20_000 },
  );
  expect(links.filter((link) => link.predecessor_id === plan.itemId)).toHaveLength(1);

  expect(errors).toEqual([]);
  await desk.close();
});
