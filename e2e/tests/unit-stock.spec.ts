import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { stockErrorKey, STOCK_ERROR_KEY_NAMES } from "../../frontend/src/lib/stock/errors";
import {
  issueLinePayload,
  issueProblems,
  parseQuantity,
  receiptLinePayload,
  receiptProblems,
  unitChoices,
} from "../../frontend/src/lib/stock/lines";
import type { MaterialOut } from "../../frontend/src/lib/stock/types";

// Чистая логика склада без браузера: тексты ошибок, строки приёма и выдачи, тело позиции для API.
const CERTIFIED: MaterialOut = {
  id: "11111111-1111-4111-8111-111111111111",
  legal_entity_id: "22222222-2222-4222-8222-222222222222",
  code: "CEM-1",
  name: "Цемент М500",
  base_unit: "кг",
  requires_certificate: true,
  active: true,
  units: [{ id: "33333333-3333-4333-8333-333333333333", unit: "мешок", factor: "25" }],
  plan_price: null,
};
const PLAIN: MaterialOut = { ...CERTIFIED, id: "44444444-4444-4444-8444-444444444444", requires_certificate: false, units: [] };

test("каждый код склада и справочника переводится в ключ текста, неизвестный код - нет", () => {
  for (const code of [
    "stock.material_required",
    "stock.receipt_consumed",
    "stock.batch_required",
    "stock.batch_not_applicable",
    "document.file_required",
    "material.certificate_required",
  ]) {
    expect(stockErrorKey(code), code).not.toBeNull();
  }
  expect(stockErrorKey("stock.material_required")).toBe("materialRequired");
  expect(stockErrorKey("какой-то.неизвестный")).toBeNull();
});

test("все ключи текстов ошибок склада есть в messages/ru.json", () => {
  const ru = JSON.parse(readFileSync(join(__dirname, "../../frontend/messages/ru.json"), "utf8"));
  for (const key of STOCK_ERROR_KEY_NAMES) {
    expect(ru.StockErrors[key], key).toBeTruthy();
  }
  expect(ru.StockErrors.materialRequired).toBe("нет в справочнике — попросите снабженца добавить");
});

test("количество: запятая или точка, до четырёх знаков, только положительное", () => {
  expect(parseQuantity("2,5")).toBe(2.5);
  expect(parseQuantity(" 3 ")).toBe(3);
  expect(parseQuantity("0")).toBeNull();
  expect(parseQuantity("-1")).toBeNull();
  expect(parseQuantity("1.12345")).toBeNull();
  expect(parseQuantity("abc")).toBeNull();
  expect(parseQuantity("")).toBeNull();
});

test("единицы выбора: базовая и единицы с коэффициентом", () => {
  expect(unitChoices(CERTIFIED)).toEqual(["кг", "мешок"]);
  expect(unitChoices(PLAIN)).toEqual(["кг"]);
});

test("приход: сертифицированный материал без партии и сертификата - проблемы строки", () => {
  const empty = { material: CERTIFIED, quantity: "10", unit: "кг", batchNo: "", batchId: null };
  expect(receiptProblems(empty)).toEqual(["batch", "certificate"]);
  const ready = { ...empty, batchNo: "П-1", batchId: "55555555-5555-4555-8555-555555555555" };
  expect(receiptProblems(ready)).toEqual([]);
  expect(receiptProblems({ material: PLAIN, quantity: "", unit: "кг", batchNo: "", batchId: null })).toEqual([
    "quantity",
  ]);
});

test("выдача: сертифицированному материалу нужна партия, остальным - нет", () => {
  expect(issueProblems({ material: CERTIFIED, quantity: "5", unit: "кг", batchId: null })).toEqual(["batch"]);
  expect(issueProblems({ material: PLAIN, quantity: "5", unit: "кг", batchId: null })).toEqual([]);
});

test("тело позиции приёма и выдачи совпадает с LineIn API", () => {
  const receipt = receiptLinePayload(
    { material: CERTIFIED, quantity: "2,5", unit: "мешок", batchNo: "П-1", batchId: "b" },
    "w",
  );
  expect(receipt).toEqual({
    name: "Цемент М500",
    unit: "мешок",
    quantity: 2.5,
    material_id: CERTIFIED.id,
    warehouse_id: "w",
    material_batch_id: "b",
  });
  const issue = issueLinePayload({ material: PLAIN, quantity: "4", unit: "кг", batchId: null }, "w", "n", "r");
  expect(issue).toEqual({
    name: "Цемент М500",
    unit: "кг",
    quantity: 4,
    material_id: PLAIN.id,
    warehouse_id: "w",
    project_node_id: "n",
    responsible_user_id: "r",
    material_batch_id: null,
  });
});
