import { type APIRequestContext, type Browser, type BrowserContext, type Page } from "@playwright/test";

import { expect, test } from "../helpers/test";
import { apiLogin, get, type Session } from "../helpers/api";
import { createDocument, type DocumentRow } from "../helpers/documents";
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
