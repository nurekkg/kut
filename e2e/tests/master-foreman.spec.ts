import { expect, test } from "../helpers/test";

import { apiLogin, createPlan, factsOfNode, get, readiness } from "../helpers/api";
import { PHOTO } from "../helpers/env";
import {
  desktopContext,
  enterFact,
  expectNoHorizontalScroll,
  expectTapTarget,
  phoneContext,
  shot,
  signIn,
  watchErrors,
} from "../helpers/ui";

// Сценарий 1: мастер на телефоне (375 px) вносит факт с фото и геопозицией, прораб подтверждает,
// процент узла и объекта растёт, результат виден в карточке узла.
test("мастер вносит факт, прораб подтверждает, процент растёт и виден в карточке", async ({
  browser,
  request,
}) => {
  const foreman = await apiLogin(request, "foreman");
  const plan = await createPlan(request, foreman);
  const before = await readiness(request, foreman, plan.projectId);
  expect(before.node(plan.nodeId)).toBe(0);

  // --- мастер: вся цепочка на телефоне ---
  const phone = await phoneContext(browser);
  const master = await phone.newPage();
  const masterErrors = watchErrors(master);
  await signIn(master, "master");
  await master.goto("/m");
  await expect(master.getByTestId("wizard")).toHaveAttribute("data-step", /node|project/);
  await shot(master, "master-1-start");
  await expectTapTarget(master, "tab-new");
  await expectNoHorizontalScroll(master);

  await enterFact(master, {
    nodeName: plan.nodeName,
    workName: plan.workName,
    volume: "40",
    photo: PHOTO,
    screens: "master",
  });
  await expect(master.getByTestId("geo-card")).toContainText("±12");
  await expectTapTarget(master, "send");
  await expectNoHorizontalScroll(master);
  await master.getByTestId("send").click();
  await expect(master.getByTestId("sent")).toHaveAttribute("data-state", "done", {
    timeout: 90_000,
  });
  await shot(master, "master-6-sent");

  // Запись у сервера: на проверке, с фото и геометкой.
  const [fact] = await factsOfNode(request, foreman, plan.projectId, plan.nodeId);
  expect(fact.status).toBe("submitted");
  expect(fact.photo_count).toBe(1);

  // --- прораб: очередь, миниатюры, объём против плана, место ---
  const desk = await desktopContext(browser);
  const boss = await desk.newPage();
  const bossErrors = watchErrors(boss);
  await signIn(boss, "foreman");
  await boss.goto("/approvals");
  const card = boss.getByTestId("approval").filter({ hasText: plan.nodeName });
  await expect(card).toHaveCount(1);
  await expect(card.getByTestId("approval-volume")).toContainText("40");
  await expect(card.getByTestId("approval-plan")).toContainText("План 100");
  await expect(card.getByTestId("approval-photos").locator("img")).toHaveCount(1);
  await expect(card.getByTestId("photos-missing")).toHaveCount(0); // снимков столько, сколько заявлено
  await expect(card.getByRole("link", { name: /карте/ })).toContainText("42.8746");
  await expect(boss.getByTestId("approvals-count").first()).toBeVisible();
  await shot(boss, "foreman-1-queue");

  await card.getByTestId("approve").click();
  await expect(boss.getByTestId("approval").filter({ hasText: plan.nodeName })).toHaveCount(0);

  // --- итог: процент узла и объекта вырос ---
  const after = await readiness(request, foreman, plan.projectId);
  expect(after.node(plan.nodeId)).toBe(40);
  expect(after.project).toBeGreaterThan(before.project);

  // Видно в карточке узла: процент обзора, вкладки «Этапы работ» и «Фото».
  await boss.goto(`/projects/${plan.projectId}?node=${plan.nodeId}`);
  await expect(boss.getByTestId("node-readiness-percent").first()).toHaveText("40%");
  await shot(boss, "foreman-2-node-overview");
  await boss.getByRole("tab", { name: "Этапы работ" }).click();
  await expect(boss.getByTestId("schedule-row")).toHaveCount(1);
  await expect(boss.getByTestId("schedule-row")).toContainText("40%");
  await shot(boss, "foreman-3-node-works");
  await boss.getByRole("tab", { name: "Сроки" }).click();
  await expect(boss.getByTestId("node-readiness-percent").first()).toHaveText("40%");
  await shot(boss, "foreman-4-node-dates");
  await boss.getByRole("tab", { name: "Фото" }).click();
  await expect(boss.getByTestId("node-media-grid").locator("li")).toHaveCount(1);
  await shot(boss, "foreman-5-node-photos");
  await boss.getByTestId("node-media-grid").locator("button").first().click();
  await expect(boss.getByRole("dialog")).toContainText("Мастер Восток (dev)");
  await shot(boss, "foreman-6-photo-viewer");

  // Фото на сервере сжато на клиенте: длинная сторона не больше 2560 px.
  const [approved] = await factsOfNode(request, foreman, plan.projectId, plan.nodeId);
  const photos = await get<{ id: string; size_bytes: number }[]>(
    request,
    foreman,
    `/api/v1/projects/${plan.projectId}/photos`,
    { work_actual_id: approved.id },
  );
  expect(photos).toHaveLength(1);
  const link = await get<{ url: string }>(
    request,
    foreman,
    `/api/v1/projects/${plan.projectId}/photos/${photos[0].id}/download-url`,
  );
  const bytes = Buffer.from(await (await request.get(link.url)).body());
  const { width, height } = jpegSize(bytes);
  expect(Math.max(width, height)).toBeLessThanOrEqual(2560);
  expect(Math.max(width, height)).toBeGreaterThan(2000);

  // Ни ошибок страницы, ни ошибок консоли (в том числе недостающих текстов) за весь сценарий.
  expect(masterErrors).toEqual([]);
  expect(bossErrors).toEqual([]);

  await phone.close();
  await desk.close();
});

// Размер JPEG из заголовка (маркер SOF): без внешних библиотек.
function jpegSize(bytes: Buffer): { width: number; height: number } {
  let offset = 2;
  while (offset < bytes.length) {
    const marker = bytes[offset + 1];
    const length = bytes.readUInt16BE(offset + 2);
    if (marker >= 0xc0 && marker <= 0xcf && ![0xc4, 0xc8, 0xcc].includes(marker)) {
      return { height: bytes.readUInt16BE(offset + 5), width: bytes.readUInt16BE(offset + 7) };
    }
    offset += 2 + length;
  }
  throw new Error("в файле нет заголовка JPEG");
}
