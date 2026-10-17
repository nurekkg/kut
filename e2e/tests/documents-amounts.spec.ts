import { expect, test } from "../helpers/test";
import { apiLogin } from "../helpers/api";
import { transitionVersion } from "../helpers/db";
import { createDocument, documentById, setVersionAmount } from "../helpers/documents";
import { EAST_NAME, projectNamed } from "../helpers/equipment";
import { desktopContext, shot, signIn, watchErrors } from "../helpers/ui";

// Сценарий 5 (R1): сумма принадлежит версии. Сметчик вносит сумму при загрузке и правит её, пока версия в
// черновике. Когда система переводит версию из черновика, поле правки пропадает, а API отвечает 409.
const PDF = Buffer.from("%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n");

test("сумма версии: вносится при загрузке, правится в черновике, после утверждения не меняется", async ({
  browser,
  request,
}) => {
  const estimator = await apiLogin(request, "estimator");
  const east = await projectNamed(request, estimator, EAST_NAME);
  const document = await createDocument(request, estimator, {
    kind: "estimate_local",
    legal_entity_id: east.legal_entity_id,
    project_id: east.id,
    title: `E2E смета с суммой ${Date.now()}`,
  });
  const desktop = await desktopContext(browser);
  const page = await desktop.newPage();
  const errors = watchErrors(page);
  await signIn(page, "estimator");

  await page.goto(`/documents/${document.id}`);
  await expect(page.getByTestId("document-card")).toBeVisible({ timeout: 40_000 });
  await page.getByTestId("document-upload-open").click();
  await page.getByTestId("document-upload-file").setInputFiles({
    name: "smeta.pdf",
    mimeType: "application/pdf",
    buffer: PDF,
  });
  await page.getByTestId("document-upload-amount").fill("150,00");
  await page.getByTestId("document-upload-submit").click();
  await expect(page.getByTestId("document-upload-dialog")).toBeHidden({ timeout: 60_000 });
  await expect(page.getByTestId("document-version-amount")).toContainText("150,00");

  await page.getByTestId("document-version-amount-open").click();
  await page.getByTestId("document-amount-input").fill("175,50");
  await page.getByTestId("document-amount-submit").click();
  await expect(page.getByTestId("document-amount-dialog")).toBeHidden({ timeout: 40_000 });
  await expect(page.getByTestId("document-version-amount")).toContainText("175,50");
  await shot(page, "document-amount-draft");

  // Переход из черновика делает система (фикстура): дальше правки нет ни в интерфейсе, ни в API.
  const version = (await documentById(request, estimator, document.id)).versions[0];
  expect(version, "версия загружена").toBeDefined();
  await transitionVersion(version!.id, "on_approval");
  await transitionVersion(version!.id, "approved");
  await page.reload();
  await expect(page.getByTestId("document-version-amount")).toContainText("175,50");
  await expect(page.getByTestId("document-version-amount-open")).toHaveCount(0);
  const refused = await setVersionAmount(request, estimator, document.id, version!.id, "1.00");
  expect(refused.status()).toBe(409);
  expect(await refused.json()).toEqual({ detail: "document.version_not_draft" });
  await shot(page, "document-amount-approved");
  expect(errors).toEqual([]);
});
