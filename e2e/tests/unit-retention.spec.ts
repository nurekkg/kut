import { expect, test } from "@playwright/test";

import { OUTBOX_TTL_MS, selectRemovable } from "../../frontend/src/lib/field/retention";

// Чистая логика очистки очереди отправки на устройстве, без браузера (решение PO): записи другого
// пользователя стираются при входе, записи старше 7 дней - при запуске приложения.
const NOW = Date.UTC(2026, 9, 10, 12, 0, 0);
const DAY = 24 * 60 * 60 * 1000;
const entry = (id: string, userId: string, ageMs: number) => ({
  id,
  userId,
  createdAt: NOW - ageMs,
});

test("срок хранения - ровно семь суток", () => {
  expect(OUTBOX_TTL_MS).toBe(7 * DAY);
});

test("свои свежие записи остаются", () => {
  const entries = [entry("a", "master", 0), entry("b", "master", 3 * DAY)];
  expect(selectRemovable(entries, "master", NOW)).toEqual([]);
});

test("записи другого пользователя стираются, какими бы свежими ни были", () => {
  const entries = [entry("mine", "master", 1000), entry("alien", "foreman", 1000)];
  expect(selectRemovable(entries, "master", NOW).map((item) => item.id)).toEqual(["alien"]);
});

test("запись старше семи суток стирается, ровно семь суток - ещё нет", () => {
  const entries = [
    entry("old", "master", 7 * DAY + 1),
    entry("edge", "master", 7 * DAY),
    entry("fresh", "master", 7 * DAY - 1),
  ];
  expect(selectRemovable(entries, "master", NOW).map((item) => item.id)).toEqual(["old"]);
});

test("чужая запись стирается и по принадлежности, и по возрасту: один раз в списке", () => {
  const entries = [entry("alien-old", "foreman", 30 * DAY)];
  expect(selectRemovable(entries, "master", NOW)).toHaveLength(1);
});

test("часы устройства ушли назад: запись из будущего не считается просроченной", () => {
  expect(selectRemovable([entry("ahead", "master", -5 * DAY)], "master", NOW)).toEqual([]);
});

test("пустая очередь - нечего стирать", () => {
  expect(selectRemovable([], "master", NOW)).toEqual([]);
});
