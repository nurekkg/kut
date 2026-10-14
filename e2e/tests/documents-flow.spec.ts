import { expect, test } from "../helpers/test";
import { apiLogin } from "../helpers/api";
import { documentById } from "../helpers/documents";
import { EAST_NAME, projectNamed } from "../helpers/equipment";
import { desktopContext, shot, signIn, watchErrors } from "../helpers/ui";

// Сценарий 1 (DoD S5b): документ создан, версия загружена, скачивание идёт как attachment по ссылке
// на 120 с, версия видна в карточке. Вид «Акт ввода» не финансовый, поэтому скачивание доступно
// техдиру без права на суммы.
const PDF = Buffer.from("%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n");

test("документ: создать, загрузить версию, скачать как attachment на 120 с, версия в карточке", async ({
  browser,
  request,
}) => {
  const techdir = await apiLogin(request, "techdir");
  const east = await projectNamed(request, techdir, EAST_NAME);
  const desktop = await desktopContext(browser);
  const page = await desktop.newPage();
  const errors = watchErrors(page);
  await signIn(page, "techdir");

  await page.goto(`/projects/${east.id}/documents`);
  await expect(page.getByTestId("documents-registry")).toBeVisible({ timeout: 40_000 });
  await page.getByTestId("documents-create").click();
  const title = `E2E акт ${Date.now()}`;
  await page.getByTestId("documents-create-kind").selectOption("commissioning_act");
  await page.getByTestId("documents-create-title").fill(title);
  await page.getByTestId("documents-create-submit").click();

  await page.waitForURL(/\/documents\/[0-9a-f-]{36}$/, { timeout: 40_000 });
  const documentId = new URL(page.url()).pathname.split("/").pop() ?? "";
  await expect(page.getByTestId("document-card")).toBeVisible({ timeout: 40_000 });
  await expect(page.getByRole("heading", { name: title })).toBeVisible();

  await page.getByTestId("document-upload-open").click();
  await page.getByTestId("document-upload-file").setInputFiles({
    name: "act.pdf",
    mimeType: "application/pdf",
    buffer: PDF,
  });
  await page.getByTestId("document-upload-comment").fill("Первая версия");
  await page.getByTestId("document-upload-submit").click();
  await expect(page.getByTestId("document-upload-dialog")).toBeHidden({ timeout: 60_000 });
  await expect(page.getByTestId("document-version-row")).toHaveCount(1);
  await shot(page, "document-version-uploaded");

  // Черновик не текущая версия: ссылку даёт кнопка в строке версии, а не блок текущей версии.
  const [link] = await Promise.all([
    page.waitForResponse((response) => /\/versions\/[0-9a-f-]{36}\/download-url$/.test(response.url())),
    page.getByTestId("document-version-download").first().click(),
  ]);
  const { url, expires_in } = (await link.json()) as { url: string; expires_in: number };
  expect(expires_in).toBe(120);
  expect(new URL(url).searchParams.get("X-Amz-Expires")).toBe("120");
  const stored = await request.get(url);
  expect(stored.ok()).toBeTruthy();
  expect(stored.headers()["content-disposition"]).toContain("attachment");

  const after = await documentById(request, techdir, documentId);
  expect(after.versions).toHaveLength(1);
  expect(after.versions[0]?.status).toBe("draft");
  expect(errors).toEqual([]);
});
