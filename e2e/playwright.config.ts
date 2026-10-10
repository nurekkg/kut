import { defineConfig } from "@playwright/test";

// Сквозные проверки идут против запущенного стека (web на 3100, api, MinIO): docker compose up -d
// и сид `docker compose run --rm dev-seed`. Один воркер: сценарии вносят данные в один демо-объект.
//
// Браузер: по умолчанию встроенный Chromium Playwright (`npx playwright install chromium`).
// E2E_CHROMIUM_PATH указывает на готовый Chromium/Chrome (например, из %LOCALAPPDATA%/ms-playwright),
// E2E_CHANNEL - на установленный в системе браузер ("chrome", "msedge").
const executablePath = process.env.E2E_CHROMIUM_PATH || undefined;
const channel = process.env.E2E_CHANNEL || undefined;
// Слабая машина (нехватка памяти, медленный диск, PostgreSQL уходит в восстановление на минуты):
// E2E_SLOW=3 растягивает ожидания втрое.
const slow = Number(process.env.E2E_SLOW) || 1;

export default defineConfig({
  testDir: "./tests",
  globalSetup: "./helpers/global-setup.ts",
  outputDir: "./artifacts/test-results",
  fullyParallel: false,
  workers: 1,
  // Сценарии заводят свои данные, поэтому повтор безопасен; он страхует от сбоев слабой машины
  // (PostgreSQL иногда уходит в восстановление на десяток секунд посреди прогона).
  retries: 2,
  timeout: 180_000 * slow,
  expect: { timeout: 20_000 * slow },
  reporter: [["list"], ["html", { open: "never", outputFolder: "./artifacts/report" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3100",
    locale: "ru-RU",
    timezoneId: "Asia/Bishkek",
    // Трассы и снимки при каждом тесте - десятки мегабайт на диск хоста, а диск общий с виртуальной
    // машиной Docker: на слабой машине это добавляло зависаний. По умолчанию - только при падении;
    // для доказательств: E2E_TRACE=on E2E_SCREENSHOT=on (`--trace=on` в командной строке тоже работает).
    trace: (process.env.E2E_TRACE as "on" | "off" | "retain-on-failure" | undefined) ?? "retain-on-failure",
    screenshot: (process.env.E2E_SCREENSHOT as "on" | "off" | "only-on-failure" | undefined) ?? "only-on-failure",
    launchOptions: { executablePath },
    channel,
  },
});
