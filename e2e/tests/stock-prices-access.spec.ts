import { expect, test } from "../helpers/test";
import { get } from "../helpers/api";
import { EAST_NAME, projectNamed } from "../helpers/equipment";
import { desktopContext, shot, signIn } from "../helpers/ui";
import { certifiedMaterial, ownerSession } from "../helpers/stock";

// S7b, сценарий 4: план-цена закупок не попадает в DOM у прораба, мастера и кладовщика (не скрыта CSS).
// Внешние роли (покупатель, поставщик) экранов склада не видят. Фактическое поведение записывается
// в аннотации теста, потому что отказ может быть и редиректом, и пустым экраном.
test("план-цена не в DOM у прораба, мастера и кладовщика; внешние роли не видят склад", async ({
  browser,
  request,
}) => {
  const owner = await ownerSession(request);
  const east = await projectNamed(request, owner, EAST_NAME);
  const material = await certifiedMaterial(request, owner, east.legal_entity_id);

  const put = await request.put(`/api/v1/materials/${material.id}/price`, {
    headers: { Authorization: `Bearer ${owner.token}` },
    data: { unit_price: "150.5" },
  });
  expect(put.ok(), `цена: ${put.status()}`).toBeTruthy();
  // Контроль: владелец (право procurement:read:prices) видит цену в API.
  const control = await get<{ plan_price: string | null }>(request, owner, `/api/v1/materials/${material.id}`);
  expect(control.plan_price).toBe("150.50");

  for (const user of ["foreman", "master", "storekeeper"]) {
    const context = await desktopContext(browser);
    const page = await context.newPage();
    await signIn(page, user);
    await page.goto(`/materials/${material.id}`);
    await page.waitForLoadState("networkidle");
    const visible = await page.getByText(material.name).count();
    test.info().annotations.push({ type: "fact", description: `${user}: карточка видна=${visible > 0}, url=${new URL(page.url()).pathname}` });
    await expect(page.getByText("План-цена")).toHaveCount(0);
    expect(await page.content()).not.toContain("150.50");
    await shot(page, `stock-materials-card-${user}`, true);
    await context.close();
  }

  for (const user of ["customer", "supplier"]) {
    const context = await desktopContext(browser);
    const page = await context.newPage();
    await signIn(page, user);
    await page.goto("/stock");
    await page.waitForLoadState("networkidle");
    test.info().annotations.push({ type: "fact", description: `${user}: /stock url=${new URL(page.url()).pathname}` });
    await page.goto(`/projects/${east.id}/warehouse`);
    await page.waitForLoadState("networkidle");
    test.info().annotations.push({ type: "fact", description: `${user}: склад url=${new URL(page.url()).pathname}` });
    await expect(page.getByText(material.name)).toHaveCount(0);
    await expect(page.getByText("План-цена")).toHaveCount(0);
    await shot(page, `stock-external-${user}`, true);
    await context.close();
  }
});
