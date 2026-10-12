import fs from "node:fs";
import path from "node:path";

import { apiLogin, get } from "../helpers/api";
import { test, expect } from "../helpers/test";
import { desktopContext, shot, signIn, watchErrors } from "../helpers/ui";
import { today } from "../helpers/env";

function percentile(values: number[], p: number): number {
  const sorted = [...values].sort((a, b) => a - b);
  const index = Math.min(sorted.length - 1, Math.ceil((p / 100) * sorted.length) - 1);
  return Math.round(sorted[Math.max(0, index)] * 10) / 10;
}

// Замер календаря на 300+ единицах: время до первых строк после перехода и цена прокрутки
// (мс на кадр при сдвиге области). Результаты пишутся в artifacts/equipment-timing.json.
test("календарь на 300+ единицах: первый рендер и прокрутка, p50 и p95", async ({ browser, request }) => {
  const head = await apiLogin(request, "equipment_head");
  const calendar = await get<{ units: { id: string }[] }>(request, head, "/api/v1/equipment/calendar", {
    date_from: today(-28),
    date_to: today(28),
    limit: 2000,
  });
  expect(calendar.units.length, "в календаре не меньше 300 единиц").toBeGreaterThanOrEqual(300);

  const context = await desktopContext(browser);
  const page = await context.newPage();
  const errors = watchErrors(page);
  await signIn(page, "equipment_head");

  const firstRender: number[] = [];
  for (let run = 0; run < 10; run += 1) {
    const started = Date.now();
    await page.goto("/equipment/calendar");
    await page.getByTestId("calendar-row").first().waitFor({ timeout: 60_000 });
    firstRender.push(Date.now() - started);
  }

  const frames = await page.evaluate(async () => {
    const grid = document.querySelector('[data-testid="calendar-grid"]') as HTMLElement | null;
    if (!grid) return [] as number[];
    const durations: number[] = [];
    for (let step = 0; step < 60; step += 1) {
      const started = performance.now();
      grid.scrollTop = step * 180;
      await new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      );
      durations.push(performance.now() - started);
    }
    return durations;
  });
  expect(frames.length).toBe(60);

  const result = {
    units: calendar.units.length,
    firstRenderMs: { p50: percentile(firstRender, 50), p95: percentile(firstRender, 95), runs: firstRender.length },
    scrollFrameMs: { p50: percentile(frames, 50), p95: percentile(frames, 95), steps: frames.length },
    measuredAt: new Date().toISOString(),
  };
  const dir = path.join(__dirname, "..", "artifacts");
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, "equipment-timing.json"), JSON.stringify(result, null, 2));
  console.log(`календарь: ${JSON.stringify(result)}`);

  await shot(page, "equipment-calendar");
  expect(errors).toEqual([]);
});
