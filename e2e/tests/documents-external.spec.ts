import { expect, test } from "../helpers/test";
import { apiLogin, get } from "../helpers/api";
import { type DocumentRow } from "../helpers/documents";
import { EAST_NAME, projectNamed } from "../helpers/equipment";
import { desktopContext, signIn } from "../helpers/ui";

// Сценарий 5 (DoD S5b): заказчик и субподрядчик - только чтение. В реестре и карточке нет кнопок
// загрузки, архивирования и выгрузки; в списке только утверждённые версии.
const VISIBLE = ["approved", "signed"];

test("заказчик и субподрядчик: только чтение, только утверждённое", async ({ browser, request }) => {
  const techdir = await apiLogin(request, "techdir");
  const east = await projectNamed(request, techdir, EAST_NAME);
  const desktop = await desktopContext(browser);

  for (const who of ["customer", "sub_a"] as const) {
    const session = await apiLogin(request, who);
    const listed = await get<DocumentRow[]>(request, session, "/api/v1/documents", {
      project_id: east.id,
    });
    for (const row of listed) expect(VISIBLE).toContain(row.latest_status);
    if (who === "customer") expect(listed.length).toBeGreaterThan(0);

    const page = await desktop.newPage();
    await signIn(page, who);
    await page.goto(`/projects/${east.id}/documents`);
    await expect(page.getByTestId("documents-registry")).toBeVisible({ timeout: 40_000 });
    await expect(page.getByTestId("documents-create")).toHaveCount(0);
    await expect(page.getByTestId("documents-export")).toHaveCount(0);

    const first = listed[0];
    if (first) {
      await page.goto(`/documents/${first.id}`);
      await expect(page.getByTestId("document-card")).toBeVisible({ timeout: 40_000 });
      await expect(page.getByTestId("document-upload-open")).toHaveCount(0);
      await expect(page.getByTestId("document-archive-open")).toHaveCount(0);
    }
    await page.close();
  }
});
