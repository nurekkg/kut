import type { APIRequestContext, Page } from "@playwright/test";
import { randomUUID } from "node:crypto";

import { apiLogin, CONSENT_TEXT_VERSION, get, jwtSubject, type Session } from "../helpers/api";
import { createDocument, DOCUMENTS, documentById, uploadVersion } from "../helpers/documents";
import { EAST_NAME, projectNamed, totp, TOTP_SECRET } from "../helpers/equipment";
import { PASSWORD, today } from "../helpers/env";
import { expect, test } from "../helpers/test";
import {
  desktopContext,
  expectNoHorizontalScroll,
  expectTapTarget,
  phoneContext,
  shot,
  signIn,
  watchErrors,
} from "../helpers/ui";

// Согласование (ADR-015 часть 5): шаги КС-2 - прораб, ПТО, техдиректор, финдиректор. Владелец и финдиректор входят
// с кодом 2FA, поэтому их решения и делегирование идут через вход с кодом (тот же путь, что в браузере).
const PDF = Buffer.from("%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n");
const SCAN = Buffer.from("%PDF-1.4\n1 0 obj << /Type /Catalog /Subject (scan) >> endobj\n%%EOF\n");
// Строка журнала уведомлений (GET /notifications/deliveries): проверяем по документу, событию и роли.
type Delivery = { document_id: string | null; event_type: string; recipient_role: string | null; status: string };

// WhatsApp по умолчанию выключен (S6b-close). Прораб даёт согласие сам; стенд делает то же от его
// имени через API, а проверка согласия в профиле - в notifications-consent.spec.ts.
async function foremanConsents(request: APIRequestContext): Promise<void> {
  const foreman = await apiLogin(request, "foreman");
  const response = await request.patch("/api/v1/notifications/settings", {
    headers: { Authorization: `Bearer ${foreman.token}` },
    data: { notify_whatsapp: true, consent_text_version: CONSENT_TEXT_VERSION },
  });
  expect(response.ok(), `согласие прораба: ${await response.text()}`).toBeTruthy();
}

type Ids = { document: string; version: string };
type Row = { position_no: number | null; name: string; unit: string; quantity: string };
type Project = { id: string; legal_entity_id: string };

function withKey(session: Session): Record<string, string> {
  return { Authorization: `Bearer ${session.token}`, "Idempotency-Key": randomUUID() };
}

// Код 2FA действует одно окно (30 с) и повторно не принимается: если этот человек уже входил в текущем окне,
// ждём следующее. Карта живёт в одном процессе; сценарии идут по очереди (workers: 1).
const usedWindow = new Map<string, number>();

async function freshCode(user: string): Promise<string> {
  const window = () => Math.floor(Date.now() / 30_000);
  const before = usedWindow.get(user) ?? null;
  let slept = 0;
  if (usedWindow.get(user) === window()) {
    slept = 30_000 - (Date.now() % 30_000) + 500;
    await new Promise((resolve) => setTimeout(resolve, slept));
  }
  usedWindow.set(user, window());
  console.log(`[2fa-diag] ${new Date().toISOString()} user=${user} before=${before} window=${window()} slept=${slept}`);
  return totp(TOTP_SECRET);
}

async function signInWithFreshCode(page: Page, user: string): Promise<void> {
  await page.goto("/login");
  await page.locator("#identifier").fill(`${user}@kut.test`);
  await page.locator("#password").fill(PASSWORD);
  await page.getByRole("button", { name: "Войти" }).click();
  await page.locator("#totp-code").waitFor({ timeout: 40_000 });
  await page.locator("#totp-code").fill(await freshCode(user));
  await page.getByRole("button", { name: "Подтвердить" }).click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"), { timeout: 40_000 });
}

