import { apiLogin } from "../helpers/api";
import { EAST_NAME, projectNamed, signInWithCode, TOTP_SECRET, newUnit, typeByCode } from "../helpers/equipment";
import { expect, test } from "../helpers/test";
import { desktopContext, shot, signIn, watchErrors } from "../helpers/ui";

// Сценарий 5 из брифа: начальник отдела предлагает норматив, финдир утверждает; начальник утвердить
// свой норматив не может - ни кнопки в интерфейсе, ни вызов API (нет права approve:rate).
test("норматив: начальник предлагает, финдир утверждает, начальник утвердить не может", async ({
  browser,
  request,
}) => {
  test.skip(TOTP_SECRET === "", "нужен DEV_SEED_TOTP_SECRET: вход финдиректора проходит с кодом");
  const head = await apiLogin(request, "equipment_head");
  const project = await projectNamed(request, await apiLogin(request, "foreman"), EAST_NAME);
  const type = await typeByCode(request, head, "SAM");
  const unit = await newUnit(request, head, project, type);

  const headContext = await desktopContext(browser);
  const boss = await headContext.newPage();
  const bossErrors = watchErrors(boss);
  await signIn(boss, "equipment_head");
  await boss.goto(`/equipment/${unit.id}`);
  await boss.getByTestId("propose-rate").click();
  await boss.locator("#rate-unit").selectOption("hour");
  await boss.locator("#rate-amount").fill("1500");
  await boss.getByTestId("form-submit").click();
  await expect(boss.getByRole("dialog")).toBeHidden({ timeout: 20_000 });
  const row = boss.getByTestId("rate-list").locator('li[data-approved="false"]').filter({ hasText: "1500" }).first();
  await expect(row).toContainText("Ждёт утверждения", { timeout: 20_000 });
  await expect(row.getByTestId("approve-rate")).toHaveCount(0);
  await shot(boss, "equipment-rate-proposed");

  // API: начальник без права approve:rate получает отказ, даже зная идентификатор норматива.
  const proposed = await request.get("/api/v1/equipment/own-rates", {
    headers: { Authorization: `Bearer ${head.token}` },
    params: { legal_entity_id: project.legal_entity_id, type_id: type.id },
  });
  const rateId = ((await proposed.json()) as { id: string; approved_by: string | null }[]).find(
    (item) => item.approved_by === null,
  )?.id;
  expect(rateId).toBeTruthy();
  const attempt = await request.post(`/api/v1/equipment/own-rates/${rateId}/approve`, {
    headers: { Authorization: `Bearer ${head.token}` },
  });
  expect(attempt.status()).toBe(403);

  const financeContext = await desktopContext(browser);
  const finance = await financeContext.newPage();
  const financeErrors = watchErrors(finance);
  await signInWithCode(finance, "finance", TOTP_SECRET);
  await finance.goto(`/equipment/${unit.id}`);
  const pending = finance.getByTestId("rate-list").locator('li[data-approved="false"]').filter({ hasText: "1500" }).first();
  await expect(pending).toBeVisible({ timeout: 30_000 });
  await pending.getByTestId("approve-rate").click();
  await expect(finance.getByTestId("rate-list").locator('li[data-approved="true"]').filter({ hasText: "1500" }).first()).toContainText(
    "Утверждён",
    { timeout: 20_000 },
  );
  await shot(finance, "equipment-rate-approved");

  expect(bossErrors).toEqual([]);
  expect(financeErrors).toEqual([]);
});
