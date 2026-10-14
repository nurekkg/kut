import { expect, test } from "../helpers/test";
import { apiLogin } from "../helpers/api";
import {
  createShift,
  openApprovals,
  openMachinistPhone,
  prepareDay,
  shiftById,
  shiftRow,
  submitShift,
} from "../helpers/field-shifts";
import { desktopContext, phoneContext, shot } from "../helpers/ui";

// Сценарий 3: прораб отклоняет лист с причиной; машинист видит причину в «Мои листы», исправляет часы и
// отправляет лист снова. Лист уходит на проверку тем же путём, что и первый раз.
test("отклонение, исправление и повторная отправка листа", async ({ browser, request }) => {
  const day = await prepareDay(request, 1);
  const machinist = await apiLogin(request, "machinist");
  const shift = await createShift(request, machinist, {
    equipment_unit_id: day.unitEast.id,
    project_id: day.east.id,
    operator_id: day.operatorId,
    hours: "5",
  });
  await submitShift(request, machinist, shift.id);

  const desktop = await desktopContext(browser);
  const foreman = await desktop.newPage();
  await openApprovals(foreman, "foreman");
  await shiftRow(foreman, day.unitEast).getByTestId("shift-reject").click();
  await foreman.getByTestId("equipment-reject-reason").fill("не совпадает с табелем");
  await foreman.getByRole("button", { name: "Отклонить лист" }).click();
  await expect(shiftRow(foreman, day.unitEast)).toHaveCount(0, { timeout: 60_000 });

  const phone = await phoneContext(browser);
  const page = await phone.newPage();
  await openMachinistPhone(page);
  await page.getByTestId("tab-mine").click();
  const mine = page.getByTestId("mine-row").filter({ hasText: day.unitEast.registry_number });
  await expect(mine.getByTestId("mine-status")).toHaveText("Отклонён");
  await expect(mine).toContainText("не совпадает с табелем");
  await shot(page, "field-06-rejected");

  await mine.getByTestId("mine-fix").click();
  await expect(page.getByTestId("shift-form")).toBeVisible({ timeout: 20_000 });
  await page.getByTestId("field-hours").fill("7");
  await page.getByTestId("form-submit").click();
  await expect(page.getByTestId("notice")).toContainText("Лист отправлен", { timeout: 60_000 });

  const after = await shiftById(request, machinist, shift.id);
  expect(after.status).toBe("submitted");
  expect(Number(after.hours)).toBe(7);
});