async function loginWithCode(request: APIRequestContext, user: string): Promise<Session> {
  const first = await request.post("/api/v1/auth/login", {
    data: { identifier: `${user}@kut.test`, password: PASSWORD },
  });
  expect(first.ok(), `вход ${user}: ${first.status()}`).toBeTruthy();
  const challenge = (await first.json()) as { mfa_token: string };
  const verified = await request.post("/api/v1/auth/mfa/verify", {
    data: { mfa_token: challenge.mfa_token, code: await freshCode(user) },
  });
  expect(verified.ok(), `2FA ${user}: ${verified.status()}`).toBeTruthy();
  return { token: ((await verified.json()) as { access_token: string }).access_token };
}

async function drafted(
  request: APIRequestContext,
  author: Session,
  project: Project,
  kind: "ks2" | "commissioning_act",
  title: string,
): Promise<Ids> {
  const body: Record<string, unknown> = {
    kind,
    legal_entity_id: project.legal_entity_id,
    project_id: project.id,
    title,
  };
  if (kind === "ks2") {
    const [counterparty] = await get<{ id: string }[]>(request, author, `${DOCUMENTS}/counterparties`);
    body.counterparty_id = counterparty?.id;
  }
  const created = await createDocument(request, author, body);
  const version = await uploadVersion(request, author, created.id, PDF, "application/pdf");
  return { document: created.id, version: version.id };
}

// Прораб вносит объёмы (позиции черновика); ПТО ставит цены. Позиции с номером сохраняют цену.
async function fillLines(request: APIRequestContext, session: Session, ids: Ids, rows: Row[]): Promise<void> {
  const response = await request.put(`${DOCUMENTS}/${ids.document}/versions/${ids.version}/lines`, {
    headers: withKey(session),
    data: { lines: rows.map((row) => ({ ...row, work_id: null, project_node_id: null })) },
  });
  expect(response.ok(), `позиции: ${response.status()} ${await response.text()}`).toBeTruthy();
}

async function fillPrices(
  request: APIRequestContext,
  session: Session,
  ids: Ids,
  prices: [number, string][],
): Promise<void> {
  const response = await request.put(`${DOCUMENTS}/${ids.document}/versions/${ids.version}/line-prices`, {
    headers: withKey(session),
    data: { prices: prices.map(([position_no, unit_price]) => ({ position_no, unit_price })) },
  });
  expect(response.ok(), `цены: ${response.status()} ${await response.text()}`).toBeTruthy();
}

async function sendApi(request: APIRequestContext, session: Session, ids: Ids): Promise<void> {
  const response = await request.post(`${DOCUMENTS}/${ids.document}/versions/${ids.version}/send`, {
    headers: withKey(session),
  });
  expect(response.ok(), `отправка: ${response.status()} ${await response.text()}`).toBeTruthy();
}

async function decideApi(
  request: APIRequestContext,
  session: Session,
  ids: Ids,
  position: number,
  decision: "approve" | "reject",
  reason?: string,
): Promise<void> {
  const response = await request.post(`${DOCUMENTS}/${ids.document}/versions/${ids.version}/decisions`, {
    headers: withKey(session),
    data: { position, decision, ...(reason ? { reason } : {}) },
  });
  expect(response.ok(), `решение ${position}: ${response.status()} ${await response.text()}`).toBeTruthy();
}

async function openCard(page: Page, document: string): Promise<void> {
  await page.goto(`/documents/${document}`);
  await expect(page.getByTestId("document-route")).toBeVisible({ timeout: 40_000 });
}

// Карточка и диалоги снимаются целиком: блок маршрута и диалог отказа должны быть видны без прокрутки.
function fullShot(page: Page, name: string): Promise<void> {
  return shot(page, name, true);
}

