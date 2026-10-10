import { expect, test } from "../helpers/test";

import { apiLogin, get, projectId } from "../helpers/api";
import { EAST } from "../helpers/env";
import {
  numberIn,
  expectProportional,
  objectDashboard,
  planKeysOf,
} from "../helpers/dashboards";
import {
  desktopContext,
  expectNoHorizontalScroll,
  phoneContext,
  shot,
  signIn,
  watchErrors,
} from "../helpers/ui";

// Дашборды S4: сводка группы, объект, проблемы и вид заказчика. Идентификаторы - по контракту UI.

// Владелец группы (техдиректор входит по паролю): группа -> объект -> проблема -> узел.
test("группа -> объект -> проблема -> узел: сводка группы ведёт в объект и его проблемы", async ({
  browser,
}) => {
  const desk = await desktopContext(browser);
  const page = await desk.newPage();
  const errors = watchErrors(page);
  await signIn(page, "techdir");
  await page.goto("/dashboard");
  await expect(page.getByTestId("group-dashboard")).toBeVisible();
  const row = page.getByTestId("group-row").filter({ hasText: EAST });
  await expect(row).toHaveCount(1);
  await expect(row).toContainText("%");
  await shot(page, "dashboard-1-group");

  await row.getByRole("link").first().click();
  await expect(page.getByTestId("object-dashboard")).toBeVisible();
  await expect(page.getByTestId("kpi-fact")).toContainText("%");
  await expect(page.getByTestId("s-curve")).toBeVisible();
  await expect(page.getByTestId("problems-table")).toBeVisible();
  await shot(page, "dashboard-2-object");

  await page.getByTestId("problem-link").first().click();
  await expect(page).toHaveURL(/[?&]node=/);
  await expect(page.getByTestId("node-readiness-percent").first()).toBeVisible();
  await shot(page, "dashboard-3-node");
  expect(errors).toEqual([]);
  await desk.close();
});

// Мастер на телефоне: дашборд объекта читается на 375 px, без горизонтальной прокрутки страницы.
test("мастер на телефоне: дашборд объекта на 375 px без горизонтальной прокрутки", async ({
  browser,
  request,
}) => {
  const east = await projectId(request, await apiLogin(request, "master"), EAST);
  const phone = await phoneContext(browser);
  const master = await phone.newPage();
  const errors = watchErrors(master);
  await signIn(master, "master");
  await master.goto(`/projects/${east}`);
  await expect(master.getByTestId("object-dashboard")).toBeVisible();
  await expect(master.getByTestId("kpi-fact")).toBeVisible();
  await expect(master.getByTestId("s-curve")).toBeVisible();
  await expectNoHorizontalScroll(master);
  await shot(master, "dashboard-phone-object");
  expect(errors).toEqual([]);
  await phone.close();
});

// Заказчик: только факт - процент и ряд факта по неделям. Ни плана, ни отклонения, ни денег, проблем и
// фото: проверяем и ответ API (ключей плана нет на любой глубине), и интерфейс.
test("заказчик: только факт - процент и ряд, без плана, отклонения, денег, проблем и фото", async ({
  browser,
  request,
}) => {
  const customer = await apiLogin(request, "customer");
  const east = await projectId(request, customer, EAST);
  const view = await get<Record<string, unknown>>(
    request,
    customer,
    `/api/v1/dashboards/portal/${east}`,
  );
  expect(planKeysOf(view)).toEqual([]);
  expect(Object.keys(view).sort()).toEqual(
    ["as_of", "name", "progress", "project_id", "series", "status"],
  );

  const desk = await desktopContext(browser);
  const page = await desk.newPage();
  const errors = watchErrors(page);
  await signIn(page, "customer");
  await page.goto("/projects");
  await page.getByRole("link").filter({ hasText: EAST }).first().click();
  await expect(page.getByTestId("portal-dashboard")).toBeVisible();
  await expect(page.getByTestId("kpi-fact")).toContainText("%");
  await expect(page.getByTestId("kpi-plan")).toHaveCount(0);
  await expect(page.getByTestId("kpi-deviation")).toHaveCount(0);
  await expect(page.getByTestId("s-curve")).toBeVisible();
  await expect(page.getByTestId("s-curve")).not.toContainText(/план/i);
  await expect(page.getByTestId("node-bars")).toHaveCount(0);
  await expect(page.getByTestId("problems-table")).toHaveCount(0);
  await expect(page.getByTestId("budget-tile")).toHaveCount(0);
  await expect(page.getByTestId("photo-feed")).toHaveCount(0);
  await expect(page.getByTestId("kpi-cost")).toHaveCount(0);
  await shot(page, "dashboard-customer");
  expect(errors).toEqual([]);
  await desk.close();
});

