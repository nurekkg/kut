// Строки приёма (М-7) и выдачи (ВН): проверка на телефоне и тело позиции для API. Сервер проверяет
// всё повторно (гарды БД); здесь - понятные подсказки до отправки.
import type { MaterialOut } from "./types";

export type ReceiptRow = {
  material: MaterialOut;
  quantity: string;
  unit: string;
  batchNo: string;
  batchId: string | null;
};

export type IssueRow = {
  material: MaterialOut;
  quantity: string;
  unit: string;
  batchId: string | null;
};

export type RowProblem = "quantity" | "batch" | "certificate";

// Количество с точкой или запятой; до четырёх знаков (как LineIn.quantity на сервере).
export function parseQuantity(value: string): number | null {
  const normalized = value.trim().replace(",", ".");
  if (!/^\d{1,14}(\.\d{1,4})?$/.test(normalized)) return null;
  const parsed = Number(normalized);
  return parsed > 0 ? parsed : null;
}

// Единицы для выбора: базовая единица материала и единицы с коэффициентом.
export function unitChoices(material: MaterialOut): string[] {
  return [material.base_unit, ...material.units.map((unit) => unit.unit)];
}

export function receiptProblems(row: ReceiptRow): RowProblem[] {
  const problems: RowProblem[] = [];
  if (parseQuantity(row.quantity) === null) problems.push("quantity");
  if (row.material.requires_certificate) {
    if (row.batchNo.trim() === "") problems.push("batch");
    if (row.batchId === null) problems.push("certificate");
  }
  return problems;
}

export function issueProblems(row: IssueRow): RowProblem[] {
  const problems: RowProblem[] = [];
  if (parseQuantity(row.quantity) === null) problems.push("quantity");
  if (row.material.requires_certificate && row.batchId === null) problems.push("batch");
  return problems;
}

export function receiptLinePayload(row: ReceiptRow, warehouseId: string) {
  return {
    name: row.material.name,
    unit: row.unit,
    quantity: parseQuantity(row.quantity) ?? 0,
    material_id: row.material.id,
    warehouse_id: warehouseId,
    material_batch_id: row.batchId,
  };
}

export function issueLinePayload(
  row: IssueRow,
  warehouseId: string,
  nodeId: string,
  responsibleId: string,
) {
  return {
    name: row.material.name,
    unit: row.unit,
    quantity: parseQuantity(row.quantity) ?? 0,
    material_id: row.material.id,
    warehouse_id: warehouseId,
    project_node_id: nodeId,
    responsible_user_id: responsibleId,
    material_batch_id: row.batchId,
  };
}
