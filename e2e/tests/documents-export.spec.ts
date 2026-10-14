import { readFileSync } from "node:fs";

import { expect, test } from "../helpers/test";
import { apiLogin } from "../helpers/api";
import { WEST_NAME, projectNamed } from "../helpers/equipment";
import { desktopContext, signIn, watchErrors } from "../helpers/ui";
import { readZip } from "../helpers/zip";

// Сценарий 6 (DoD S5b, экспорт): бухгалтер (право documents:export и документы с суммами) скачивает
// реестр XLSX; в общих строках файла есть заголовок суммы и регистрационный номер.
test("бухгалтер: выгрузка реестра скачивается, в файле есть колонка суммы", async ({ browser, request }) => {
  const accountant = await apiLogin(request, "accountant");
  const west = await projectNamed(request, accountant, WEST_NAME);
  const desktop = await desktopContext(browser);
  const page = await desktop.newPage();
  const errors = watchErrors(page);
  await signIn(page, "accountant");

  await page.goto(`/projects/${west.id}/documents`);
  await expect(page.getByTestId("documents-registry")).toBeVisible({ timeout: 40_000 });
  await expect(page.getByTestId("documents-export")).toBeVisible();
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByTestId("documents-export").click(),
  ]);
  expect(download.suggestedFilename()).toMatch(/^documents-\d{4}-\d{2}-\d{2}\.xlsx$/);

  const saved = await download.path();
  expect(saved).toBeTruthy();
  const entries = readZip(readFileSync(saved!));
  const strings = entries.get("xl/sharedStrings.xml")?.toString("utf8") ?? "";
  expect(strings).toContain("Регистрационный номер");
  expect(strings).toContain("Сумма");
  expect(errors).toEqual([]);
});