test("КС-2 проходит четыре шага: прораб, ПТО, техдиректор, финдиректор; версия утверждена", async ({
  browser,
  request,
}) => {
  test.setTimeout(420_000);
  await foremanConsents(request);
  const author = await apiLogin(request, "lawyer");
  const project = await projectNamed(request, await apiLogin(request, "techdir"), EAST_NAME);
  const ids = await drafted(request, author, project, "ks2", `E2E КС-2 маршрут ${Date.now()}`);
  const foreman = await apiLogin(request, "foreman");
  const estimatorApi = await apiLogin(request, "estimator");
  await fillLines(request, foreman, ids, [
    { position_no: null, name: "Бетон B25", unit: "м3", quantity: "12.5" },
    { position_no: null, name: "Арматура", unit: "т", quantity: "1.5" },
  ]);
  await fillPrices(request, estimatorApi, ids, [
    [1, "5400.00"],
    [2, "92000.00"],
  ]);

  // ПТО отправляет из карточки.
  const estimatorContext = await desktopContext(browser);
  const estimator = await estimatorContext.newPage();
  const estimatorErrors = watchErrors(estimator);
  await signIn(estimator, "estimator");
  await openCard(estimator, ids.document);
  await estimator.getByTestId("route-send").click();
  await expect(estimator.getByTestId("route-step")).toHaveCount(4, { timeout: 20_000 });
  await expect(estimator.getByTestId("route-send")).toHaveCount(0);
  await fullShot(estimator, "approvals-1280-1-sent");

  // Прораб на телефоне (375 px): кнопка «Согласовать» крупная, страница без горизонтальной прокрутки.
  const phone = await phoneContext(browser);
  const foremanPage = await phone.newPage();
  const foremanErrors = watchErrors(foremanPage);
  await signIn(foremanPage, "foreman");
  await openCard(foremanPage, ids.document);
  await expectNoHorizontalScroll(foremanPage);
  await expectTapTarget(foremanPage, "route-approve");
  await foremanPage.getByTestId("route-approve").click();
  await expect(foremanPage.getByTestId("route-step").first()).toHaveAttribute("data-status", "approved", {
    timeout: 20_000,
  });
  await fullShot(foremanPage, "approvals-375-1-foreman-approved");
  expect(foremanErrors).toEqual([]);

  await estimator.reload();
  await expect(estimator.getByTestId("route-approve")).toBeVisible({ timeout: 20_000 });
  await estimator.getByTestId("route-approve").click();
  await expect(estimator.getByTestId("route-step").nth(1)).toHaveAttribute("data-status", "approved", {
    timeout: 20_000,
  });

  const techdirContext = await desktopContext(browser);
  const techdir = await techdirContext.newPage();
  await signIn(techdir, "techdir");
  await openCard(techdir, ids.document);
  await techdir.getByTestId("route-approve").click();
  await expect(techdir.getByTestId("route-step").nth(2)).toHaveAttribute("data-status", "approved", {
    timeout: 20_000,
  });

  // Финдиректор - последний шаг: после него версия утверждена, маршрут пройден.
  const financeContext = await desktopContext(browser);
  const finance = await financeContext.newPage();
  await signInWithFreshCode(finance, "finance");
  await openCard(finance, ids.document);
  await finance.getByTestId("route-approve").click();
  await expect(finance.getByTestId("document-route-status")).toContainText("Маршрут пройден", { timeout: 20_000 });
  await fullShot(finance, "approvals-1280-1-approved");

  const after = await documentById(request, author, ids.document);
  expect(after.effective_version_no).toBe(1);
  expect(after.current_version?.status).toBe("approved");

  // Журнал уведомлений (S6b): шаг отправлен в очередь, рассылку делает beat раз в минуту (стенд dev, лог-адаптер).
  // Ждём строку именно этого документа через API (событие approval_requested, роль foreman, статус logged).
  // По тексту страницы не ждём: чтение DOM сразу после перезагрузки не дожидалось отрисовки таблицы
  // (флейк, прогон 39378284671: строки в API были, счётчик в DOM - 0).
  const ownerApi = await loginWithCode(request, "owner");
  let lastDeliveries: Delivery[] = [];
  try {
    await expect
      .poll(
        async () => {
          lastDeliveries = await get<Delivery[]>(request, ownerApi, "/api/v1/notifications/deliveries", {
            limit: 200,
          });
          return (
            lastDeliveries.find(
              (row) =>
                row.document_id === ids.document &&
                row.event_type === "approval_requested" &&
                row.recipient_role === "foreman",
            )?.status ?? null
          );
        },
        { timeout: 240_000, intervals: [10_000] },
      )
      .toBe("logged");
  } catch (error) {
    await test.info().attach("deliveries-api.json", {
      body: JSON.stringify(lastDeliveries, null, 2),
      contentType: "application/json",
    });
    throw error;
  }
  const ownerContext = await desktopContext(browser);
  const owner = await ownerContext.newPage();
  await signInWithFreshCode(owner, "owner");
  await owner.goto("/settings/notifications");
  await expect(owner.getByTestId("notifications-journal")).toContainText("Нужно решение", { timeout: 60_000 });
  await expect(owner.getByTestId("notifications-journal")).toContainText("+996 *** *** 01");
  await fullShot(owner, "notifications-1280-journal");
  // Тот же журнал на телефоне 375 px: без горизонтальной прокрутки.
  const ownerPhone = await phoneContext(browser);
  const ownerOnPhone = await ownerPhone.newPage();
  await signInWithFreshCode(ownerOnPhone, "owner");
  await ownerOnPhone.goto("/settings/notifications");
  await expect(ownerOnPhone.getByTestId("notifications-journal")).toContainText("+996 *** *** 01", {
    timeout: 60_000,
  });
  await expectNoHorizontalScroll(ownerOnPhone);
  await shot(ownerOnPhone, "notifications-375-journal", true);
  expect(estimatorErrors).toEqual([]);
});

