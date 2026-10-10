import { chromium } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { PHONE } from "../helpers/env";
import { expect, test } from "../helpers/test";

type Manifest = {
  id: string;
  name: string;
  short_name: string;
  start_url: string;
  display: string;
  icons: { src: string; sizes: string; type: string; purpose?: string }[];
};

// Размеры PNG берутся из заголовка IHDR: ширина и высота лежат в байтах 16-23.
function pngSize(bytes: Buffer): { width: number; height: number } {
  expect(bytes.subarray(0, 8).toString("hex")).toBe("89504e470d0a1a0a");
  return { width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20) };
}

// PWA: манифест разобран браузером без ошибок, иконки на месте и нужных размеров, service worker
// установлен и активен, а проверка «устанавливаемости» Chrome (та же, что у Lighthouse) пуста.
// Установка невозможна в режиме инкогнито, а обычный контекст Playwright - именно он, поэтому
// проверка идёт в постоянном профиле.
test("PWA: манифест валиден, service worker активен, приложение устанавливаемо", async ({
  request,
}) => {
  const baseURL = process.env.E2E_BASE_URL ?? "http://localhost:3100";
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "kut-pwa-"));
  const context = await chromium.launchPersistentContext(profile, {
    ...PHONE,
    baseURL,
    locale: "ru-RU",
    executablePath: process.env.E2E_CHROMIUM_PATH || undefined,
    channel: process.env.E2E_CHANNEL || undefined,
  });
  try {
    const page = context.pages()[0] ?? (await context.newPage());
    await page.goto("/m");
    await page.waitForLoadState("networkidle");

    const link = await page.locator('link[rel="manifest"]').getAttribute("href");
    expect(link).toBeTruthy();
    const manifest = (await (await request.get(link ?? "")).json()) as Manifest;
    expect(manifest.start_url).toBe("/m");
    expect(manifest.display).toBe("standalone");
    expect(manifest.name).toBeTruthy();
    expect(manifest.short_name).toBeTruthy();
    for (const icon of manifest.icons) {
      const response = await request.get(icon.src);
      expect(response.status(), icon.src).toBe(200);
      expect(response.headers()["content-type"]).toBe(icon.type);
      const [width, height] = icon.sizes.split("x").map(Number);
      expect(pngSize(Buffer.from(await response.body())), icon.src).toEqual({ width, height });
    }
    expect(manifest.icons.some((icon) => icon.sizes === "192x192")).toBeTruthy();
    expect(manifest.icons.some((icon) => icon.sizes === "512x512")).toBeTruthy();
    expect(manifest.icons.some((icon) => icon.purpose === "maskable")).toBeTruthy();

    const worker = await page.evaluate(async () => {
      const registration = await navigator.serviceWorker.ready;
      return { state: registration.active?.state ?? null, scope: registration.scope };
    });
    expect(worker.state).toBe("activated");
    expect(worker.scope).toMatch(/\/$/);

    const session = await context.newCDPSession(page);
    const parsed = await session.send("Page.getAppManifest");
    expect(parsed.errors, "ошибки разбора манифеста").toEqual([]);
    const installable = await session.send("Page.getInstallabilityErrors");
    await session.detach();
    expect(installable.installabilityErrors, "причины, по которым нельзя установить").toEqual([]);
    console.log(
      "PWA " +
        JSON.stringify({
          manifestUrl: parsed.url,
          manifestErrors: parsed.errors.length,
          installabilityErrors: installable.installabilityErrors.length,
          serviceWorker: worker,
          icons: manifest.icons.map((icon) => `${icon.sizes} ${icon.purpose ?? "any"}`),
        }),
    );
  } finally {
    // Закрытие постоянного профиля иногда зависает на этой машине: не даём ему съесть таймаут теста.
    await Promise.race([context.close(), new Promise((resolve) => setTimeout(resolve, 20_000))]);
    fs.rmSync(profile, { recursive: true, force: true, maxRetries: 3 });
  }
});
