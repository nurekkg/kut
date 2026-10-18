import { expect, test } from "../helpers/test";
import { apiLogin } from "../helpers/api";
import { createDocument, documentById } from "../helpers/documents";
import { EAST_NAME, projectNamed } from "../helpers/equipment";
import { desktopContext, signIn } from "../helpers/ui";
import { docxWithMacros } from "../helpers/zip";

// Сценарий 2 (DoD S5b): DOCX с макросами отклонён, текст ошибки понятен, версия не создана.
// Ошибка 422 в консоли браузера ожидаема, поэтому журнал ошибок здесь не проверяем.
const DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

test("файл с макросами отклоняется понятным текстом, версия не создаётся", async ({ browser, request }) => {
  const techdir = await apiLogin(request, "techdir");
  const east = await projectNamed(request, techdir, EAST_NAME);
  const document = await createDocument(request, techdir, {
    kind: "commissioning_act",
    legal_entity_id: east.legal_entity_id,
    project_id: east.id,
    title: `E2E макросы ${Date.now()}`,
  });
  const desktop = await desktopContext(browser);
  const page = await desktop.newPage();
  await signIn(page, "techdir");

  await page.goto(`/documents/${document.id}`);
  await expect(page.getByTestId("document-card")).toBeVisible({ timeout: 40_000 });
  await page.getByTestId("document-upload-open").click();
  await page.getByTestId("document-upload-file").setInputFiles({
    name: "macros.docx",
    mimeType: DOCX,
    buffer: docxWithMacros(),
  });
  await page.getByTestId("document-upload-submit").click();

  await expect(page.getByTestId("document-upload-error")).toContainText("макрос", { timeout: 60_000 });
  const after = await documentById(request, techdir, document.id);
  expect(after.versions).toHaveLength(0);
});
