import { expect, test } from "../helpers/test";

import { apiLogin, get, post } from "../helpers/api";
import {
  createShift,
  newPageFor,
  openApprovals,
  prepareDay,
  SECOND,
  shiftRow,
  submitShift,
} from "../helpers/field-shifts";
import { projectNamed, unique } from "../helpers/equipment";
import { shot } from "../helpers/ui";

// Сценарий 4: прораб назначает делегата на период с сегодняшнего дня; делегат видит баннер и подтверждает
// лист объекта. Прораб-оператор не подтверждает свой лист: право подтверждения у другого человека.
// Сценарий ведёт несколько ролей и ждёт связи и подтверждений: лимит времени длиннее общего.
test.setTimeout(480_000);

test("делегат подтверждает в период; прораб-оператор не подтверждает свой лист", async ({ browser, request }) => {
  const day = await prepareDay(request, 1);
  const foreman = await apiLogin(request, "foreman");
  const shift = await createShift(request, foreman, {
    equipment_unit_id: day.unitEast.id,
    project_id: day.east.id,
    operator_id: day.operatorId,
    hours: "4",
  });
  await submitShift(request, foreman, shift.id);

  const foremanPage = await newPageFor(browser);
  await openApprovals(foremanPage, "foreman");
  await foremanPage.getByTestId("delegate-add").click();
  await foremanPage.getByTestId("delegate-user").selectOption({ label: "Мастер Восток (dev)" });
  const reason = unique("отпуск");
  await foremanPage.getByTestId("delegate-reason").fill(reason);
  await foremanPage.getByRole("button", { name: "Назначить" }).click();
  await expect(foremanPage.getByTestId("delegate-row").filter({ hasText: reason })).toBeVisible({
    timeout: 40_000,
  });

  const masterPage = await newPageFor(browser);
  await openApprovals(masterPage, "master");
  // У мастера могут быть активные делегации с прошлых прогонов: нужен хотя бы один баннер.
  await expect(masterPage.getByTestId("delegate-banner").first()).toContainText("Вы делегат на этот объект до");
  await shiftRow(masterPage, day.unitEast).getByTestId("shift-confirm").click();
  await expect(shiftRow(masterPage, day.unitEast)).toHaveCount(0, { timeout: 60_000 });
  await shot(masterPage, "approvals-delegate-confirmed");

  // Прораб-оператор: учётка прораба Восток-2 записана оператором, лист на этом объекте ведёт он сам.
  const head = await apiLogin(request, "equipment_head");
  const second = await projectNamed(request, head, SECOND);
  const secondForeman = await apiLogin(request, "foreman2");
  // Учётка могла быть записана оператором в прошлом прогоне: берём существующую запись (иначе 409).
  const operatorUserId = await userIdOf(secondForeman.token);
  const operators = await get<{ id: string; user_id: string | null }[]>(request, head, "/api/v1/equipment/operators");
  const operator = operators.find((row) => row.user_id === operatorUserId) ??
    (await post<{ id: string }>(request, head, "/api/v1/equipment/operators", {
      legal_entity_id: day.east.legal_entity_id,
      full_name: unique("Прораб-оператор"),
      employment: "staff",
      user_id: operatorUserId,
    }));
  const own = await createShift(request, secondForeman, {
    equipment_unit_id: day.unitSecond.id,
    project_id: second.id,
    operator_id: operator.id,
    hours: "3",
  });
  await submitShift(request, secondForeman, own.id);
  const ownPage = await newPageFor(browser);
  await openApprovals(ownPage, "foreman2");
  // S5a, решение F1: в очереди согласований только подтверждаемые листы. Свой лист прораб-оператор там не видит,
  // поэтому кнопки подтверждения нет; отказ сервера проверяем запросом, без экрана.
  await expect(ownPage.getByTestId("equipment-approvals")).toBeVisible({ timeout: 40_000 });
  await expect(shiftRow(ownPage, day.unitSecond)).toHaveCount(0);
  await shot(ownPage, "approvals-own-sheet-hidden");
  const refused = await request.post(`/api/v1/equipment/shifts/${own.id}/confirm`, {
    headers: { Authorization: `Bearer ${secondForeman.token}` },
  });
  expect(refused.ok()).toBeFalsy();
});

// Идентификатор пользователя из токена входа (поле sub).
async function userIdOf(token: string): Promise<string> {
  const payload = (token.split(".")[1] ?? "").replace(/-/g, "+").replace(/_/g, "/");
  return (JSON.parse(Buffer.from(payload, "base64").toString("utf8")) as { sub: string }).sub;
}