// Внутренний дашборд объекта: полосы узлов верхнего уровня и таблица данных повторяют /dashboards/object,
// длины полос пропорциональны процентам, кривая на экране. Скриншот - с прокруткой до графиков.
test("дашборд объекта: полосы узлов повторяют API, длины пропорциональны, скриншот графиков", async ({
  browser,
  request,
}) => {
  const techdir = await apiLogin(request, "techdir");
  const east = await projectId(request, techdir, EAST);
  const dash = await objectDashboard(request, techdir, east);
  const desk = await desktopContext(browser);
  const page = await desk.newPage();
  const errors = watchErrors(page);
  await signIn(page, "techdir");
  await page.goto(`/projects/${east}`);
  await expect(page.getByTestId("object-dashboard")).toBeVisible();

  const bars = page.getByTestId("node-bars");
  await expect(bars).toBeVisible();
  // две серии - план и факт; у каждой по полосе на узел верхнего уровня
  await expect(bars.locator(".recharts-bar")).toHaveCount(2);
  const planRects = bars.locator(".recharts-bar").nth(0).locator(".recharts-bar-rectangle");
  const factRects = bars.locator(".recharts-bar").nth(1).locator(".recharts-bar-rectangle");
  await expect(planRects).toHaveCount(dash.nodes.length);
  await expect(factRects).toHaveCount(dash.nodes.length);
  // таблица данных повторяет числа ответа API (до целого: подписи округляются); таблица свёрнута в
  // <details>, поэтому читаем textContent: innerText скрытого текста не отдаёт
  const rows = bars.locator("tbody tr");
  await expect(rows).toHaveCount(dash.nodes.length);
  for (const [index, node] of dash.nodes.entries()) {
    const cells = rows.nth(index).locator("td");
    const planText = (await cells.nth(1).textContent()) ?? "";
    const factText = (await cells.nth(2).textContent()) ?? "";
    expect(numberIn(planText)).toBeCloseTo(Number(node.progress.planned_percent), 0);
    expect(numberIn(factText)).toBeCloseTo(Number(node.progress.percent), 0);
  }
  // длины полос пропорциональны значениям: одна шкала 0-100 на все узлы
  await expectProportional(
    factRects,
    dash.nodes.map((node) => Number(node.progress.percent)),
  );
  await expectProportional(
    planRects,
    dash.nodes.map((node) => Number(node.progress.planned_percent)),
  );

  // скриншот: страница прокручена до графиков, кривая и полосы узлов в кадре
  await page.getByTestId("s-curve").scrollIntoViewIfNeeded();
  await expect(page.getByTestId("s-curve")).toBeInViewport();
  await shot(page, "dashboard-object-charts");
  expect(errors).toEqual([]);
  await desk.close();
});

// Прораб видит объекты, но не сводку группы: страница и строки в ней недоступны.
test("сводка группы закрыта от прораба", async ({ browser }) => {
  const desk = await desktopContext(browser);
  const page = await desk.newPage();
  await signIn(page, "foreman");
  await page.goto("/dashboard");
  await expect(page.getByTestId("group-dashboard")).toHaveCount(0);
  await expect(page.getByTestId("group-row")).toHaveCount(0);
  await desk.close();
});
