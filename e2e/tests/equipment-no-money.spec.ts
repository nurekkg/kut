import { expect, test } from "../helpers/test";
import { apiLogin, get, post } from "../helpers/api";
import { createShift, prepareDay, submitShift } from "../helpers/field-shifts";
import { desktopContext, shot, signIn } from "../helpers/ui";

// Сценарий 5 (Q11 = C): прораб не видит сумм ни в ответах API, ни на страницах. Сумма заправки и итоги
// журнала - только с правом на ставки; количества (часы, литры) - видны.
const AMOUNT = "4321.50";

test("прораб нигде не видит сумм: JSON ответов и текст страниц", async ({ browser, request }) => {
  const day = await prepareDay(request, 1);
  const machinist = await apiLogin(request, "machinist");
  const techdir = await apiLogin(request, "techdir");
  const shift = await createShift(request, machinist, {
    equipment_unit_id: day.unitEast.id,
    project_id: day.east.id,
    operator_id: day.operatorId,
    hours: "8",
  });
  await post(request, machinist, `/api/v1/equipment/shifts/${shift.id}/fuel`, {
    liters: "55.5",
    amount: AMOUNT,
  });
  await submitShift(request, machinist, shift.id);
  await post(request, techdir, `/api/v1/equipment/shifts/${shift.id}/confirm`, undefined);

  const foreman = await apiLogin(request, "foreman");
  const window = { date_from: "2026-01-01", date_to: "2100-12-31" };
  const fuel = await get<Record<string, unknown>[]>(request, foreman, `/api/v1/equipment/shifts/${shift.id}/fuel`);
  expect(fuel.length).toBeGreaterThan(0);
  for (const row of fuel) expect(Object.keys(row)).not.toContain("amount");
  expect(await get(request, foreman, "/api/v1/equipment/costs/totals", window)).toEqual([]);
  const usage = await get<Record<string, unknown>[]>(request, foreman, "/api/v1/equipment/usage/totals", {
    ...window,
    project_id: day.east.id,
  });
  expect(JSON.stringify(usage)).not.toContain(AMOUNT);
  const denied = await request.get("/api/v1/equipment/costs/entries", {
    params: window,
    headers: { Authorization: `Bearer ${foreman.token}` },
  });
  expect(denied.status()).toBe(403);

  const desktop = await desktopContext(browser);
  const page = await desktop.newPage();
  await signIn(page, "foreman");
  await page.goto(`/projects/${day.east.id}/equipment`);
  await expect(page.getByTestId("project-equipment-table")).toBeVisible({ timeout: 40_000 });
  await expect(page.locator("body")).not.toContainText(AMOUNT);
  await shot(page, "project-equipment-tab");

  await page.goto("/equipment/journal");
  await expect(page.getByTestId("journal-forbidden")).toBeVisible({ timeout: 40_000 });
  await expect(page.locator("body")).not.toContainText(AMOUNT);

  await page.goto("/approvals");
  await expect(page.getByTestId("equipment-approvals")).toBeVisible({ timeout: 40_000 });
  await expect(page.locator("body")).not.toContainText(AMOUNT);
});
