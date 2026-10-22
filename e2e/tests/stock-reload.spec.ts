import type { Page } from "@playwright/test";

import { expect, test } from "../helpers/test";
import { get, post } from "../helpers/api";
import { EAST_NAME, projectNamed } from "../helpers/equipment";
import { phoneContext, signIn } from "../helpers/ui";
import { ensureWarehouse, grantLegalAccess, jpegFile, ownerSession } from "../helpers/stock";

type Doc = { id: string };

// S7b, ревью кода (MEDIUM): отправка обрывается на сети, кладовщик перезагружает телефон и отправляет снова.
// Второго акта М-7 быть не должно: идентификаторы черновика лежат в sessionStorage вкладки.
test("перезагрузка после обрыва отправки не заводит второй акт М-7", async ({ browser, request }) => {
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
  const before = await actCount(request, owner, east.id);

  const phone = await phoneContext(browser);
  const store = await phone.newPage();
  await signIn(store, "storekeeper");
  await store.goto(`/m/stock/${east.id}`);
  await store.getByRole("button", { name: "Приход" }).click();

  const sendPath = /\/versions\/[^/]+\/send$/;
  await store.route(sendPath, (route) => route.abort());
  await addReceipt(store, material.id);
  await store.getByRole("button", { name: "Отправить прорабу" }).click();
  await expect(store.getByText(/Нет связи с сервером/)).toBeVisible({ timeout: 90_000 });

  await store.unroute(sendPath);
  await store.reload({ waitUntil: "networkidle" });
  await store.getByRole("button", { name: "Приход" }).click();
  await addReceipt(store, material.id);
  await store.getByRole("button", { name: "Отправить прорабу" }).click();
  await expect(store.getByText("Накладная отправлена прорабу на согласование.")).toBeVisible({
    timeout: 90_000,
  });

  expect(await actCount(request, owner, east.id)).toBe(before + 1);
  await phone.close();
});

async function addReceipt(store: Page, materialId: string): Promise<void> {
  await store.locator("select:visible").first().selectOption(materialId);
  await store.getByLabel("Количество фактически").fill("3");
  await store.locator('input[type="file"]').last().setInputFiles(jpegFile());
}

async function actCount(
  request: Parameters<typeof get>[0],
  owner: Awaited<ReturnType<typeof ownerSession>>,
  projectId: string,
): Promise<number> {
  const acts = await get<Doc[]>(request, owner, `/api/v1/documents?project_id=${projectId}&kind=m7_act&limit=200`);
  return acts.length;
}
