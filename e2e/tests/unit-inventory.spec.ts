import { expect, test } from "@playwright/test";

import {
  factLinePayload,
  parseFact,
  uncountedPositions,
  type InventoryLine,
} from "../../frontend/src/lib/stock/inventory";

function line(position: number): InventoryLine {
  return {
    position_no: position,
    name: `Позиция ${position}`,
    unit: "кг",
    quantity: null,
    material_id: `material-${position}`,
    material_batch_id: null,
    warehouse_id: "warehouse-1",
    snapshot_qty: null,
    balance_now: "10",
    discrepancy: null,
    plan_price: null,
    value: null,
  };
}

// Факт пересчёта: ноль - законный факт (всё списано); пустое, отрицательное и не число - нет.
test("факт: ноль допустим, пустое, отрицательное и нечисло - нет", () => {
  expect(parseFact("0")).toBe("0");
  expect(parseFact("40,5")).toBe("40.5");
  expect(parseFact("")).toBeNull();
  expect(parseFact("-1")).toBeNull();
  expect(parseFact("два")).toBeNull();
});

// Отправка пройдёт, только когда факт введён по каждой позиции (сервер отказывает по пустой - inventory.not_counted).
test("непосчитанные позиции: пустой или некорректный факт", () => {
  const lines = [line(1), line(2), line(3)];
  expect(uncountedPositions(lines, { 1: "4", 2: "" })).toEqual([2, 3]);
  expect(uncountedPositions(lines, { 1: "0", 2: "0", 3: "1" })).toEqual([]);
});

// В запись уходят идентичность позиции и факт; снимок и остаток сервер считает сам и не принимает.
test("тело строки: факт и идентичность позиции, без снимка и остатка", () => {
  expect(factLinePayload(line(4), "7,25")).toEqual({
    position_no: 4,
    name: "Позиция 4",
    unit: "кг",
    quantity: "7.25",
    material_id: "material-4",
    material_batch_id: null,
    warehouse_id: "warehouse-1",
  });
  expect(factLinePayload(line(5), "").quantity).toBeNull();
});
