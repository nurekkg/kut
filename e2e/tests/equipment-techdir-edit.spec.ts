import { expect, test } from "../helpers/test";

import { apiLogin, post } from "../helpers/api";
import { createShift, newPageFor, prepareDay, submitShift } from "../helpers/field-shifts";
import { shot, signIn } from "../helpers/ui";

// Сценарий 6: техдиректор правит подтверждённый лист. Старая проводка сторнируется в журнале, новая
// строка появляется с исправленными часами - так видно и исходную цифру, и правку.
// Сценарий ведёт несколько ролей и ждёт связи и подтверждений: лимит времени длиннее общего.
test.setTimeout(480_000);

test("техдир правит подтверждённый лист: сторно и новая строка в журнале", async ({ browser, request }) => {
  const day = await prepareDay(request, 1);
  const machinist = await apiLogin(request, "machinist");
  const foreman = await apiLogin(request, "foreman");
  const shift = await createShift(request, machinist, {
    equipment_unit_id: day.unitEast.id,
    project_id: day.east.id,
    operator_id: day.operatorId,
    hours: "8",
  });
  await submitShift(request, machinist, shift.id);
  await post(request, foreman, `/api/v1/equipment/shifts/${shift.id}/confirm`, undefined);

  const techdir = await newPageFor(browser);
  await signIn(techdir, "techdir");
  await techdir.goto("/approvals");
  await expect(techdir.getByTestId("equipment-approvals")).toBeVisible({ timeout: 40_000 });
  const row = techdir.getByTestId("confirmed-shift-row").filter({ hasText: day.unitEast.registry_number });
  await row.getByTestId("shift-edit").click();
  await techdir.getByLabel("Часы").fill("7");
  await techdir.getByRole("button", { name: "Сохранить правку" }).click();
  await expect(techdir.getByRole("dialog")).toHaveCount(0, { timeout: 40_000 });
  await shot(techdir, "approvals-techdir-edit");

  const head = await newPageFor(browser);
  await signIn(head, "equipment_head");
  await head.goto("/equipment/journal");
  await expect(head.getByTestId("journal")).toBeVisible({ timeout: 40_000 });
  const ours = head.getByTestId("journal-row").filter({ hasText: day.unitEast.registry_number });
  await expect(ours.filter({ has: head.getByTestId("journal-reversal") }).first()).toBeVisible({
    timeout: 40_000,
  });
  await expect(ours.filter({ hasText: "7.00" }).first()).toBeVisible();
  await shot(head, "journal-reversal");
});
