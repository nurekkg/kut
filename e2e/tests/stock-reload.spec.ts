import type { Page } from "@playwright/test";

import { expect, test } from "../helpers/test";
import { get, post } from "../helpers/api";
import { EAST_NAME, projectNamed } from "../helpers/equipment";
import { phoneContext, signIn } from "../helpers/ui";
import { ensureWarehouse, grantLegalAccess, jpegFile, ownerSession } from "../helpers/stock";

type Doc = { id: string };

// Повторный выбор того же файла на телефоне: имя, размер и время изменения совпадают.
const SAME_SCAN_MODIFIED = 1_700_000_000_000;
const SENT = "Накладная отправлена прорабу на согласование.";

// S7b, ревью (E1, E2, M1, M2, M6): отправка рвётся на сети, потерянный ответ, перезагрузка вкладки. Версий и актов
// М-7 должно быть ровно по одному: повтор продолжает тот же акт и ту же версию, а не заводит новые.
test("обрыв отправки и перезагрузка: один акт, одна версия со сканом", async ({ browser, request }) => {
  const { owner, east, material } = await setup(request);
  const before = await actIds(request, owner, east.id);

  const phone = await phoneContext(browser);
  const store = await phone.newPage();
  await signIn(store, "storekeeper");
  await openReceipt(store, east.id);
  const sendPath = /\/versions\/[^/]+\/send$/;
  await store.route(sendPath, (route) => route.abort());
  await addReceipt(store, material.id, "3");
  await store.getByRole("button", { name: "Отправить прорабу" }).click();
  await expect(store.getByText(/Нет связи с сервером/)).toBeVisible({ timeout: 90_000 });

  await store.unroute(sendPath);
  await store.reload({ waitUntil: "networkidle" });
  await openReceipt(store, east.id);
  await addReceipt(store, material.id, "3");
  await store.getByRole("button", { name: "Отправить прорабу" }).click();
  await expect(store.getByText(SENT)).toBeVisible({ timeout: 90_000 });

  await expectOneNewAct(request, owner, east.id, before);
  await phone.close();
});

// Ответ на отправку потерян (сервер принял, клиент получил 503), вкладка перезагружена, повтор без правок:
// отправка принимается как выполненная, акт и версия - по одному.
test("потерянный ответ на отправку: повтор без правок принимает отправку", async ({ browser, request }) => {
  const { owner, east, material } = await setup(request);
  const before = await actIds(request, owner, east.id);

  const phone = await phoneContext(browser);
  const store = await phone.newPage();
  await signIn(store, "storekeeper");
  await openReceipt(store, east.id);
  const sendPath = /\/versions\/[^/]+\/send$/;
  await store.route(sendPath, async (route) => {
    await route.fetch();
    await route.fulfill({ status: 503, contentType: "application/json", body: '{"detail":"lost"}' });
  });
  await addReceipt(store, material.id, "3");
  await store.getByRole("button", { name: "Отправить прорабу" }).click();
  await expect(store.getByText(/Не удалось выполнить действие|Нет связи/)).toBeVisible({ timeout: 90_000 });

  await store.unroute(sendPath);
  await store.reload({ waitUntil: "networkidle" });
  await openReceipt(store, east.id);
  await addReceipt(store, material.id, "3");
  await store.getByRole("button", { name: "Отправить прорабу" }).click();
  await expect(store.getByText(SENT)).toBeVisible({ timeout: 90_000 });

  await expectOneNewAct(request, owner, east.id, before);
  await phone.close();
});

