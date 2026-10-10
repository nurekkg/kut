import { expect, test } from "../helpers/test";

import { apiLogin, createPlan, factsOfNode, readiness } from "../helpers/api";
import { PHOTO } from "../helpers/env";
import { desktopContext, enterFact, phoneContext, shot, signIn } from "../helpers/ui";

// Сценарий 2: прораб отклоняет запись с причиной, мастер видит причину, исправляет и отправляет
// заново, прораб подтверждает исправленную запись.
test("отклонение, исправление, повторная отправка и подтверждение", async ({ browser, request }) => {
  const foreman = await apiLogin(request, "foreman");
  const plan = await createPlan(request, foreman);

  const phone = await phoneContext(browser);
  const master = await phone.newPage();
  await signIn(master, "master");
  await enterFact(master, {
    nodeName: plan.nodeName,
    workName: plan.workName,
    volume: "60",
    photo: PHOTO,
  });
  await master.getByTestId("send").click();
  await expect(master.getByTestId("sent")).toHaveAttribute("data-state", "done", {
    timeout: 90_000,
  });

  // --- прораб отклоняет с причиной ---
  const desk = await desktopContext(browser);
  const boss = await desk.newPage();
  await signIn(boss, "foreman");
  await boss.goto("/approvals");
  const card = boss.getByTestId("approval").filter({ hasText: plan.nodeName });
  await expect(card).toHaveCount(1);
  await card.getByTestId("reject").click();
  const reason = "Объём больше проектного: уточните обмер";
  await boss.getByTestId("reject-reason-input").fill(reason);
  await shot(boss, "reject-1-dialog");
  await boss.getByRole("button", { name: "Отклонить" }).last().click();
  await expect(boss.getByTestId("approval").filter({ hasText: plan.nodeName })).toHaveCount(0);

  // --- мастер видит статус и причину, исправляет и отправляет заново ---
  await master.goto("/m");
  await master.getByTestId("tab-records").click();
  const record = master.getByTestId("record").filter({ hasText: plan.nodeName });
  await expect(record).toHaveAttribute("data-status", "rejected");
  await expect(record.getByTestId("record-status")).toHaveText("Отклонена");
  await expect(record.getByTestId("reject-reason")).toHaveText(reason);
  await shot(master, "reject-2-records");

  await record.getByTestId("fix").click();
  await master.getByTestId("fix-volume").fill("30");
  await master.getByTestId("photo-input").setInputFiles(PHOTO);
  await expect(master.getByTestId("photo-list").locator("img")).toHaveCount(1);
  await shot(master, "reject-3-fix");
  await master.getByTestId("resubmit").click();
  await expect(master.getByTestId("record").filter({ hasText: plan.nodeName })).toHaveAttribute(
    "data-status",
    "submitted",
    { timeout: 90_000 },
  );
  await shot(master, "reject-4-resubmitted");

  const [fact] = await factsOfNode(request, foreman, plan.projectId, plan.nodeId);
  expect(fact.status).toBe("submitted");
  expect(Number(fact.volume)).toBe(30);
  expect(fact.photo_count).toBe(2);

  // --- прораб подтверждает исправленное: 30 из 100 ---
  await boss.goto("/approvals");
  const again = boss.getByTestId("approval").filter({ hasText: plan.nodeName });
  await expect(again).toHaveCount(1);
  await expect(again.getByTestId("approval-volume")).toContainText("30");
  await again.getByTestId("approve").click();
  await expect(boss.getByTestId("approval").filter({ hasText: plan.nodeName })).toHaveCount(0);
  expect((await readiness(request, foreman, plan.projectId)).node(plan.nodeId)).toBe(30);

  await phone.close();
  await desk.close();
});
