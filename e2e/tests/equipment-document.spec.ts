import { apiLogin } from "../helpers/api";
import { EAST_NAME, futureDay, newUnit, projectNamed, typeByCode } from "../helpers/equipment";
import { createRequest, pick, submitRequest } from "../helpers/equipment-flow";
import { expect, test } from "../helpers/test";
import { desktopContext, signIn, watchErrors } from "../helpers/ui";


// Сценарий 2 из брифа: страховка истекает внутри периода - назначение блокируется; новую страховку вносит
// начальник отдела (PO 2026-10-18: механик вносит только технический осмотр) - назначение проходит.
test("истёкшая страховка блокирует назначение, новый документ механика снимает блокировку", async ({
  browser,
  request,
}) => {
  const head = await apiLogin(request, "equipment_head");
  const project = await projectNamed(request, await apiLogin(request, "foreman"), EAST_NAME);
  const type = await typeByCode(request, head, "SAM");
  const offset = 300 + Math.floor(Math.random() * 1500);
  const from = futureDay(offset);
  const until = futureDay(offset + 5);
  const unit = await newUnit(request, head, project, type, { insuranceUntil: futureDay(offset + 2) });

  const foremanContext = await desktopContext(browser);
  const foreman = await foremanContext.newPage();
  const foremanErrors = watchErrors(foreman);
  await signIn(foreman, "foreman");
  await createRequest(foreman, { project: project.id, type: type.id, from, until, hours: "8" });
  const requestId = await submitRequest(foreman, from);

  const headContext = await desktopContext(browser);
  const boss = await headContext.newPage();
  const headErrors = watchErrors(boss, { allowAssignmentRefusals: true });
  await signIn(boss, "equipment_head");
  await boss.goto("/equipment/requests");
  await boss.locator(`[data-request-id="${requestId}"]`).getByTestId("assign-request").click();
  await pick(boss, "#assign-unit", unit.id);
  await expect(boss.getByTestId("assign-reason")).toContainText("Документ просрочен", { timeout: 20_000 });
  await expect(boss.getByTestId("form-submit")).toBeDisabled();
  await boss.getByRole("button", { name: "Отмена" }).click();
  await expect(boss.getByRole("dialog")).toBeHidden({ timeout: 20_000 });

  // Механик в диалоге строки срока видит только технический осмотр - страховку ему не предлагают.
  const mechanicContext = await desktopContext(browser);
  const mechanic = await mechanicContext.newPage();
  const mechanicErrors = watchErrors(mechanic);
  await signIn(mechanic, "mechanic");
  await mechanic.goto(`/equipment/${unit.id}`);
  await mechanic.getByTestId("add-document").click();
  await expect(mechanic.locator("#document-kind option[value='technical_inspection']")).toHaveCount(1);
  await expect(mechanic.locator("#document-kind option[value='insurance']")).toHaveCount(0);
  await mechanic.keyboard.press("Escape");
  await expect(mechanic.getByRole("dialog")).toBeHidden({ timeout: 20_000 });

  await boss.goto(`/equipment/${unit.id}`);
  await boss.getByTestId("add-document").click();
  await boss.locator("#document-kind").selectOption("insurance");
  await boss.locator("#document-number").fill("СТ-E2E-NEW");
  await boss.locator("#document-valid").fill(futureDay(offset + 90));
  await boss.getByTestId("form-submit").click();
  await expect(boss.getByRole("dialog")).toBeHidden({ timeout: 20_000 });
  await expect(boss.getByTestId("document-list")).toContainText("СТ-E2E-NEW", { timeout: 20_000 });

  await boss.goto("/equipment/requests");
  await boss.locator(`[data-request-id="${requestId}"]`).getByTestId("assign-request").click();
  await pick(boss, "#assign-unit", unit.id);
  await expect(boss.getByTestId("assign-fits")).toBeVisible({ timeout: 20_000 });
  await boss.getByTestId("form-submit").click();
  await expect(boss.getByRole("dialog")).toBeHidden({ timeout: 20_000 });
  await expect(boss.locator(`[data-request-id="${requestId}"]`)).toHaveAttribute("data-status", "assigned", {
    timeout: 20_000,
  });

  expect(foremanErrors).toEqual([]);
  expect(headErrors).toEqual([]);
  expect(mechanicErrors).toEqual([]);
});
