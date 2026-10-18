import { expect, test } from "../helpers/test";
import { apiLogin } from "../helpers/api";
import { documentById } from "../helpers/documents";
import { EAST_NAME, newUnit, projectNamed, typeByCode, unique } from "../helpers/equipment";
import { desktopContext, shot, signIn, watchErrors } from "../helpers/ui";

// Сценарий 6 (R2, Q8): начальник отдела создаёт паспорт техники из карточки единицы - юрлицо и связь с
// единицей задаются сразу - и привязывает его к строке срока паспорта. Префикс номера паспорта - ПСП.
test("паспорт техники: создать из карточки единицы и привязать к строке срока", async ({
  browser,
  request,
}) => {
  const head = await apiLogin(request, "equipment_head");
  const east = await projectNamed(request, head, EAST_NAME);
  const type = await typeByCode(request, head, "SAM");
  const unit = await newUnit(request, head, east, type);
  const desktop = await desktopContext(browser);
  const page = await desktop.newPage();
  const errors = watchErrors(page);
  await signIn(page, "equipment_head");

  await page.goto(`/equipment/${unit.id}`);
  await expect(page.getByTestId("unit-card")).toBeVisible({ timeout: 40_000 });
  await page.getByTestId("unit-document-create").click();
  await page.getByTestId("documents-create-kind").selectOption("equipment_passport");
  await page.getByTestId("documents-create-title").fill(unique("Паспорт E2E "));
  await page.getByTestId("documents-create-submit").click();
  await page.waitForURL(/\/documents\/[0-9a-f-]{36}$/, { timeout: 40_000 });
  const passportId = new URL(page.url()).pathname.split("/").pop() ?? "";
  await expect(page.getByTestId("document-card")).toBeVisible({ timeout: 40_000 });
  await expect(page.getByTestId("document-links")).toContainText(unit.id);
  const passport = await documentById(request, head, passportId);
  expect(passport.registration_no).toMatch(/^ПСП-/);
  await shot(page, "equipment-passport-created");

  // Строка срока паспорта: документ выбирается из реестра этой единицы того же вида (Q8).
  await page.goto(`/equipment/${unit.id}`);
  await expect(page.getByTestId("unit-card")).toBeVisible({ timeout: 40_000 });
  await page.getByTestId("add-document").click();
  await page.locator("#document-kind").selectOption("passport");
  await page.locator("#document-number").fill(unique("ПАС-"));
  await page.locator("#document-valid").fill("2040-12-31");
  await page.getByTestId("equipment-document-link").selectOption(passportId);
  await page.getByTestId("form-submit").click();
  await expect(page.getByTestId("document-row-link").filter({ hasText: passport.registration_no ?? "" })).toBeVisible({
    timeout: 40_000,
  });
  await shot(page, "equipment-passport-linked");
  expect(errors).toEqual([]);
});
