import { expect, test } from "../helpers/test";
import { apiLogin, get } from "../helpers/api";
import { createDocument, documentById, uploadVersion, type DocumentRow } from "../helpers/documents";
import { EAST_NAME, newUnit, projectNamed, typeByCode } from "../helpers/equipment";
import { desktopContext, shot, signIn } from "../helpers/ui";

// Сценарий 4 (DoD S5b, R1-R3): прораб без documents:read:financial не видит сумму (ни колонку, ни текст),
// у финансового документа нет кнопки скачивания. Страховка техники (денежный вид) - та же история:
// прораб видит документ, а суммы и файла у него нет (право equipment:read:rates). Экспорта тоже нет.
// Сумма 98765.43 видна в пробелах и запятой, как её форматирует интерфейс (пробел разряда - \s).
const AMOUNT = /98\s765,43/;
const PDF = Buffer.from("%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n");

test("прораб: суммы нет в DOM, скачивания финансового вида нет, экспорта нет", async ({
  browser,
  request,
}) => {
  const techdir = await apiLogin(request, "techdir");
  const east = await projectNamed(request, techdir, EAST_NAME);
  const financial = await createDocument(request, techdir, {
    kind: "estimate_local",
    legal_entity_id: east.legal_entity_id,
    project_id: east.id,
    title: `E2E смета ${Date.now()}`,
  });
  const version = await uploadVersion(request, techdir, financial.id, PDF, "application/pdf", "98765.43");
  expect(version.amount).toBe("98765.43");
  const desktop = await desktopContext(browser);
  const page = await desktop.newPage();
  await signIn(page, "foreman");

  await page.goto(`/projects/${east.id}/documents`);
  await expect(page.getByTestId("documents-registry")).toBeVisible({ timeout: 40_000 });
  await expect(page.getByTestId("documents-row").first()).toBeVisible({ timeout: 40_000 });
  await expect(page.getByTestId("documents-amount-header")).toHaveCount(0);
  await expect(page.getByTestId("documents-export")).toHaveCount(0);
  await expect(page.locator("body")).not.toContainText(AMOUNT);
  await shot(page, "documents-registry-foreman");

  await page.goto(`/documents/${financial.id}`);
  await expect(page.getByTestId("document-card")).toBeVisible({ timeout: 40_000 });
  await expect(page.getByTestId("document-amount")).toHaveCount(0);
  await expect(page.getByTestId("document-version-amount")).toHaveCount(0);
  await expect(page.locator("body")).not.toContainText(AMOUNT);

  // Сид утверждает версии видов КС-2, договоров и других финансовых видов (не сметы): берём любой такой документ.
  const approved = (await get<DocumentRow[]>(request, techdir, "/api/v1/documents", {
    project_id: east.id,
    limit: 50,
  })).find((row) => row.financial && row.current_version !== null);
  expect(approved, "в демо-данных нет утверждённой финансовой версии").toBeDefined();
  await page.goto(`/documents/${approved!.id}`);
  await expect(page.getByTestId("document-card")).toBeVisible({ timeout: 40_000 });
  await expect(page.getByTestId("document-current-version")).toBeVisible();
  await expect(page.getByTestId("document-download")).toHaveCount(0);
  await expect(page.getByTestId("document-version-download")).toHaveCount(0);
});

test("прораб: страховка техники видна, но без суммы и без файла (R3)", async ({ browser, request }) => {
  const head = await apiLogin(request, "equipment_head");
  const east = await projectNamed(request, head, EAST_NAME);
  const type = await typeByCode(request, head, "SAM");
  const unit = await newUnit(request, head, east, type);
  const insurance = await createDocument(request, head, {
    kind: "equipment_insurance",
    legal_entity_id: east.legal_entity_id,
    title: `E2E страховка ${Date.now()}`,
    links: [{ kind: "equipment_unit", id: unit.id }],
  });
  await uploadVersion(request, head, insurance.id, PDF, "application/pdf", "4321.00");
  const desktop = await desktopContext(browser);
  const page = await desktop.newPage();
  await signIn(page, "foreman");

  await page.goto(`/documents/${insurance.id}`);
  await expect(page.getByTestId("document-card")).toBeVisible({ timeout: 40_000 });
  await expect(page.getByTestId("document-current-version")).toBeVisible();
  await expect(page.getByTestId("document-amount")).toHaveCount(0);
  await expect(page.getByTestId("document-version-amount")).toHaveCount(0);
  await expect(page.getByTestId("document-download")).toHaveCount(0);
  await expect(page.getByTestId("document-version-download")).toHaveCount(0);
  await expect(page.locator("body")).not.toContainText("4321");
  await shot(page, "document-insurance-foreman");
  const after = await documentById(request, head, insurance.id);
  expect(after.versions[0]?.amount).toBe("4321.00");
});
