import fs from "node:fs";
import path from "node:path";

import { unique } from "../helpers/equipment";
import { expect, test } from "../helpers/test";
import { buildXlsx, type Cell } from "../helpers/xlsx";
import { desktopContext, shot, signIn, watchErrors } from "../helpers/ui";

const HEADER: Cell[] = [
  "ИНН юрлица",
  "Код типа",
  "Владение",
  "Номер",
  "Номер договора аренды",
  "Марка и модель",
  "Год выпуска",
];
const EAST_TAX = "DEV-EAST-001";
const DIR = path.join(__dirname, "..", "artifacts", "import");

function writeWorkbook(name: string, rows: Cell[][]): string {
  fs.mkdirSync(DIR, { recursive: true });
  const file = path.join(DIR, name);
  fs.writeFileSync(file, buildXlsx([HEADER, ...rows]));
  return file;
}

// Сценарий 3 из брифа: шаблон скачан, файл с ошибкой в строке отклонён целиком с отчётом по строкам,
// исправленный файл проходит проверку, загрузка создаёт обе единицы, и они видны в реестре.
test("импорт XLSX: ошибка в строке даёт отчёт, исправленный файл попадает в реестр", async ({ browser }) => {
  const numberOk = unique("IMP");
  const numberBad = unique("IMP");
  const context = await desktopContext(browser);
  const page = await context.newPage();
  const errors = watchErrors(page);
  await signIn(page, "equipment_head");
  await page.goto("/equipment/import");

  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByTestId("download-xlsx").click(),
  ]);
  expect(download.suggestedFilename()).toBe("equipment-import.xlsx");

  const bad = writeWorkbook("import-with-error.xlsx", [
    [EAST_TAX, "SAM", "own", numberOk, null, "Самосвал E2E", 2016],
    [EAST_TAX, "SAM", "lease", numberBad, null, "Самосвал E2E", 2017],
  ]);
  await page.getByTestId("import-file").setInputFiles(bad);
  await page.getByTestId("import-check").click();
  const report = page.getByTestId("import-report");
  await expect(report).toBeVisible({ timeout: 30_000 });
  await expect(report).toContainText("Ошибок в файле: 1");
  const issue = page.getByTestId("import-issue").first();
  await expect(issue).toContainText("Владение");
  await expect(issue).toContainText("Укажите own");
  await expect(report.getByRole("row").nth(1)).toContainText("3");
  await expect(page.getByTestId("download-errors")).toBeVisible();
  await shot(page, "equipment-import-report");

  const fixed = writeWorkbook("import-fixed.xlsx", [
    [EAST_TAX, "SAM", "own", numberOk, null, "Самосвал E2E", 2016],
    [EAST_TAX, "SAM", "own", numberBad, null, "Самосвал E2E", 2017],
  ]);
  await page.getByTestId("import-file").setInputFiles(fixed);
  await page.getByTestId("import-check").click();
  await expect(page.getByTestId("import-ready")).toBeVisible({ timeout: 30_000 });
  await page.getByTestId("import-commit").click();
  await expect(page.getByTestId("import-loaded")).toContainText("создано 2", { timeout: 60_000 });

  await page.goto("/equipment");
  await page.getByTestId("registry-search").fill(numberOk);
  await expect(page.getByText(numberOk, { exact: true }).first()).toBeVisible({ timeout: 30_000 });
  await page.getByTestId("registry-search").fill(numberBad);
  await expect(page.getByText(numberBad, { exact: true }).first()).toBeVisible({ timeout: 30_000 });

  expect(errors).toEqual([]);
});
