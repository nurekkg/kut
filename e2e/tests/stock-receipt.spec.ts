import { expect, test } from "../helpers/test";
import { get } from "../helpers/api";
import { EAST_NAME, projectNamed } from "../helpers/equipment";
import { desktopContext, expectNoHorizontalScroll, phoneContext, shot, signIn } from "../helpers/ui";
import {
  balanceOf,
  certifiedMaterial,
  ensureWarehouse,
  grantLegalAccess,
  expectTapTargetByName,
  jpegFile,
  ownerSession,
  pdfFile,
} from "../helpers/stock";

type DocRow = { id: string; created_at: string };
type NodeRow = { id: string; master_user_id?: string | null };

// S7b, сценарии 2 и 3: кладовщик на телефоне принимает М-7 с партией и сертификатом (2 мешка по 25 кг),
// прораб согласовывает на телефоне, остаток на складе - 50 кг. Затем выдача ВН без файла: -10 кг.
// Оба экрана проверяются на 375 px: без горизонтальной прокрутки, тап-цели кнопок не меньше 48 px.
test("приход М-7 с партией и сертификатом, согласование прорабом, выдача ВН без файла", async ({
  browser,
  request,
}) => {
  const owner = await ownerSession(request);
  const east = await projectNamed(request, owner, EAST_NAME);
  const warehouse = await ensureWarehouse(request, owner, east.id);
  const material = await certifiedMaterial(request, owner, east.legal_entity_id);
  const batchNo = `П-${Date.now().toString(36).toUpperCase()}`;
  // Кладовщику выдаём юрлицо объекта: каталог и остатки видны по юрлицу (сиды этого не дают).
  await grantLegalAccess(request, owner, "storekeeper", east.legal_entity_id);

  // Приход на телефоне кладовщика.
  const storePhone = await phoneContext(browser);
  const store = await storePhone.newPage();
  await signIn(store, "storekeeper");
  await store.goto(`/m/stock/${east.id}`);
  await store.getByRole("button", { name: "Приход" }).click();
  // Обе формы склада смонтированы (неактивная скрыта): берём видимые select.
  await store.locator("select:visible").first().selectOption(material.id);
  await store.getByLabel("Количество фактически").fill("2");
  await store.getByLabel("Единица").selectOption("мешок");
  await store.getByLabel("Номер партии").fill(batchNo);
  await store.getByLabel("Сертификат партии").setInputFiles(pdfFile());
  await store.locator('input[type="file"]').last().setInputFiles(jpegFile());
  await expectNoHorizontalScroll(store);
  await expectTapTargetByName(store, "Отправить прорабу");
  await shot(store, "stock-receipt-375");
  await store.getByRole("button", { name: "Отправить прорабу" }).click();
  await expect(store.getByText("Накладная отправлена прорабу на согласование.")).toBeVisible({ timeout: 90_000 });

  // Прораб на телефоне согласовывает М-7 (блок маршрута документа).
  const docs = await get<DocRow[]>(request, owner, "/api/v1/documents", { project_id: east.id, kind: "m7_act" });
  const m7 = [...docs].sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
  const forePhone = await phoneContext(browser);
  const fore = await forePhone.newPage();
  await signIn(fore, "foreman");
  await fore.goto(`/documents/${m7.id}`);
  await fore.getByRole("button", { name: "Согласовать" }).first().click();
  await expectNoHorizontalScroll(fore);
  await shot(fore, "stock-m7-approve-375");
  await expect
    .poll(async () => {
      const detail = await get<{ versions: { status: string }[] }>(request, owner, `/api/v1/documents/${m7.id}`);
      return detail.versions.at(-1)?.status;
    })
    .toBe("approved");
  expect(await balanceOf(request, owner, warehouse.id, material.id)).toBe(50);

  // Выдача ВН без файла: участок с мастером, 10 кг из партии.
  const tree = await get<NodeRow[]>(request, owner, `/api/v1/projects/${east.id}/tree`);
  const node = tree.find((item) => item.master_user_id);
  expect(node, "на объекте нет участка с мастером").toBeTruthy();
  // Остатки по партиям кладовщик видит после переоткрытия экрана: утверждение прорабом пришло позже загрузки.
  await store.reload({ waitUntil: "networkidle" });
  await store.getByRole("button", { name: "Выдача" }).click();
  await store.locator("select:visible").nth(0).selectOption(node!.id);
  await store.locator("select:visible").nth(1).selectOption(material.id);
  await store.getByLabel("Количество", { exact: false }).fill("10");
  await store.getByLabel("Единица").selectOption("кг");
  await store.getByLabel("Партия").selectOption({ index: 1 });
  await expectNoHorizontalScroll(store);
  await expectTapTargetByName(store, "Провести");
  await shot(store, "stock-issue-375");
  await store.getByRole("button", { name: "Провести" }).click();
  await expect(store.getByText("Проведено движений: 1.")).toBeVisible({ timeout: 90_000 });
  expect(await balanceOf(request, owner, warehouse.id, material.id)).toBe(40);

  // Остаток на складе объекта (телефон кладовщика, десктопная версия страницы склада).
  const desktop = await desktopContext(browser);
  const page = await desktop.newPage();
  await signIn(page, "storekeeper");
  await page.goto(`/projects/${east.id}/warehouse`);
  await expect(page.getByText(material.name).first()).toBeVisible({ timeout: 60_000 });
  await shot(page, "stock-warehouse-1280", true);
});