test("отказ на шаге ПТО с причиной: черновик, правка прораба, повторная отправка, утверждено", async ({
  browser,
  request,
}) => {
  test.setTimeout(420_000);
  const author = await apiLogin(request, "lawyer");
  const project = await projectNamed(request, await apiLogin(request, "techdir"), EAST_NAME);
  const ids = await drafted(request, author, project, "ks2", `E2E КС-2 отказ ${Date.now()}`);
  const foreman = await apiLogin(request, "foreman");
  const estimator = await apiLogin(request, "estimator");
  const techdir = await apiLogin(request, "techdir");
  const finance = await loginWithCode(request, "finance");
  await fillLines(request, foreman, ids, [
    { position_no: null, name: "Бетон B25", unit: "м3", quantity: "12.5" },
    { position_no: null, name: "Арматура", unit: "т", quantity: "1.5" },
  ]);
  await fillPrices(request, estimator, ids, [
    [1, "5400.00"],
    [2, "92000.00"],
  ]);
  await sendApi(request, estimator, ids);
  await decideApi(request, foreman, ids, 1, "approve");

  const context = await desktopContext(browser);
  const page = await context.newPage();
  const errors = watchErrors(page);
  await signIn(page, "estimator");
  await openCard(page, ids.document);
  await page.getByTestId("route-reject-open").click();
  await page.getByTestId("route-reject-reason").fill("Не сходятся объёмы в позиции 1");
  await fullShot(page, "approvals-1280-2-reject-dialog");
  await page.getByRole("button", { name: "Вернуть", exact: true }).click();

  // Версия вернулась в черновик: шаг ПТО отклонён, причина видна, отправка снова доступна.
  await expect(page.getByTestId("route-send")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId("route-step").nth(1)).toHaveAttribute("data-status", "rejected");
  await expect(page.getByTestId("route-step-reason")).toContainText("Не сходятся объёмы в позиции 1");
  await fullShot(page, "approvals-1280-2-returned");

  // Прораб правит объём первой позиции: цена остаётся на месте (номер позиции - её идентичность).
  await fillLines(request, foreman, ids, [
    { position_no: 1, name: "Бетон B25", unit: "м3", quantity: "13.0" },
    { position_no: 2, name: "Арматура", unit: "т", quantity: "1.5" },
  ]);
  await page.reload();
  await page.getByTestId("route-send").click();
  await expect(page.getByTestId("document-route-status")).toContainText("Попытка 2", { timeout: 20_000 });

  // Прошлые попытки: отдельным списком - решение прораба и возврат сметчика с причиной (S6b, STEP 0).
  const past = page.getByTestId("route-past-attempts");
  await expect(past).toBeVisible({ timeout: 20_000 });
  await expect(past.getByTestId("route-decision")).toHaveCount(2);
  const returned = past.getByTestId("route-decision").filter({ hasText: "Вернул" });
  await expect(returned).toHaveCount(1);
  await expect(returned).toContainText("Сметчик (dev)");
  await expect(past.getByTestId("route-decision-reason")).toContainText("Не сходятся объёмы в позиции 1");
  await fullShot(page, "approvals-1280-2-past-attempts");

  await decideApi(request, foreman, ids, 1, "approve");
  await decideApi(request, estimator, ids, 2, "approve");
  await decideApi(request, techdir, ids, 3, "approve");
  await decideApi(request, finance, ids, 4, "approve");
  await page.reload();
  await expect(page.getByTestId("document-route-status")).toContainText("Маршрут пройден", { timeout: 20_000 });
  await fullShot(page, "approvals-1280-2-resent-approved");
  expect(errors).toEqual([]);
});

