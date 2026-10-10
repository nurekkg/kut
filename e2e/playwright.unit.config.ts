import { defineConfig } from "@playwright/test";

// Юнит-проверки чистой логики фронтенда (unit-*.spec.ts): без браузера и без поднятого стека.
export default defineConfig({
  testDir: "./tests",
  testMatch: "unit-*.spec.ts",
  workers: 1,
  reporter: [["list"]],
});
