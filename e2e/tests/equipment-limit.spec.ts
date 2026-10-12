import { apiLogin } from "../helpers/api";
import { EAST_NAME, futureDay, newUnit, projectNamed, typeByCode } from "../helpers/equipment";
import { expect, test } from "../helpers/test";
import { createRequest, pick, submitRequest } from "../helpers/equipment-flow";
import { desktopContext, shot, signIn, watchErrors } from "../helpers/ui";

// Сценарий 1 из брифа: прораб подаёт заявку, начальник назначает, вторая заявка на ту же единицу на
// тот же день (16 + 10 ч) получает понятный отказ по суточному лимиту.
test("суточный лимит: вторая заявка на ту же единицу в тот же день получает понятный отказ", async ({
  browser,
  request,
}) => {
  const head = await apiLogin(request, "equipment_head");
  const project = await projectNamed(request, await apiLogin(request, "foreman"), EAST_NAME);
  const type = await typeByCode(request, head, "SAM");
  const unit = await newUnit(request, head, project, type);
  const day = futureDay(300 + Math.floor(Math.random() * 1500));

  const foremanContext = await desktopContext(browser);
  const foreman = await foremanContext.newPage();
  const foremanErrors = watchErrors(foreman);
  await signIn(foreman, "foreman");
  await createRequest(foreman, { project: project.id, type: type.id, from: day, until: day, hours: "16" });
  const first = await submitRequest(foreman, day);

  const headContext = await desktopContext(browser);
  const boss = await headContext.newPage();
  const headErrors = watchErrors(boss);
  await signIn(boss, "equipment_head");
  await boss.goto("/equipment/requests");
  await boss.locator(`[data-request-id="${first}"]`).getByTestId("assign-request").click();
  await pick(boss, "#assign-unit", unit.id);
  await expect(boss.getByTestId("assign-fits")).toBeVisible({ timeout: 20_000 });
  await boss.getByTestId("form-submit").click();
  await expect(boss.getByRole("dialog")).toBeHidden({ timeout: 20_000 });
  await expect(boss.locator(`[data-request-id="${first}"]`)).toHaveAttribute("data-status", "assigned", {
    timeout: 20_000,
  });

  await createRequest(foreman, { project: project.id, type: type.id, from: day, until: day, hours: "10" });
  const second = await submitRequest(foreman, day);

  await boss.goto("/equipment/requests");
  await boss.locator(`[data-request-id="${second}"]`).getByTestId("assign-request").click();
  await pick(boss, "#assign-unit", unit.id);
  await expect(boss.getByTestId("assign-reason")).toContainText("Превышен суточный лимит", {
    timeout: 20_000,
  });
  await expect(boss.getByTestId("form-submit")).toBeDisabled();
  await shot(boss, "equipment-limit-error");

  expect(foremanErrors).toEqual([]);
  expect(headErrors).toEqual([]);
});
