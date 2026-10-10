import { expect, test } from "@playwright/test";

import { APPROVAL_LIMIT, approvalTotal } from "../../frontend/src/lib/approvals-count";

// Счётчик «На согласовании» = сумма очередей по видимым объектам. Чистая логика, без браузера.
test("счётчик - сумма очередей по всем объектам", () => {
  expect(approvalTotal([3, 0, 5], APPROVAL_LIMIT)).toEqual({ total: 8, capped: false });
});

test("счётчик - пустые очереди дают ноль без «+»", () => {
  expect(approvalTotal([0, 0], APPROVAL_LIMIT)).toEqual({ total: 0, capped: false });
  expect(approvalTotal([], APPROVAL_LIMIT)).toEqual({ total: 0, capped: false });
});

test("счётчик - два объекта по 150 записей дают 300 без «+» (лимит действует на каждый объект)", () => {
  expect(approvalTotal([150, 150], APPROVAL_LIMIT)).toEqual({ total: 300, capped: false });
});

test("счётчик - упор хотя бы одного объекта в лимит даёт «N+»", () => {
  expect(approvalTotal([200, 4], APPROVAL_LIMIT)).toEqual({ total: 204, capped: true });
});
