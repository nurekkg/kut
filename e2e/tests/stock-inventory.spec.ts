import type { APIRequestContext, Page } from "@playwright/test";

import { expect, test } from "../helpers/test";
import { apiLogin, get, post, type Session } from "../helpers/api";
import { EAST_NAME, projectNamed, type Project } from "../helpers/equipment";
import { desktopContext, expectNoHorizontalScroll, phoneContext, shot, signIn } from "../helpers/ui";
import {
  balanceOf,
  ensureWarehouse,
  expectTapTargetByName,
  grantLegalAccess,
  jpegFile,
  ownerSession,
} from "../helpers/stock";

type Doc = { id: string; versions: { id: string }[] };
type Material = { id: string; name: string };
type Route = {
  current_position: number | null;
  version_status: string;
  steps: { position: number; role_code: string }[];
};

const DOCUMENTS = "/api/v1/documents";
const SCAN_MODIFIED = 1_700_000_000_000;

// S7c: кладовщик принимает два материала (40 кг и 5,5 кг), затем на телефоне пересчитывает склад: по первому
// недостача 3 кг, по второму излишек 1 кг, остальные позиции склада - как по учёту. Прораб и техдиректор
// согласовывают акт по маршруту «инвентаризация», остатки и журнал склада меняются на корректировки.
// Телефон 375 px: без горизонтальной прокрутки, тап-цели кнопок не меньше 48 px; количества - «40 кг», «5,5 кг».
test("пересчёт склада: недостача и излишек, маршрут прораб -> техдиректор, остатки и журнал", async ({
  browser,
  request,
}) => {
  const owner = await ownerSession(request);
  const east = await projectNamed(request, owner, EAST_NAME);
  const warehouse = await ensureWarehouse(request, owner, east.id);
  await grantLegalAccess(request, owner, "storekeeper", east.legal_entity_id);
  const sand = await createMaterial(request, owner, east, "Песок");
  const gravel = await createMaterial(request, owner, east, "Щебень");

  const storePhone = await phoneContext(browser);
  const store = await storePhone.newPage();
  await signIn(store, "storekeeper");
  await store.goto(`/m/stock/${east.id}`);
  await store.getByRole("button", { name: "Приход" }).click();
  await addReceiptRow(store, sand.id, "40");
  await addReceiptRow(store, gravel.id, "5.5");
  await attachScan(store, jpegFile().buffer, "photo.jpg", SCAN_MODIFIED);
  await store.getByRole("button", { name: "Отправить прорабу" }).click();
  await expect(store.getByText("Накладная отправлена прорабу на согласование.")).toBeVisible({ timeout: 90_000 });
  await decideRoute(request, await latestDocument(request, owner, east.id, "m7_act"));
  expect(await balanceOf(request, owner, warehouse.id, sand.id)).toBe(40);
  expect(await balanceOf(request, owner, warehouse.id, gravel.id)).toBe(5.5);

  await store.goto(`/m/stock/${east.id}`);
  await store.getByRole("button", { name: "Инвентаризация" }).click();
  await expectTapTargetByName(store, "Инвентаризация");
  await store.getByRole("button", { name: "Начать пересчёт" }).click();
  const positions = store.locator("li").filter({ has: store.getByRole("textbox") });
  await expect(positions.first()).toBeVisible({ timeout: 60_000 });
  // На складе есть позиции прошлых прогонов с такими же остатками: проверяем строку нужного материала.
  await expect(positionOf(store, sand.name).getByText("Учёт: 40 кг")).toBeVisible();
  await expect(positionOf(store, gravel.name).getByText("Учёт: 5,5 кг")).toBeVisible();
  // Позиции склада, которые не участвуют в сценарии, считаем «как по учёту»: факт равен учётному остатку.
  for (const position of await positions.all()) {
    const name = (await position.locator("p").first().innerText()).trim();
    const book = (await position.innerText()).match(/Учёт:\s*([\d\s,]+)/)?.[1] ?? "0";
    const fact = name === sand.name ? "37" : name === gravel.name ? "6,5" : book.replace(/\s/g, "").replace(",", ".");
    await position.getByRole("textbox").fill(fact);
  }
  await expectNoHorizontalScroll(store);
  await expectTapTargetByName(store, "Сохранить");
  await expectTapTargetByName(store, "Отправить прорабу");
  await shot(store, "stock-inventory-375");
  await store.getByRole("button", { name: "Отправить прорабу" }).click();
  await expect(store.getByText(/Пересчёт отправлен прорабу/)).toBeVisible({ timeout: 90_000 });
  await shot(store, "stock-inventory-sent-375");

  await decideRoute(request, await latestDocument(request, owner, east.id, "inventory_act"));
  expect(await balanceOf(request, owner, warehouse.id, sand.id)).toBe(37);
  expect(await balanceOf(request, owner, warehouse.id, gravel.id)).toBe(6.5);
  const movements = await get<{ kind: string; material_id: string; qty: string }[]>(
    request,
    owner,
    "/api/v1/stock/movements",
    { warehouse_id: warehouse.id, limit: 200 },
  );
  // Корректировки прошлых прогонов лежат в том же складе по другим материалам: смотрим только свои два.
  const ours = movements
    .filter((row) => row.kind === "adjustment" && [sand.id, gravel.id].includes(row.material_id))
    .map((row) => [row.material_id, Number(row.qty)] as const);
  expect(ours).toHaveLength(2);
  expect(Object.fromEntries(ours)).toEqual({ [sand.id]: -3, [gravel.id]: 1 });

  const desktop = await desktopContext(browser);
  const page: Page = await desktop.newPage();
  await signIn(page, "storekeeper");
  await page.goto(`/projects/${east.id}/warehouse`);
  await page.getByRole("button", { name: "Журнал" }).click();
  await expect(page.getByText("Корректировка по пересчёту").first()).toBeVisible({ timeout: 30_000 });
  await shot(page, "stock-journal-1280");

  await storePhone.close();
  await desktop.close();
});

