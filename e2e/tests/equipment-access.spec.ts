import { apiLogin, get, jwtSubject, post } from "../helpers/api";
import { EAST_NAME, futureDay, newUnit, projectNamed, typeByCode } from "../helpers/equipment";
import { expect, test } from "../helpers/test";
import { expectNoHorizontalScroll, phoneContext, desktopContext, shot, signIn, watchErrors } from "../helpers/ui";

type Assignment = { id: string; equipment_unit_id: string };
type Operator = { id: string; user_id: string | null };

// Сценарий 4 из брифа: прораб не видит тарифы, договоры и нормативы (в интерфейсе и на сервере);
// машинист на телефоне видит только свои назначения.
test("прораб не видит деньги, машинист на 375 px видит только свои назначения", async ({ browser, request }) => {
  const head = await apiLogin(request, "equipment_head");
  const project = await projectNamed(request, await apiLogin(request, "foreman"), EAST_NAME);
  const type = await typeByCode(request, head, "SAM");
  const unit = await newUnit(request, head, project, type);
  const machinist = await apiLogin(request, "machinist");
  const operators = await get<Operator[]>(request, head, "/api/v1/equipment/operators");
  const operator = operators.find((item) => item.user_id === jwtSubject(machinist.token));
  expect(operator, "оператор машиниста заведён в сиде").toBeTruthy();
  const offset = 300 + Math.floor(Math.random() * 1500);
  const mine = await post<Assignment>(request, head, "/api/v1/equipment/assignments", {
    equipment_unit_id: unit.id,
    project_id: project.id,
    date_from: futureDay(offset),
    date_to: futureDay(offset),
    planned_hours_per_day: "6",
    operator_id: operator?.id,
  });
  const others = await post<Assignment>(request, head, "/api/v1/equipment/assignments", {
    equipment_unit_id: unit.id,
    project_id: project.id,
    date_from: futureDay(offset + 1),
    date_to: futureDay(offset + 1),
    planned_hours_per_day: "5",
  });

  // Сервер: прораб не получает тарифы, договоры и контрагентов.
  const foreman = await apiLogin(request, "foreman");
  const rates = await request.get("/api/v1/equipment/own-rates", {
    headers: { Authorization: `Bearer ${foreman.token}` },
    params: { legal_entity_id: project.legal_entity_id, type_id: type.id },
  });
  expect(rates.status()).toBe(403);
  const contracts = await request.get("/api/v1/equipment/contracts", {
    headers: { Authorization: `Bearer ${foreman.token}` },
  });
  expect(contracts.status()).toBe(403);

  // Интерфейс прораба: в меню нет договоров и импорта, в карточке нет нормативов и договора.
  const desktop = await desktopContext(browser);
  const page = await desktop.newPage();
  const errors = watchErrors(page);
  await signIn(page, "foreman");
  await page.goto(`/equipment/${unit.id}`);
  await expect(page.getByTestId("unit-card")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("link", { name: "Договоры", exact: true })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Импорт", exact: true })).toHaveCount(0);
  await expect(page.getByText("Нормативы своей техники")).toHaveCount(0);
  await expect(page.getByText("Договор аренды", { exact: true })).toHaveCount(0);
  await expect(page.getByTestId("propose-rate")).toHaveCount(0);
  expect(errors).toEqual([]);

  // Телефон машиниста: 375 px, только его назначения, без прокрутки страницы вбок.
  const phone = await phoneContext(browser);
  const driver = await phone.newPage();
  const driverErrors = watchErrors(driver);
  await signIn(driver, "machinist");
  await driver.goto("/equipment/my");
  const list = driver.locator('[data-testid="my-assignments"] li');
  await expect(list.first()).toBeVisible({ timeout: 30_000 });
  const shown = await list.evaluateAll((items) => items.map((item) => item.getAttribute("data-assignment-id")));
  const visible = await get<Assignment[]>(request, machinist, "/api/v1/equipment/assignments", { limit: 500 });
  expect(shown).toContain(mine.id);
  expect(shown).not.toContain(others.id);
  expect(shown.length).toBe(visible.length);
  await expect(driver.getByRole("link", { name: "Техника", exact: true })).toHaveCount(0);
  await expectNoHorizontalScroll(driver);
  await shot(driver, "equipment-my-375");
  expect(driverErrors).toEqual([]);
});
