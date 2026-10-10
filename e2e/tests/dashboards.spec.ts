import { expect, test } from "../helpers/test";

import { apiLogin, projectId } from "../helpers/api";
import { EAST } from "../helpers/env";
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

// Заказчик: процент и ряд по неделям своего объекта; денег, проблем, фото и стоимости в виде нет.
test("заказчик: процент и ряд по неделям, без бюджета, проблем и фото", async ({ browser }) => {
  const desk = await desktopContext(browser);
  const page = await desk.newPage();
  const errors = watchErrors(page);
  await signIn(page, "customer");
  await page.goto("/projects");
  await page.getByRole("link").filter({ hasText: EAST }).first().click();
  await expect(page.getByTestId("portal-dashboard")).toBeVisible();
  await expect(page.getByTestId("s-curve")).toBeVisible();
  await expect(page.getByTestId("problems-table")).toHaveCount(0);
  await expect(page.getByTestId("budget-tile")).toHaveCount(0);
  await expect(page.getByTestId("photo-feed")).toHaveCount(0);
  await expect(page.getByTestId("kpi-cost")).toHaveCount(0);
  await shot(page, "dashboard-customer");
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