function positionOf(store: Page, name: string) {
  return store.locator("li").filter({ hasText: name });
}

async function createMaterial(
  request: APIRequestContext,
  owner: Session,
  project: Project,
  name: string,
): Promise<Material> {
  const suffix = `${Date.now().toString(36).toUpperCase()}${name.length}`;
  return post<Material>(request, owner, "/api/v1/materials", {
    legal_entity_id: project.legal_entity_id,
    code: `INV-${suffix}-${name.charCodeAt(0)}`,
    name: `${name} ${suffix}`,
    base_unit: "кг",
    requires_certificate: false,
  });
}

// Позиция прихода: выбор материала добавляет строку, количество - в её поле (последнее).
async function addReceiptRow(store: Page, materialId: string, quantity: string): Promise<void> {
  await store.locator("select:visible").first().selectOption(materialId);
  await store.getByLabel("Количество фактически").last().fill(quantity);
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

async function latestDocument(
  request: APIRequestContext,
  owner: Session,
  projectId: string,
  kind: string,
): Promise<Doc> {
  const [latest] = await get<{ id: string }[]>(request, owner, DOCUMENTS, {
    project_id: projectId,
    kind,
    limit: 5,
  });
  return get<Doc>(request, owner, `${DOCUMENTS}/${latest.id}`);
}

// Маршрут до конца: решение принимает тот, чей шаг текущий (прораб или техдиректор).
async function decideRoute(request: APIRequestContext, document: Doc): Promise<void> {
  const foreman = await apiLogin(request, "foreman");
  const techdir = await apiLogin(request, "techdir");
  const version = document.versions[0].id;
  const route = `${DOCUMENTS}/${document.id}/versions/${version}`;
  for (let step = 0; step < 4; step += 1) {
    const current = await get<Route>(request, foreman, `${route}/route`);
    if (current.version_status !== "on_approval") {
      expect(current.version_status).toBe("approved");
      return;
    }
    const role = current.steps.find((item) => item.position === current.current_position)?.role_code;
    await post(request, role === "technical_director" ? techdir : foreman, `${route}/decisions`, {
      position: current.current_position,
      decision: "approve",
    });
  }
  throw new Error("маршрут не завершился за четыре решения");
}
