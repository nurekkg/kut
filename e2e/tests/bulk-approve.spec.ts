import { type APIRequestContext } from "@playwright/test";
import { expect, test } from "../helpers/test";
import { randomUUID } from "node:crypto";

import { apiLogin, createPlan, factsOfNode, readiness, type Session } from "../helpers/api";
import { today } from "../helpers/env";
import { desktopContext, shot, signIn, watchErrors } from "../helpers/ui";

async function send(
  request: APIRequestContext,
  session: Session,
  path: string,
  data: unknown,
): Promise<void> {
  const response = await request.post(path, {
    headers: { Authorization: `Bearer ${session.token}`, "Idempotency-Key": randomUUID() },
    data,
  });
  expect(response.ok(), `${path}: ${await response.text()}`).toBeTruthy();
}

// Массовое подтверждение: три записи мастера одним действием; процент узла считается по сумме.
test("прораб подтверждает несколько записей одним действием", async ({ browser, request }) => {
  const foreman = await apiLogin(request, "foreman");
  const master = await apiLogin(request, "master");
  const plan = await createPlan(request, foreman);
  const base = `/api/v1/projects/${plan.projectId}/work-actuals`;
  for (const volume of ["10", "20", "30"]) {
    await send(request, master, base, {
      node_id: plan.nodeId,
      work_id: plan.workId,
      volume,
      work_date: today(),
      submit: true,
    });
  }
  const queued = await factsOfNode(request, foreman, plan.projectId, plan.nodeId);
  expect(queued.map((fact) => fact.status)).toEqual(["submitted", "submitted", "submitted"]);

  const desk = await desktopContext(browser);
  const page = await desk.newPage();
  const errors = watchErrors(page);
  await signIn(page, "foreman");
  await page.goto("/approvals");
  const cards = page.getByTestId("approval").filter({ hasText: plan.nodeName });
  await expect(cards).toHaveCount(3);
  // Выбираем только свои три записи: остальные из очереди (демо-данные) не трогаем.
  for (let index = 0; index < 3; index += 1) {
    await cards.nth(index).getByTestId("approval-select").check();
  }
  await expect(page.getByTestId("approve-selected")).toContainText("(3)");
  await shot(page, "bulk-1-selected");
  await page.getByTestId("approve-selected").click();
  await expect(page.getByTestId("batch-result")).toContainText("Подтверждено записей: 3");
  await expect(cards).toHaveCount(0);
  await shot(page, "bulk-2-done");

  const facts = await factsOfNode(request, foreman, plan.projectId, plan.nodeId);
  expect(facts.map((fact) => fact.status)).toEqual(["approved", "approved", "approved"]);
  expect((await readiness(request, foreman, plan.projectId)).node(plan.nodeId)).toBe(60);
  expect(errors).toEqual([]);
  await desk.close();
});
