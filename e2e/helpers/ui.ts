import { expect, type Browser, type BrowserContext, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

import { DESKTOP, GEO, PASSWORD, PHONE, SCREENS } from "./env";
import { waitForStack } from "./health";

// Ошибки страницы и консоли браузера (в том числе «нет текста» next-intl и сбои гидратации):
// сценарии без заведомых отказов сервера в конце проверяют, что список пуст.
export function watchErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    // Анонимный заход: страница просит обновить сеанс, и сервер честно отвечает 401 - это работа
    // входа, а не ошибка приложения.
    const refresh = message.location().url.endsWith("/api/v1/auth/refresh");
    if (refresh && message.text().includes("401")) return;
    errors.push(`console: ${message.text()}`);
  });
  return errors;
}

// Форма входа на открытой странице входа: страница не перезагружается, поэтому состояние приложения
// (сессия, очередь отправки) переживает смену человека на устройстве, как на общем телефоне.
// Если сервер ответил сбоем (PostgreSQL на слабой машине иногда уходит в восстановление), вход
// повторяется после того, как стек снова готов, а не висит до конца времени сценария.
export async function signInHere(page: Page, user: string): Promise<void> {
  for (let attempt = 1; ; attempt += 1) {
    await page.locator("#identifier").fill(`${user}@kut.test`);
    await page.locator("#password").fill(PASSWORD);
    await page.getByRole("button", { name: "Войти" }).click();
    const signedIn = page
      .waitForURL(/\/projects/, { timeout: 40_000 })
      .then(() => "ok" as const)
      .catch(() => "timeout" as const);
    const refused = page
      .getByRole("alert")
      .filter({ hasText: "Не удалось выполнить действие" })
      .waitFor({ timeout: 40_000 })
      .then(() => "refused" as const)
      .catch(() => "timeout" as const);
    const outcome = await Promise.race([signedIn, refused]);
    if (outcome === "ok") return;
    expect(attempt, `вход ${user}: ${outcome} после ${attempt} попыток`).toBeLessThan(4);
    await waitForStack(page.request);
  }
}

export async function signIn(page: Page, user: string): Promise<void> {
  await page.goto("/login");
  await signInHere(page, user);
}

// Телефон мастера: 375 px, касание, геолокация замокана (разрешение выдано заранее).
export async function phoneContext(browser: Browser): Promise<BrowserContext> {
  return browser.newContext({
    ...PHONE,
    geolocation: GEO,
    permissions: ["geolocation"],
    locale: "ru-RU",
    timezoneId: "Asia/Bishkek",
  });
}

export async function desktopContext(browser: Browser): Promise<BrowserContext> {
  return browser.newContext({ ...DESKTOP, locale: "ru-RU", timezoneId: "Asia/Bishkek" });
}

// Снимок экрана после загрузки картинок: фото идут по подписанным ссылкам и грузятся лениво.
export async function shot(page: Page, name: string): Promise<void> {
  fs.mkdirSync(SCREENS, { recursive: true });
  await page
    .waitForFunction(() => Array.from(document.images).every((image) => image.complete), undefined, {
      timeout: 10_000,
    })
    .catch(() => undefined);
  await page.screenshot({ path: path.join(SCREENS, `${name}.png`), fullPage: false });
}

// Горизонтальной прокрутки на телефоне быть не должно: экран 375 px помещается целиком.
export async function expectNoHorizontalScroll(page: Page): Promise<void> {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow, "страница шире экрана телефона").toBeLessThanOrEqual(1);
}

// Крупные кнопки: не меньше 48 px по высоте (требование к экрану мастера).
export async function expectTapTarget(page: Page, testId: string): Promise<void> {
  const box = await page.getByTestId(testId).first().boundingBox();
  expect(box, `${testId}: нет в разметке`).toBeTruthy();
  expect(box?.height ?? 0, `${testId}: высота кнопки`).toBeGreaterThanOrEqual(48);
}

// Мастер вносит факт: узел, работа, объём, фото. Возвращает страницу на итоговом шаге.
export async function enterFact(
  page: Page,
  options: { nodeName: string; workName: string; volume: string; photo: string; screens?: string },
): Promise<void> {
  const screens = options.screens;
  await page.goto("/m");
  const search = page.getByLabel("Найти узел");
  const project = page.getByTestId("project-option").first();
  await expect(search.or(project)).toBeVisible();
  // Объектов несколько - сначала выбор объекта, один объект подставляется сам.
  if (await project.isVisible()) await project.click();
  await search.fill(options.nodeName);
  await page.getByTestId("node-option").filter({ hasText: options.nodeName }).first().click();
  if (screens) await shot(page, `${screens}-2-node`);

  await expect(page.getByTestId("wizard")).toHaveAttribute("data-step", "work");
  const work = page.getByTestId("work-option").filter({ hasText: options.workName }).first();
  await expect(work).toBeVisible();
  if (screens) await shot(page, `${screens}-3-work`);
  await work.click();

  await expect(page.getByTestId("wizard")).toHaveAttribute("data-step", "volume");
  await page.getByTestId("volume-input").fill(options.volume);
  if (screens) await shot(page, `${screens}-4-volume`);
  await page.getByTestId("volume-next").click();

  await expect(page.getByTestId("wizard")).toHaveAttribute("data-step", "evidence");
  await page.getByTestId("photo-input").setInputFiles(options.photo);
  await expect(page.getByTestId("photo-list").locator("img")).toHaveCount(1);
  await expect(page.getByTestId("geo-card")).toHaveAttribute("data-state", "ready");
  if (screens) await shot(page, `${screens}-5-evidence`);
}
