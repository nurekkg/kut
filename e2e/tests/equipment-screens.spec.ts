import { expect, test } from "../helpers/test";
import { desktopContext, expectNoHorizontalScroll, phoneContext, shot, signIn, watchErrors } from "../helpers/ui";

// Снимки экранов для отчёта: реестр на десктопе и на телефоне, ТО и ремонты. На телефоне страница
// не должна уходить вбок.
test("реестр на десктопе и на 375 px, страница ТО и ремонтов", async ({ browser }) => {
  const desktop = await desktopContext(browser);
  const page = await desktop.newPage();
  const errors = watchErrors(page);
  await signIn(page, "equipment_head");
  await page.goto("/equipment");
  await expect(page.getByTestId("registry-table")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("registry-count")).toContainText("единиц");
  await shot(page, "equipment-registry-desktop");
  await page.goto("/equipment/maintenance");
  await expect(page.getByRole("heading", { name: "ТО и ремонты" })).toBeVisible({ timeout: 30_000 });
  await shot(page, "equipment-maintenance");

  const phone = await phoneContext(browser);
  const mobile = await phone.newPage();
  const mobileErrors = watchErrors(mobile);
  await signIn(mobile, "foreman");
  await mobile.goto("/equipment");
  await expect(mobile.getByTestId("registry-cards").locator("a").first()).toBeVisible({ timeout: 30_000 });
  await expectNoHorizontalScroll(mobile);
  await shot(mobile, "equipment-registry-375");

  expect(errors).toEqual([]);
  expect(mobileErrors).toEqual([]);
});