test("замещение: финдиректор назначил владельца; решение видно как «за финдиректора»", async ({
  browser,
  request,
}) => {
  test.setTimeout(420_000);
  const author = await apiLogin(request, "lawyer");
  const project = await projectNamed(request, await apiLogin(request, "techdir"), EAST_NAME);
  const finance = await loginWithCode(request, "finance");
  const owner = await loginWithCode(request, "owner");
  const delegation = await request.post(`${DOCUMENTS}/delegations`, {
    headers: withKey(finance),
    data: {
      delegate_id: jwtSubject(owner.token),
      role_code: "finance_director",
      valid_from: today(),
      valid_to: today(1),
    },
  });
  expect(delegation.status(), `замещение: ${await delegation.text()}`).toBe(201);
  const { id: delegationId } = (await delegation.json()) as { id: string };

  const ids = await drafted(request, author, project, "ks2", `E2E КС-2 замещение ${Date.now()}`);
  const foreman = await apiLogin(request, "foreman");
  const estimator = await apiLogin(request, "estimator");
  const techdir = await apiLogin(request, "techdir");
  await fillLines(request, foreman, ids, [{ position_no: null, name: "Бетон B25", unit: "м3", quantity: "12.5" }]);
  await fillPrices(request, estimator, ids, [[1, "5400.00"]]);
  await sendApi(request, estimator, ids);
  await decideApi(request, foreman, ids, 1, "approve");
  await decideApi(request, estimator, ids, 2, "approve");
  await decideApi(request, techdir, ids, 3, "approve");

  // Владелец на шаге финдиректора: решение за него, в маршруте видно «за кого».
  const context = await desktopContext(browser);
  const page = await context.newPage();
  const errors = watchErrors(page);
  await signInWithFreshCode(page, "owner");
  await openCard(page, ids.document);
  await page.getByTestId("route-approve").click();
  await expect(page.getByTestId("route-step").nth(3)).toHaveAttribute("data-status", "approved", {
    timeout: 20_000,
  });
  await expect(page.getByTestId("route-step").nth(3)).toContainText("за Финансовый директор");
  await fullShot(page, "approvals-1280-3-substitute");
  // Мои замещения (S6b-close): замещение владельца видно в профиле вместе с именами сторон.
  await page.goto("/profile");
  await expect(page.getByTestId("my-delegations")).toContainText("Финансовый директор", { timeout: 20_000 });
  await fullShot(page, "delegations-1280-profile");

  const revoked = await request.post(`${DOCUMENTS}/delegations/${delegationId}/revoke`, {
    headers: withKey(finance),
  });
  expect(revoked.status()).toBe(204);
  expect(errors).toEqual([]);
});

