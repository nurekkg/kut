// Типы склада и справочника материалов (ADR-016 часть 5, S7a/S7b). Количества - строки (Decimal с API).
export type UnitOut = { id: string; unit: string; factor: string };

export type MaterialOut = {
  id: string;
  legal_entity_id: string;
  code: string;
  name: string;
  base_unit: string;
  requires_certificate: boolean;
  active: boolean;
  units: UnitOut[];
  // Цена плана приходит только с правом procurement:read:prices; без права - null, элемента цены нет.
  plan_price: string | null;
};

export type BatchOut = {
  id: string;
  material_id: string;
  batch_no: string;
  certificate_document_id: string | null;
  customs_document_id: string | null;
  country_of_origin: string | null;
};

export type ImportIssue = { row: number; message: string };
export type ImportReport = { created: number; issues: ImportIssue[] };

export type WarehouseOut = { id: string; project_id: string; name: string; kind: string };

export type BalanceOut = { warehouse_id: string; material_id: string; base_unit: string; qty: string };

// Остаток по партиям (GET /stock/balances?by_batch=true): для сертифицированных материалов.
export type BatchBalanceOut = {
  warehouse_id: string;
  material_id: string;
  batch_id: string | null;
  batch_no: string | null;
  base_unit: string;
  qty: string;
};

export type MovementOut = {
  id: string;
  created_at: string;
  kind: "receipt" | "issue" | "reversal" | "adjustment";
  qty: string;
  unit: string;
  quantity_in_unit: string;
  factor: string;
  material_id: string;
  batch_id: string | null;
  document_id: string | null;
  version_id: string | null;
  source_line_id: string | null;
  project_node_id: string | null;
  responsible_user_id: string | null;
  reversal_of_id: string | null;
};

export type PostOut = { movements: number };
export type ReverseOut = { movement_id: string };
