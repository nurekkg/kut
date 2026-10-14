import { expect, test } from "../helpers/test";
import { enterShift, openMachinistPhone, prepareDay } from "../helpers/field-shifts";
import { phoneContext, shot } from "../helpers/ui";

// Сценарий 2: суточный лимит часов единицы (24 ч). 14 часов уходят, второй лист на 12 часов получает
// понятную ошибку прямо в форме, а не общий отказ: причина - в тексте, который видит машинист.
test("лимит часов 14 + 12 - понятная ошибка в форме", async ({ browser, request }) => {
  const day = await prepareDay(request, 1);
  const phone = await phoneContext(browser);
  const page = await phone.newPage();
  await openMachinistPhone(page);

  await enterShift(page, day.unitEast, { hours: "14" });
  await expect(page.getByTestId("notice")).toContainText("Лист отправлен");

  const started = Date.now();
  await enterShift(page, day.unitEast, { hours: "12" });
  const failure = page.getByTestId("form-failure");
  await expect(failure).toContainText("Превышен суточный лимит", { timeout: 90_000 });
  console.log("LIMIT_ERROR_SHOWN_MS", Date.now() - started);
  await expect(page.getByTestId("shift-form")).toBeVisible();
  await shot(page, "field-05-limit-error");
});
