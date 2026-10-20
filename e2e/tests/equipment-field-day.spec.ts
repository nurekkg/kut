import { expect, test } from "../helpers/test";

import { apiLogin, get } from "../helpers/api";
import {
  enterShift,
  fuelOf,
  newPageFor,
  openApprovals,
  openMachinistPhone,
  prepareDay,
  shiftRow,
} from "../helpers/field-shifts";
import { phoneContext, shot, signIn, watchErrors } from "../helpers/ui";

// Сценарий 1 (ADR-014 часть 3, брифинг SE-2b): машинист на телефоне 375 px работает офлайн, на одном
// дне два объекта, одна заправка с чеком. После связи очередь уходит на сервер; прораб каждого объекта
// подтверждает свой лист; начальник отдела видит строки в журнале.
// Сценарий ведёт несколько ролей и ждёт связи и подтверждений: лимит времени длиннее общего.
test.setTimeout(480_000);

test("машинист на 375 px офлайн: два объекта в день, заправка с чеком, два прораба подтверждают, журнал", async ({
  browser,
  request,
}) => {
  const day = await prepareDay(request, 2);
  const phone = await phoneContext(browser);
  const page = await phone.newPage();
  const errors = watchErrors(page);
  // «Сегодня» телефона - дата сценария, а не часы в момент отрисовки: сценарий не зависит от полуночи по Бишкеку.
  await page.clock.setFixedTime(new Date(`${day.date}T12:00:00+06:00`));

  await openMachinistPhone(page);
  await shot(page, "field-01-today");
  await phone.setOffline(true);

  await enterShift(page, day.unitEast, { hours: "8", liters: "40", amount: "3200", photo: true });
  await expect(page.getByTestId("notice")).toContainText("Сохранено на телефоне");
  await enterShift(page, day.unitSecond, { hours: "6" });
  await expect(page.getByTestId("notice")).toContainText("Сохранено на телефоне");
  await page.getByTestId("tab-queue").click();
  await expect(page.getByTestId("queue-entry")).toHaveCount(2);
  await shot(page, "field-02-queue-offline");

  const online = Date.now();
  await phone.setOffline(false);
  await expect(page.getByTestId("queue-entry")).toHaveCount(0, { timeout: 150_000 });
  console.log("QUEUE_SYNC_MS", Date.now() - online);
  await page.getByTestId("tab-mine").click();
  const mineEast = page.getByTestId("mine-row").filter({ hasText: day.unitEast.registry_number });
  await expect(mineEast.getByTestId("mine-status")).toHaveText("Отправлен");
  await shot(page, "field-03-mine-sent");

  // Заправка с чеком легла на сервер: строка ГСМ в листе, чек загружен и виден загрузившему.
  const machinist = await apiLogin(request, "machinist");
  const east = (await get<{ id: string; shift_id: string; liters: string | null; has_receipt: boolean }[]>(
    request,
    machinist,
    "/api/v1/equipment/fuel",
    { project_id: day.east.id },
  )).filter((row) => row.liters === "40.00");
  expect(east.length).toBeGreaterThan(0);
  const fuel = east[0];
  expect(fuel.has_receipt).toBe(true);
  const link = await request.get(`/api/v1/equipment/shifts/${fuel.shift_id}/fuel/${fuel.id}/receipt`, {
    headers: { Authorization: `Bearer ${machinist.token}` },
  });
  expect(link.status()).toBe(200);
  expect(await fuelOf(request, machinist, fuel.shift_id)).toHaveLength(1);

  // Каждый прораб подтверждает свой лист: прораб объекта «Восток» и прораб «Восток-2».
  const foremanPage = await newPageFor(browser);
  await openApprovals(foremanPage, "foreman");
  await shiftRow(foremanPage, day.unitEast).getByTestId("shift-confirm").click();
  await expect(shiftRow(foremanPage, day.unitEast)).toHaveCount(0, { timeout: 60_000 });
  await shot(foremanPage, "approvals-equipment-east-confirmed");

  const secondForeman = await newPageFor(browser);
  await openApprovals(secondForeman, "foreman2");
  await shiftRow(secondForeman, day.unitSecond).getByTestId("shift-confirm").click();
  await expect(shiftRow(secondForeman, day.unitSecond)).toHaveCount(0, { timeout: 60_000 });

  // Начальник отдела видит строки в журнале: ГСМ с литрами и суммой, аренда или своя техника.
  const head = await newPageFor(browser);
  await signIn(head, "equipment_head");
  await head.goto("/equipment/journal");
  await expect(head.getByTestId("journal")).toBeVisible({ timeout: 40_000 });
  await head.getByTestId("journal-project").selectOption({ label: day.east.name });
  await expect(head.getByTestId("journal-row").filter({ hasText: "ГСМ" }).first()).toBeVisible({
    timeout: 40_000,
  });
  await expect(head.getByTestId("journal-row").filter({ hasText: day.unitEast.registry_number })).not.toHaveCount(0);
  await shot(head, "journal-after-confirm");

  // Ошибок приложения нет (офлайн-обрывы в консоли - ожидаемые).
  const real = errors.filter((message) => !message.includes("ERR_INTERNET_DISCONNECTED"));
  expect(real).toEqual([]);
});
