import type { Page } from "@playwright/test";

import { expect } from "./test";

// Дата как её показывает интерфейс (ДД.ММ.ГГ): по ней находим свою строку в списке заявок.
export function shortDate(iso: string): string {
  const [year, month, day] = iso.split("-");
  return `${day}.${month}.${year.slice(2)}`;
}

export async function createRequest(
  page: Page,
  input: { project: string; type: string; from: string; until: string; hours: string },
): Promise<void> {
  await page.goto("/equipment/requests");
  await page.getByTestId("new-request").click();
  await pick(page, "#request-project", input.project);
  await pick(page, "#request-type", input.type);
  await page.locator("#request-from").fill(input.from);
  await page.locator("#request-until").fill(input.until);
  await page.locator("#request-hours").fill(input.hours);
  await page.getByTestId("form-submit").click();
  await expect(page.getByRole("dialog")).toBeHidden({ timeout: 20_000 });
}

// Строка заявки в статусе draft на нужную дату: отправляем на назначение, возвращаем её id.
export async function submitRequest(page: Page, day: string): Promise<string> {
  const row = page
    .locator('[data-testid="request-list"] li[data-status="draft"]')
    .filter({ hasText: shortDate(day) })
    .first();
  await expect(row).toBeVisible({ timeout: 20_000 });
  const id = (await row.getAttribute("data-request-id")) ?? "";
  await row.getByTestId("request-submit").click();
  await expect(page.locator(`[data-request-id="${id}"]`)).toHaveAttribute("data-status", "submitted", {
    timeout: 20_000,
  });
  return id;
}

// Выбор в выпадающем списке, чьи варианты грузятся запросом: ждём вариант, потом выбираем.
export async function pick(page: Page, selector: string, value: string): Promise<void> {
  await page.locator(`${selector} option[value="${value}"]`).waitFor({ state: "attached", timeout: 30_000 });
  await page.locator(selector).selectOption(value);
}