test("подписанный скан: к согласованной версии, новая версия signed становится действующей", async ({
  browser,
  request,
}) => {
  test.setTimeout(420_000);
  const author = await apiLogin(request, "lawyer");
  const project = await projectNamed(request, await apiLogin(request, "techdir"), EAST_NAME);
  const ids = await drafted(request, author, project, "ks2", `E2E КС-2 скан ${Date.now()}`);
  const foreman = await apiLogin(request, "foreman");
  const estimator = await apiLogin(request, "estimator");
  const techdir = await apiLogin(request, "techdir");
  const finance = await loginWithCode(request, "finance");
  await fillLines(request, foreman, ids, [{ position_no: null, name: "Бетон B25", unit: "м3", quantity: "12.5" }]);
  await fillPrices(request, estimator, ids, [[1, "5400.00"]]);
  await sendApi(request, estimator, ids);
  await decideApi(request, foreman, ids, 1, "approve");
  await decideApi(request, estimator, ids, 2, "approve");
  await decideApi(request, techdir, ids, 3, "approve");
  await decideApi(request, finance, ids, 4, "approve");

  const context = await desktopContext(browser);
  const page = await context.newPage();
  const errors = watchErrors(page);
  await signIn(page, "techdir");
  await openCard(page, ids.document);
  await page.getByTestId("route-scan-open").click();
  await page.getByTestId("route-scan-file").setInputFiles({ name: "scan.pdf", mimeType: "application/pdf", buffer: SCAN });
  await page.getByTestId("route-scan-submit").click();
  await expect(page.getByTestId("route-scan-dialog")).toBeHidden({ timeout: 60_000 });
  await expect(page.getByTestId("route-source-version")).toContainText("1");
  await fullShot(page, "approvals-1280-4-signed-parent-route");

  const after = await documentById(request, author, ids.document);
  expect(after.current_version?.status).toBe("signed");
  expect(after.effective_version_no).toBe(2);
  expect(after.versions.find((version) => version.version_no === 1)?.status).toBe("archived");
  expect(errors).toEqual([]);
});

test("очередь и счётчик: шаг виден участнику на 375 и 1280 px, после решения уходит из очереди", async ({
  browser,
  request,
}) => {
  test.setTimeout(300_000);
  const author = await apiLogin(request, "lawyer");
  const project = await projectNamed(request, await apiLogin(request, "techdir"), EAST_NAME);
  const title = `E2E акт очереди ${Date.now()}`;
  const ids = await drafted(request, author, project, "commissioning_act", title);
  await sendApi(request, author, ids);

  const phone = await phoneContext(browser);
  const foreman = await phone.newPage();
  const foremanErrors = watchErrors(foreman);
  await signIn(foreman, "foreman");
  await foreman.goto("/approvals");
  const foremanRow = foreman.getByTestId("document-approval-row").filter({ hasText: title });
  await expect(foremanRow).toBeVisible({ timeout: 40_000 });
  await expect(foreman.getByTestId("approvals-count")).toBeVisible();
  await expectNoHorizontalScroll(foreman);
  await fullShot(foreman, "approvals-375-5-queue-foreman");
  await expectTapTarget(foreman, "document-approve");
  await foremanRow.getByTestId("document-approve").click();
  await expect(foremanRow).toBeHidden({ timeout: 20_000 });
  expect(foremanErrors).toEqual([]);

  const context = await desktopContext(browser);
  const techdir = await context.newPage();
  const techdirErrors = watchErrors(techdir);
  await signIn(techdir, "techdir");
  await techdir.goto("/approvals");
  const techdirRow = techdir.getByTestId("document-approval-row").filter({ hasText: title });
  await expect(techdirRow).toBeVisible({ timeout: 40_000 });
  await expect(techdirRow.getByTestId("document-approval-step")).toContainText("Технический директор");
  await expect(techdir.getByTestId("approvals-count")).toBeVisible();
  await fullShot(techdir, "approvals-1280-5-queue-techdir");
  expect(techdirErrors).toEqual([]);
});
