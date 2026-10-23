// Акт пересчёта на телефоне (ADR-016, S7c): строки с учётным остатком и фактом. Чистая логика - её проверяют
// unit-тесты без стека (e2e/tests/unit-inventory.spec.ts), поэтому без React и без `@/`-псевдонимов в тестах.

import { normalizeQuantity } from "../documents/format";

export type InventoryLine = {
  position_no: number;
  name: string;
  unit: string;
  quantity: string | null;
  material_id: string | null;
  material_batch_id: string | null;
  warehouse_id: string | null;
  snapshot_qty: string | null;
  balance_now: string | null;
  discrepancy: string | null;
  plan_price: string | null;
  value: string | null;
};

export type InventoryLinesOut = { lines: InventoryLine[] };

// Факт пересчёта: ноль допустим (всё списано); пустое, отрицательное и не число - нет.
export function parseFact(input: string): string | null {
  const text = normalizeQuantity(input);
  return /^\d{1,14}(\.\d{1,4})?$/.test(text) ? text : null;
}

// Позиции без факта: по ним отправка не пройдёт (сервер отвечает inventory.not_counted).
export function uncountedPositions(lines: InventoryLine[], facts: Record<number, string>): number[] {
  return lines.filter((line) => parseFact(facts[line.position_no] ?? "") === null).map((line) => line.position_no);
}

// Тело строки для записи: идентичность позиции и факт. Снимок и остаток сервер не принимает.
export function factLinePayload(line: InventoryLine, fact: string) {
  return {
    position_no: line.position_no,
    name: line.name,
    unit: line.unit,
    quantity: parseFact(fact),
    material_id: line.material_id,
    material_batch_id: line.material_batch_id,
    warehouse_id: line.warehouse_id,
  };
}
