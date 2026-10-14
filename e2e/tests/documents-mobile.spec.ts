import { expect, test } from "../helpers/test";
import { apiLogin } from "../helpers/api";
import { EAST_NAME, projectNamed } from "../helpers/equipment";
import { expectNoHorizontalScroll, phoneContext, shot, signIn } from "../helpers/ui";

// Сценарий 3 (DoD S5b): реестр на 375 px показан карточками и не даёт горизонтальной прокрутки.
test("реестр документов на 375 px: карточки без горизонтальной прокрутки", async ({ browser, request }) => {
  const foreman = await apiLogin(request, "foreman");
  const east = await projectNamed(request, foreman, EAST_NAME);
  const phone = await phoneContext(browser);
  const page = await phone.newPage();
  await signIn(page, "foreman");

  await page.goto(`/projects/${east.id}/documents`);
  await expect(page.getByTestId("documents-registry")).toBeVisible({ timeout: 40_000 });
  await expect(page.getByTestId("documents-cards").first()).toBeVisible();
  await expectNoHorizontalScroll(page);
  await shot(page, "documents-registry-375");
});