// Ответ на отправку потерян, после перезагрузки количество изменено: изменения не сохранятся, и форма говорит об этом
// прямо, а не пишет «отправлено» (ревью S7b, M2).
test("потерянный ответ и правка после перезагрузки: честное сообщение, правка не записана", async ({ browser, request }) => {
  const { owner, east, material } = await setup(request);
  const before = await actIds(request, owner, east.id);

  const phone = await phoneContext(browser);
  const store = await phone.newPage();
  await signIn(store, "storekeeper");
  await openReceipt(store, east.id);
  const sendPath = /\/versions\/[^/]+\/send$/;
  await store.route(sendPath, async (route) => {
    await route.fetch();
    await route.fulfill({ status: 503, contentType: "application/json", body: '{"detail":"lost"}' });
  });
  await addReceipt(store, material.id, "3");
  await store.getByRole("button", { name: "Отправить прорабу" }).click();
  await expect(store.getByText(/Не удалось выполнить действие|Нет связи/)).toBeVisible({ timeout: 90_000 });

  await store.unroute(sendPath);
  await store.reload({ waitUntil: "networkidle" });
  await openReceipt(store, east.id);
  await addReceipt(store, material.id, "4");
  await store.getByRole("button", { name: "Отправить прорабу" }).click();
  await expect(store.getByText(/изменения количества не сохранились/)).toBeVisible({ timeout: 90_000 });

  await expectOneNewAct(request, owner, east.id, before);
  await phone.close();
});

async function setup(request: Parameters<typeof ownerSession>[0]) {
  const owner = await ownerSession(request);
  const east = await projectNamed(request, owner, EAST_NAME);
  await ensureWarehouse(request, owner, east.id);
  await grantLegalAccess(request, owner, "storekeeper", east.legal_entity_id);
  const suffix = Date.now().toString(36).toUpperCase();
  const material = await post<Doc>(request, owner, "/api/v1/materials", {
    legal_entity_id: east.legal_entity_id,
    code: `RLD-${suffix}`,
    name: `Песок RLD-${suffix}`,
    base_unit: "кг",
    requires_certificate: false,
  });
  return { owner, east, material };
}

async function openReceipt(store: Page, projectId: string): Promise<void> {
  await store.goto(`/m/stock/${projectId}`);
  await store.getByRole("button", { name: "Приход" }).click();
}

// Позиция и скан. Скан прикрепляется с фиксированным временем изменения: повторный выбор того же файла.
async function addReceipt(store: Page, materialId: string, quantity: string): Promise<void> {
  await store.locator("select:visible").first().selectOption(materialId);
  await store.getByLabel("Количество фактически").fill(quantity);
  await attachScan(store, jpegFile().buffer, "photo.jpg", SAME_SCAN_MODIFIED);
}

async function attachScan(store: Page, buffer: Buffer, name: string, lastModified: number): Promise<void> {
  await store.locator('input[type="file"]').last().evaluate(
    (input, file) => {
      const bytes = Uint8Array.from(atob(file.base64), (char) => char.charCodeAt(0));
      const transfer = new DataTransfer();
      transfer.items.add(new File([bytes], file.name, { type: file.type, lastModified: file.lastModified }));
      const element = input as HTMLInputElement;
      element.files = transfer.files;
      element.dispatchEvent(new Event("change", { bubbles: true }));
    },
    { base64: buffer.toString("base64"), name, type: "image/jpeg", lastModified },
  );
}

async function actIds(request: Parameters<typeof ownerSession>[0], owner: Awaited<ReturnType<typeof ownerSession>>, projectId: string): Promise<string[]> {
  const acts = await get<Doc[]>(request, owner, `/api/v1/documents?project_id=${projectId}&kind=m7_act&limit=200`);
  return acts.map((act) => act.id);
}

// Новые акты (по идентификаторам, а не по счёту: лимит списка 200) - ровно один, и у него одна версия.
async function expectOneNewAct(
  request: Parameters<typeof ownerSession>[0],
  owner: Awaited<ReturnType<typeof ownerSession>>,
  projectId: string,
  before: string[],
): Promise<void> {
  const created = (await actIds(request, owner, projectId)).filter((id) => !before.includes(id));
  expect(created, "новых актов М-7").toHaveLength(1);
  const detail = await get<{ versions: { id: string }[] }>(request, owner, `/api/v1/documents/${created[0]}`);
  expect(detail.versions.length, "версий акта").toBe(1);
}
