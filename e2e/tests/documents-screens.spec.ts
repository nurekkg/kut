import { type APIRequestContext, type Browser, type BrowserContext, type Page } from "@playwright/test";

import { expect, test } from "../helpers/test";
import { apiLogin, get, type Session } from "../helpers/api";
import { PHONE } from "../helpers/env";
import { createDocument, type DocumentRow, uploadVersion } from "../helpers/documents";
import { EAST_NAME, newUnit, projectNamed, typeByCode } from "../helpers/equipment";
import { desktopContext, expectNoHorizontalScroll, phoneContext, shot, signIn } from "../helpers/ui";

// Экраны документов для проверки глазами (DoD S5b): реестр и карточка у прораба, сметчика и начальника
// отдела техники, карточка у заказчика, при 375 и 1280 px. Снимки - e2e/artifacts/screens (в git не
// попадают). На 375 px прокрутки страницы вбок быть не должно.

type Width = "phone" | "desktop";

// Документ для карточки: тот, что роль видит с текущей версией (внешним - утверждённый).
async function visibleDocument(request: APIRequestContext, session: Session): Promise<DocumentRow> {
  const rows = await get<DocumentRow[]>(request, session, "/api/v1/documents", { limit: 50 });
  const found = rows.find((row) => row.current_version !== null);
  expect(found, "роль видит документ с версией").toBeDefined();
  return found as DocumentRow;
}

async function shoot(
  browser: Browser,
  width: Width,
  name: string,
  open: (page: Page) => Promise<void>,
): Promise<void> {
  const context: BrowserContext = width === "phone" ? await phoneContext(browser) : await desktopContext(browser);
  try {
    const page = await context.newPage();
    await open(page);
    if (width === "phone") await expectNoHorizontalScroll(page);
    await shot(page, `${name}-${width}`);
  } finally {
    await context.close();
  }
}

test("экраны документов: прораб, сметчик, начальник техники и заказчик при 375 и 1280 px", async ({
  browser,
  request,
}) => {
  // Четырнадцать контекстов входа и снимков: тест длиннее лимита по умолчанию (180 с).
  test.setTimeout(360_000);
  const techdir = await apiLogin(request, "techdir");
  const east = await projectNamed(request, techdir, EAST_NAME);
  const head = await apiLogin(request, "equipment_head");
  const type = await typeByCode(request, head, "SAM");
  const unit = await newUnit(request, head, east, type);
  const passport = await createDocument(request, head, {
    kind: "equipment_passport",
    legal_entity_id: east.legal_entity_id,
    title: "E2E паспорт для экрана",
    links: [{ kind: "equipment_unit", id: unit.id }],
  });
  const customer = await apiLogin(request, "customer");
  const approved = await visibleDocument(request, customer);

  for (const width of ["phone", "desktop"] as const) {
    for (const who of ["foreman", "estimator"] as const) {
      const document = await visibleDocument(request, await apiLogin(request, who));
      await shoot(browser, width, `docs-${who}-registry`, async (page) => {
        await signIn(page, who);
        await page.goto(`/projects/${east.id}/documents`);
        await expect(page.getByTestId("documents-registry")).toBeVisible({ timeout: 40_000 });
      });
      await shoot(browser, width, `docs-${who}-card`, async (page) => {
        await signIn(page, who);
        await page.goto(`/documents/${document.id}`);
        await expect(page.getByTestId("document-card")).toBeVisible({ timeout: 40_000 });
      });
    }

    await shoot(browser, width, "docs-equipment_head-registry", async (page) => {
      await signIn(page, "equipment_head");
      await page.goto(`/projects/${east.id}/documents`);
      await expect(page.getByTestId("documents-registry")).toBeVisible({ timeout: 40_000 });
    });
    await shoot(browser, width, "docs-equipment_head-card", async (page) => {
      await signIn(page, "equipment_head");
      await page.goto(`/documents/${passport.id}`);
      await expect(page.getByTestId("document-card")).toBeVisible({ timeout: 40_000 });
    });

    await shoot(browser, width, "docs-customer-card", async (page) => {
      await signIn(page, "customer");
      await page.goto(`/documents/${approved.id}`);
      await expect(page.getByTestId("document-card")).toBeVisible({ timeout: 40_000 });
    });
  }
});

