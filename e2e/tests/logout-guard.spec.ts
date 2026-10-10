import type { Browser, APIRequestContext } from "@playwright/test";

import { expect, test } from "../helpers/test";

import { apiLogin, createPlan, factsOfNode } from "../helpers/api";
import { PHOTO } from "../helpers/env";
import {
  cloneOutboxEntry,
  enterFact,
  outboxCount,
  phoneContext,
  shot,
  signIn,
  signInHere,
} from "../helpers/ui";

// Очередь отправки мастера на общем телефоне (решения PO по S3b): при выходе с неотправленным -
// диалог «отправить сейчас / удалить» и очистка; вход другого человека стирает чужую очередь;
// записи старше 7 суток стираются при запуске. Неотправленная запись получается так: запрос на
// создание факта обрывается (как при пропавшей связи), запись остаётся в очереди на устройстве.
async function unsentAsMaster(browser: Browser, request: APIRequestContext) {
  const foreman = await apiLogin(request, "foreman");
  const plan = await createPlan(request, foreman);
  const phone = await phoneContext(browser);
  const page = await phone.newPage();
  await signIn(page, "master");

  const link = { down: true };
  await page.route("**/api/v1/projects/*/work-actuals", (route) =>
    route.request().method() === "POST" && link.down
      ? route.abort("connectionrefused")
      : route.continue(),
  );
  await enterFact(page, {
    nodeName: plan.nodeName,
    workName: plan.workName,
    volume: "12",
    photo: PHOTO,
  });
  await page.getByTestId("send").click();
  await expect.poll(() => outboxCount(page), { timeout: 30_000 }).toBe(1);
  return { phone, page, plan, foreman, link };
}

test("выход с неотправленным: предупреждение, «Остаться», «Удалить и выйти», очередь пуста", async ({
  browser,
  request,
}) => {
  const { phone, page } = await unsentAsMaster(browser, request);

  await page.getByRole("button", { name: "Выйти", exact: true }).click();
  const dialog = page.getByTestId("logout-dialog");
  await expect(dialog).toBeVisible();
  await expect(page.getByRole("dialog")).toContainText("Не отправлена 1 запись");
  await shot(page, "logout-1-warning");

  // «Остаться»: диалог закрыт, сеанс и запись на месте.
  await page.getByTestId("logout-stay").click();
  await expect(dialog).toBeHidden();
  await expect(page).toHaveURL(/\/m$/);
  expect(await outboxCount(page)).toBe(1);

  // «Удалить и выйти»: очередь стёрта, сеанс закрыт.
  await page.getByRole("button", { name: "Выйти", exact: true }).click();
  await page.getByTestId("logout-discard").click();
  await page.waitForURL(/\/login/);
  expect(await outboxCount(page)).toBe(0);

  // Прораб входит на том же устройстве: очереди мастера нет.
  await signInHere(page, "foreman");
  await page.goto("/m");
  await expect(page.getByTestId("wizard")).toBeVisible();
  await expect(page.getByTestId("outbox")).toHaveCount(0);
  expect(await outboxCount(page)).toBe(0);
  await phone.close();
});

test("«Отправить сейчас»: без связи остаётся предупреждение, со связью запись уходит и выход завершается", async ({
  browser,
  request,
}) => {
  const { phone, page, plan, foreman, link } = await unsentAsMaster(browser, request);

  await page.getByRole("button", { name: "Выйти", exact: true }).click();
  await page.getByTestId("logout-send").click();
  await expect(page.getByRole("dialog")).toContainText("Отправить не удалось, осталось: 1", {
    timeout: 60_000,
  });
  expect(await outboxCount(page)).toBe(1);
  await shot(page, "logout-2-not-sent");

  link.down = false; // связь вернулась
  await page.getByTestId("logout-send").click();
  await page.waitForURL(/\/login/, { timeout: 180_000 });
  expect(await outboxCount(page)).toBe(0);

  const [fact] = await factsOfNode(request, foreman, plan.projectId, plan.nodeId);
  expect(fact.status).toBe("submitted");
  expect(fact.photo_count).toBe(1);
  await phone.close();
});

test("вход другого человека стирает чужую очередь и ничего не отправляет от его имени", async ({
  browser,
  request,
}) => {
  const { phone, page, plan, foreman } = await unsentAsMaster(browser, request);

  // Сеанс мастера потерян без выхода (истёк, куки стёрты): запись осталась на устройстве.
  await phone.clearCookies();
  await page.goto("/login");
  expect(await outboxCount(page)).toBe(1);

  const created: string[] = [];
  page.on("request", (call) => {
    if (call.method() === "POST" && /\/work-actuals$/.test(new URL(call.url()).pathname)) {
      created.push(call.url());
    }
  });
  await signInHere(page, "foreman");
  await expect.poll(() => outboxCount(page), { timeout: 30_000 }).toBe(0);
  await page.goto("/m");
  await expect(page.getByTestId("wizard")).toBeVisible();
  await expect(page.getByTestId("outbox")).toHaveCount(0);
  await shot(page, "logout-3-foreign-queue-gone");

  expect(created, "запись мастера ушла на сервер под входом прораба").toEqual([]);
  expect(await factsOfNode(request, foreman, plan.projectId, plan.nodeId)).toEqual([]);
  await phone.close();
});

test("запись старше 7 суток стирается при запуске, свежая остаётся", async ({ browser, request }) => {
  const { phone, page } = await unsentAsMaster(browser, request);

  await cloneOutboxEntry(page, [8, 6]); // восьмисуточная и шестисуточная копии
  expect(await outboxCount(page)).toBe(3);

  await page.goto("/m"); // запуск приложения: очередь чистится
  await expect.poll(() => outboxCount(page), { timeout: 30_000 }).toBe(2);
  await phone.close();
});
