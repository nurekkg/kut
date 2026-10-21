import { expect, type APIRequestContext, type Page } from "@playwright/test";

import { PASSWORD } from "./env";
import { get, post, type Session } from "./api";
import { totp, TOTP_SECRET } from "./equipment";

// Склад S7b: вход владельца (2FA), заведение склада и материалов, файлы для загрузки.
// Владелец - единственная роль, которая заводит склад и каталог на объекте (ADR-016 часть 5).

let lastOwnerWindow = -1;

export async function ownerSession(request: APIRequestContext): Promise<Session> {
  // Один код TOTP на 30 секунд: второй вход в том же окне отвергается, ждём следующее окно.
  const window = () => Math.floor(Date.now() / 30_000);
  if (lastOwnerWindow === window()) {
    await new Promise((resolve) => setTimeout(resolve, 30_000 - (Date.now() % 30_000) + 500));
  }
  lastOwnerWindow = window();
  const first = await request.post("/api/v1/auth/login", {
    data: { identifier: "owner@kut.test", password: PASSWORD },
  });
  expect(first.ok(), `вход owner: ${first.status()}`).toBeTruthy();
  const challenge = (await first.json()) as { mfa_token: string };
  const verified = await request.post("/api/v1/auth/mfa/verify", {
    data: { mfa_token: challenge.mfa_token, code: totp(TOTP_SECRET) },
  });
  expect(verified.ok(), `2FA owner: ${verified.status()}`).toBeTruthy();
  return { token: ((await verified.json()) as { access_token: string }).access_token };
}

export type Warehouse = { id: string; project_id: string; name: string };

// Склад объекта: берём существующий или заводим новый (демо-данные склада не содержат).
export async function ensureWarehouse(
  request: APIRequestContext,
  owner: Session,
  projectId: string,
): Promise<Warehouse> {
  const existing = await get<Warehouse[]>(request, owner, "/api/v1/warehouses", { project_id: projectId });
  if (existing.length > 0) return existing[0];
  return post<Warehouse>(request, owner, "/api/v1/warehouses", {
    project_id: projectId,
    name: `Склад E2E ${Date.now()}`,
  });
}

export type Material = { id: string; code: string; name: string };

// Сертифицированный материал: базовая единица кг, мешок = 25 кг (коэффициент к базе).
export async function certifiedMaterial(
  request: APIRequestContext,
  owner: Session,
  legalEntityId: string,
): Promise<Material> {
  const suffix = Date.now().toString(36).toUpperCase();
  const code = `CEM-${suffix}`;
  const name = `Цемент ${code}`;
  const material = await post<Material>(request, owner, "/api/v1/materials", {
    legal_entity_id: legalEntityId,
    code,
    name,
    base_unit: "кг",
    requires_certificate: true,
  });
  await post(request, owner, `/api/v1/materials/${material.id}/units`, { unit: "мешок", factor: 25 });
  return { id: material.id, code, name };
}

// Файлы-заглушки: сервер проверяет сигнатуру (JPEG/PDF), остальное не читает.
export function jpegFile(name = "photo.jpg") {
  return { name, mimeType: "image/jpeg", buffer: Buffer.concat([Buffer.from([0xff, 0xd8, 0xff, 0xe0]), Buffer.alloc(24)]) };
}

export function pdfFile(name = "certificate.pdf") {
  return {
    name,
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n"),
  };
}

// Тап-цель по подписи кнопки (у кнопок склада нет data-testid): высота не меньше 48 px.
export async function expectTapTargetByName(page: Page, name: string): Promise<void> {
  const box = await page.getByRole("button", { name, exact: true }).first().boundingBox();
  expect(box, `кнопка «${name}» не найдена`).toBeTruthy();
  expect(box?.height ?? 0, `кнопка «${name}»: высота`).toBeGreaterThanOrEqual(48);
}

// Остаток (в базовой единице) по материалу на складе, из API.
export async function balanceOf(
  request: APIRequestContext,
  session: Session,
  warehouseId: string,
  materialId: string,
): Promise<number> {
  const rows = await get<{ material_id: string; qty: string }[]>(request, session, "/api/v1/stock/balances", {
    warehouse_id: warehouseId,
  });
  const row = rows.find((item) => item.material_id === materialId);
  return row ? Number(row.qty) : 0;
}

// Доступ к юрлицу (ADR-016): каталог и остатки видны по юрлицу пользователя. Выдаёт владелец через
// штатный экран «Настройки» (PUT /settings/users/{id}/access), сиды доступ к юрлицу не дают кладовщику
// и снабженцу. Существующие объекты и юрлица пользователя сохраняются.
type AdminUser = { id: string; email: string | null; project_ids: string[]; legal_entity_ids: string[] };

export async function grantLegalAccess(
  request: APIRequestContext,
  owner: Session,
  login: string,
  legalEntityId: string,
): Promise<void> {
  const users = await get<AdminUser[]>(request, owner, "/api/v1/settings/users");
  const user = users.find((row) => row.email === `${login}@kut.test`);
  expect(user, `пользователь ${login} не найден`).toBeTruthy();
  if (user!.legal_entity_ids.includes(legalEntityId)) return;
  const response = await request.put(`/api/v1/settings/users/${user!.id}/access`, {
    headers: { Authorization: `Bearer ${owner.token}` },
    data: { project_ids: user!.project_ids, legal_entity_ids: [...user!.legal_entity_ids, legalEntityId] },
  });
  expect(response.ok(), `доступ ${login}: ${response.status()} ${await response.text()}`).toBeTruthy();
}

// Вход владельца в интерфейс: пароль и код TOTP (окно 30 с общее с ownerSession).
export async function ownerSignIn(page: Page): Promise<void> {
  const window = () => Math.floor(Date.now() / 30_000);
  if (lastOwnerWindow === window()) {
    await new Promise((resolve) => setTimeout(resolve, 30_000 - (Date.now() % 30_000) + 500));
  }
  lastOwnerWindow = window();
  await page.goto("/login");
  await page.locator("#identifier").fill("owner@kut.test");
  await page.locator("#password").fill(PASSWORD);
  await page.getByRole("button", { name: "Войти" }).click();
  await page.locator("#totp-code").waitFor({ timeout: 40_000 });
  await page.locator("#totp-code").fill(totp(TOTP_SECRET));
  await page.getByRole("button", { name: "Подтвердить" }).click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"), { timeout: 40_000 });
}
