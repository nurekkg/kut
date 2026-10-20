import type { APIRequestContext, Page } from "@playwright/test";

import { apiLogin } from "../helpers/api";
import { expect, test } from "../helpers/test";
import {
  desktopContext,
  expectNoHorizontalScroll,
  phoneContext,
  shot,
  signIn,
  watchErrors,
} from "../helpers/ui";

// WhatsApp по согласию (S6b-close): по умолчанию выключено; прораб включает его в профиле сам, видит текст
// согласия, отмечает чекбокс и сохраняет. Дата согласия пишется сервером (гард БД).
async function setForemanFlag(request: APIRequestContext, flag: boolean): Promise<void> {
  const foreman = await apiLogin(request, "foreman");
  const response = await request.patch("/api/v1/notifications/settings", {
    headers: { Authorization: `Bearer ${foreman.token}` },
    data: { notify_whatsapp: flag },
  });
  expect(response.ok(), `переключатель WhatsApp: ${await response.text()}`).toBeTruthy();
}

async function openConsentBlock(page: Page): Promise<void> {
  await page.goto("/profile");
  const toggle = page.getByTestId("notify-whatsapp");
  await expect(toggle).toBeVisible({ timeout: 20_000 });
  await toggle.check();
  await expect(page.getByTestId("notify-consent")).toBeVisible();
}

test("профиль: WhatsApp выключен, согласие прораб дает сам и видит дату", async ({ browser, request }) => {
  test.setTimeout(180_000);
  await setForemanFlag(request, false);

  const context = await desktopContext(browser);
  const page = await context.newPage();
  const errors = watchErrors(page);
  await signIn(page, "foreman");
  await page.goto("/profile");
  await expect(page.getByTestId("notify-whatsapp")).not.toBeChecked({ timeout: 20_000 });

  await openConsentBlock(page);
  await expect(page.getByTestId("notify-consent-save")).toBeDisabled();
  await shot(page, "profile-1280-consent-text", true);

  await page.getByTestId("notify-consent-checkbox").check();
  await page.getByTestId("notify-consent-save").click();
  await expect(page.getByTestId("notify-consent")).toHaveCount(0, { timeout: 20_000 });
  await expect(page.getByTestId("notify-whatsapp")).toBeChecked();
  await shot(page, "profile-1280-consented", true);
  expect(errors).toEqual([]);
});

test("профиль на телефоне 375: текст согласия без горизонтальной прокрутки", async ({ browser, request }) => {
  test.setTimeout(180_000);
  await setForemanFlag(request, false);

  const context = await phoneContext(browser);
  const page = await context.newPage();
  const errors = watchErrors(page);
  await signIn(page, "foreman");
  await openConsentBlock(page);
  await expectNoHorizontalScroll(page);
  await shot(page, "profile-375-consent-text", true);
  expect(errors).toEqual([]);
});