// Шаг 2 раунда R1-R4: фильтры реестра под кнопкой на телефоне (первая строка на первом экране), объект у заказчика
// вместо ссылки, «Утверждённой версии нет» у черновика, сумма в сомах, срок техники с годом из четырёх цифр.
const PDF = Buffer.from("%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n");

test("экраны документов: фильтры на телефоне, название объекта, черновик, сом и срок техники", async ({
  browser,
  request,
}) => {
  const techdir = await apiLogin(request, "techdir");
  const east = await projectNamed(request, techdir, EAST_NAME);
  const customer = await apiLogin(request, "customer");
  const approved = await visibleDocument(request, customer);
  const seen = await get<{ project_name: string | null }>(request, customer, `/api/v1/documents/${approved.id}`);
  expect(seen.project_name, "заказчик видит название объекта документа").toBeTruthy();

  const draft = await createDocument(request, techdir, {
    kind: "commissioning_act",
    legal_entity_id: east.legal_entity_id,
    project_id: east.id,
    title: `E2E без утверждения ${Date.now()}`,
  });
  await uploadVersion(request, techdir, draft.id, PDF, "application/pdf");
  const financial = await createDocument(request, techdir, {
    kind: "estimate_local",
    legal_entity_id: east.legal_entity_id,
    project_id: east.id,
    title: `E2E смета в сомах ${Date.now()}`,
  });
  await uploadVersion(request, techdir, financial.id, PDF, "application/pdf", "98765.43");
  const head = await apiLogin(request, "equipment_head");
  const type = await typeByCode(request, head, "SAM");
  const unit = await newUnit(request, head, east, type);

  await shoot(browser, "phone", "docs-filters", async (page) => {
    await signIn(page, "foreman");
    await page.goto(`/projects/${east.id}/documents`);
    await expect(page.getByTestId("documents-search")).toBeVisible({ timeout: 40_000 });
    await expect(page.getByTestId("documents-filters-toggle")).toBeVisible();
    await expect(page.getByTestId("documents-filter-from")).toBeHidden();
    const first = page.getByTestId("documents-row").first();
    await expect(first).toBeVisible({ timeout: 40_000 });
    const top = (await first.boundingBox())?.y ?? Number.POSITIVE_INFINITY;
    expect(top, "первая строка реестра на первом экране телефона").toBeLessThan(PHONE.viewport.height);
    await shot(page, "docs-filters-phone-closed");
    await page.getByTestId("documents-filters-toggle").click();
    await expect(page.getByTestId("documents-filter-from")).toBeVisible();
    await expect(page.getByTestId("documents-filter-from")).toHaveAttribute("placeholder", "дд.мм.гггг");
    await page.getByTestId("documents-filter-from").fill("01.01.2026");
    await expect(page.getByTestId("documents-filter-from")).toHaveValue("01.01.2026");
  });

  await shoot(browser, "desktop", "docs-customer-name", async (page) => {
    await signIn(page, "customer");
    await page.goto(`/documents/${approved.id}`);
    await expect(page.getByTestId("document-requisites")).toContainText(seen.project_name as string, {
      timeout: 40_000,
    });
    await expect(page.locator("body")).not.toContainText("Открыть объект");
  });

  await shoot(browser, "desktop", "docs-foreman-no-approved", async (page) => {
    await signIn(page, "foreman");
    await page.goto(`/documents/${draft.id}`);
    await expect(page.getByTestId("document-no-approved")).toContainText("Утверждённой версии нет", {
      timeout: 40_000,
    });
    await expect(page.locator("body")).not.toContainText("Версий пока нет");
  });

  await shoot(browser, "desktop", "docs-estimator-sum", async (page) => {
    await signIn(page, "estimator");
    await page.goto(`/documents/${financial.id}`);
    await expect(page.getByTestId("document-amount")).toContainText(/98\s765,43 сом/, { timeout: 40_000 });
  });

  await shoot(browser, "desktop", "equipment-term-dates", async (page) => {
    await signIn(page, "equipment_head");
    await page.goto(`/equipment/${unit.id}`);
    await expect(page.getByText("31.12.2040").first()).toBeVisible({ timeout: 40_000 });
    await expect(page.locator("body")).not.toContainText(/\b31\.12\.40\b/);
  });
});
