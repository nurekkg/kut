import { expect, test } from "../helpers/test";

import { apiLogin, createPlan, submitFactWithPhoto } from "../helpers/api";
import { PHOTO } from "../helpers/env";
import { desktopContext, shot, signIn } from "../helpers/ui";

// Очередь согласования: если снимки факта не пришли (сбой запроса, ответ короче, чем нужно),
// карточка об этом говорит, а не остаётся молча без превью: прораб не должен подтверждать, не
// увидев доказательств. Когда снимки приходят, предупреждение исчезает.
test("карточка предупреждает, когда снимки факта не загрузились", async ({ browser, request }) => {
  const foreman = await apiLogin(request, "foreman");
  const master = await apiLogin(request, "master");
  const plan = await createPlan(request, foreman);
  await submitFactWithPhoto(request, master, plan, PHOTO);

  const desk = await desktopContext(browser);
  const page = await desk.newPage();
  await signIn(page, "foreman");

  // Список снимков для очереди «не приходит»: пустой ответ вместо данных.
  const photosList = /\/photos\?/;
  const empty = (route: import("@playwright/test").Route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  await page.route(photosList, empty);
  await page.goto("/approvals");
  const card = page.getByTestId("approval").filter({ hasText: plan.nodeName });
  await expect(card).toHaveCount(1);
  const warning = card.getByTestId("photos-missing");
  await expect(warning).toBeVisible();
  await expect(warning).toContainText("0 из 1");
  await expect(card.getByTestId("approval-photos")).toHaveCount(0);
  await shot(page, "approvals-photos-missing");

  // Снимки пришли: превью на месте, предупреждения нет.
  await page.unroute(photosList, empty);
  await page.goto("/approvals");
  const loaded = page.getByTestId("approval").filter({ hasText: plan.nodeName });
  await expect(loaded.getByTestId("approval-photos").locator("img")).toHaveCount(1);
  await expect(loaded.getByTestId("photos-missing")).toHaveCount(0);
  await desk.close();
});
