import { expect, test } from "../helpers/test";
import { get } from "../helpers/api";
import { desktopContext, shot } from "../helpers/ui";
import { ownerSession, ownerSignIn } from "../helpers/stock";

// S7b, сценарий 1: импорт справочника. Файл с одной ошибочной строкой -> превью показывает номер строки,
// ничего не создано; исправленный файл загружен целиком. Импорт - через владельца (каталог и список юрлиц).
test("импорт справочника: ошибка по номеру строки, ничего не создано, исправленный файл загружен", async ({
  browser,
  request,
}) => {
  const owner = await ownerSession(request);
  const suffix = Date.now().toString(36).toUpperCase();
  const first = `IMP-${suffix}-1`;
  const second = `IMP-${suffix}-2`;
  const bad = `артикул;наименование;базовая единица\n${first};Песок ${suffix};кг\n${second};;кг\n`;
  const good = `артикул;наименование;базовая единица\n${first};Песок ${suffix};кг\n${second};Щебень ${suffix};кг\n`;

  const context = await desktopContext(browser);
  const page = await context.newPage();
  await ownerSignIn(page);
  await page.goto("/materials");
  await page.getByRole("button", { name: "Импорт из файла" }).click();
  const dialog = page.getByRole("dialog");
  const file = dialog.locator('input[type="file"]');

  await file.setInputFiles({ name: "materials-bad.csv", mimeType: "text/csv", buffer: Buffer.from(bad, "utf-8") });
  await dialog.getByRole("button", { name: "Проверить" }).click();
  await expect(page.getByTestId("import-issues")).toContainText("Строка 3");
  await shot(page, "stock-import-preview-error");

  // Превью без записи: кодов из файла в справочнике нет.
  const afterPreview = await get<{ code: string }[]>(request, owner, "/api/v1/materials");
  expect(afterPreview.map((row) => row.code)).not.toContain(first);

  await file.setInputFiles({ name: "materials-good.csv", mimeType: "text/csv", buffer: Buffer.from(good, "utf-8") });
  await dialog.getByRole("button", { name: "Проверить" }).click();
  await expect(dialog).toContainText("Будет добавлено: 2");
  await dialog.getByRole("button", { name: "Загрузить" }).click();

  await expect
    .poll(async () => {
      const rows = await get<{ code: string }[]>(request, owner, "/api/v1/materials");
      return rows.map((row) => row.code).filter((code) => code === first || code === second).length;
    })
    .toBe(2);
});
