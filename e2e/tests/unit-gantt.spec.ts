import { expect, test } from "@playwright/test";

import { computeDomain, PX_PER_DAY, xEnd, xOfDay, xStart } from "../../frontend/src/lib/schedule/gantt";

// Чистая логика шкалы Ганта, без браузера. Сервер принимает любые даты ГПР (до 9999-12-31), поэтому
// одна такая строка не должна растягивать шкалу на миллионы суток и подвешивать вкладку у всех.
const TODAY = "2026-10-09";
const LIMIT_DAYS = 11_000; // окно шкалы: 10 лет в обе стороны от «сегодня» и запас по краям

test("шкала по обычным срокам: от самой ранней до самой поздней даты с запасом", () => {
  const domain = computeDomain([{ start: "2026-09-01", end: "2027-03-31" }], TODAY);
  expect(domain.clamped).toBe(false);
  // 211 суток между датами, запас 7 суток слева и 21 справа, плюс сам последний день
  expect(domain.days).toBe(211 + 7 + 21 + 1);
  expect(xStart(domain, "week", "2026-09-01")).toBe(7 * PX_PER_DAY.week);
});

test("дата из другого века не растягивает шкалу", () => {
  const domain = computeDomain([{ start: "1900-01-01", end: "9999-12-31" }], TODAY);
  expect(domain.clamped).toBe(true);
  expect(domain.days).toBeGreaterThan(0);
  expect(domain.days).toBeLessThan(LIMIT_DAYS);
});

test("одна лишняя строка не прячет остальные: обычные сроки остаются на своих местах", () => {
  const domain = computeDomain(
    [
      { start: "2026-09-01", end: "2026-12-31" },
      { start: "2026-10-01", end: "9999-12-31" },
    ],
    TODAY,
  );
  expect(domain.clamped).toBe(true);
  expect(domain.days).toBeLessThan(LIMIT_DAYS);
  expect(xStart(domain, "week", "2026-09-01")).toBe(7 * PX_PER_DAY.week);
});

test("строка целиком в далёком будущем или прошлом тоже прижимается", () => {
  for (const span of [
    { start: "2300-01-01", end: "2300-12-31" },
    { start: "1500-01-01", end: "1500-12-31" },
  ]) {
    const domain = computeDomain([span], TODAY);
    expect(domain.clamped).toBe(true);
    expect(domain.days).toBeLessThan(LIMIT_DAYS);
  }
});

test("координаты полос ограничены шкалой: вне окна полоса упирается в край", () => {
  const domain = computeDomain([{ start: "1900-01-01", end: "9999-12-31" }], TODAY);
  const width = domain.days * PX_PER_DAY.week;
  for (const x of [
    xStart(domain, "week", "1900-01-01"),
    xEnd(domain, "week", "9999-12-31"),
    xOfDay(domain, "week", 10_000_000),
    xOfDay(domain, "week", -10_000_000),
  ]) {
    expect(x).toBeGreaterThanOrEqual(-PX_PER_DAY.week);
    expect(x).toBeLessThanOrEqual(width + PX_PER_DAY.week);
  }
});
