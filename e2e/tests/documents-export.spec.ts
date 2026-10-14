import { readFileSync } from "node:fs";

import { expect, test } from "../helpers/test";
import { apiLogin } from "../helpers/api";
import { WEST_NAME, projectNamed } from "../helpers/equipment";
import { desktopContext, signIn, watchErrors } from "../helpers/ui";
import { readZip } from "../helpers/zip";

// Сценарий 6 (DoD S5b, экспорт): юрист (право documents:export и документы с суммами; бухгалтер и финдиректор
// входят с 2FA, поэтому здесь не используются) скачивает реестр XLSX; в общих строках файла есть заголовок суммы
// и регистрационный номер.
test("юрист: выгрузка реестра скачивается, в файле есть колонка суммы", async ({ browser, request }) => {
  const lawyer = await apiLogin(request, "lawyer");
  const west = await projectNamed(request, lawyer, WEST_NAME);
  const desktop = await desktopContext(browser);
  const page = await desktop.newPage();
  const errors = watchErrors(page);
  await signIn(page, "lawyer");

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
  // openpyxl пишет текст ячеек либо в sharedStrings, либо прямо в лист (inlineStr): смотрим оба места.
  const entries = readZip(readFileSync(saved!));
  const sheet = entries.get("xl/worksheets/sheet1.xml")?.toString("utf8") ?? "";
  const shared = entries.get("xl/sharedStrings.xml")?.toString("utf8") ?? "";
  const text = sheet + shared;
  expect(text).toContain("Регистрационный номер");
  expect(text).toContain("Сумма");
  expect(errors).toEqual([]);
});
