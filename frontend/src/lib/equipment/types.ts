// Типы модуля «Техника» зеркалят схемы бэкенда (backend/app/modules/equipment/schemas.py).

export const UNIT_STATUSES = [
  "available",
  "assigned",
  "day_full",
  "in_repair",
  "doc_warning",
  "doc_expired",
] as const;
export type UnitStatus = (typeof UNIT_STATUSES)[number];

export const UNIT_NAMES = ["hour", "motor_hour", "shift", "trip", "tonne", "month"] as const;
export type UnitName = (typeof UNIT_NAMES)[number];

export const DOCUMENT_KINDS = [
  "passport",
  "technical_inspection",
  "insurance",
  "permit",
  "other",
] as const;
export type DocumentKind = (typeof DOCUMENT_KINDS)[number];

export const MAINTENANCE_KINDS = ["to", "repair"] as const;
export type MaintenanceKind = (typeof MAINTENANCE_KINDS)[number];

export const MAINTENANCE_STATUSES = ["planned", "in_progress", "done", "cancelled"] as const;
export type MaintenanceStatus = (typeof MAINTENANCE_STATUSES)[number];

export const REQUEST_STATUSES = [
  "draft",
  "submitted",
  "assigned",
  "rejected",
  "cancelled",
  "closed",
] as const;
export type RequestStatus = (typeof REQUEST_STATUSES)[number];

export const CONTRACT_STATUSES = ["draft", "active", "closed", "terminated"] as const;
export type ContractStatus = (typeof CONTRACT_STATUSES)[number];

export type Ownership = "own" | "rented";

export type TypeRow = { id: string; code: string; name: string; default_unit: string };

export type UnitRow = {
  id: string;
  legal_entity_id: string;
  equipment_type_id: string;
  type_code: string;
  ownership: Ownership;
  registry_number: string;
  contract_id: string | null;
  model: string | null;
  year: number | null;
};

export type UnitListItem = UnitRow & { status: UnitStatus };

export type DocumentRow = {
  id: string;
  equipment_unit_id: string;
  kind: DocumentKind;
  number: string;
  valid_until: string;
  version: number;
  // Связанный документ реестра (S5b); номер виден только внутренним ролям.
  document_id: string | null;
  document_no: string | null;
};

export type MaintenanceRow = {
  id: string;
  equipment_unit_id: string;
  kind: MaintenanceKind;
  title: string;
  planned_from: string;
  planned_to: string | null;
  status: MaintenanceStatus;
  project_id: string | null;
  done_at: string | null;
};

export type UnitCard = UnitRow & {
  status: UnitStatus;
  documents: DocumentRow[];
  maintenance: MaintenanceRow[];
};

export type AssignmentRow = {
  id: string;
  project_id: string;
  project_name?: string | null;
  legal_entity_id: string;
  equipment_unit_id: string;
  request_id: string | null;
  node_id: string | null;
  schedule_item_id: string | null;
  date_from: string;
  date_to: string;
  planned_hours_per_day: string;
  operator_id: string | null;
  status: "planned" | "finished" | "cancelled";
  cancel_reason: string | null;
};

export type AssignmentWarning = { code: string; kind: string; valid_until: string };

export type AssignmentCheck = { warnings: AssignmentWarning[] };

export type RequestRow = {
  id: string;
  project_id: string;
  project_name?: string | null;
  legal_entity_id: string;
  node_id: string | null;
  schedule_item_id: string | null;
  equipment_type_id: string;
  date_from: string;
  date_to: string;
  planned_hours_per_day: string;
  status: RequestStatus;
  reject_reason: string | null;
};

export type OwnRateRow = {
  id: string;
  legal_entity_id: string;
  equipment_type_id: string;
  unit: UnitName;
  amount: string;
  valid_from: string;
  proposed_by: string;
  approved_by: string | null;
};

export type ContractRow = {
  id: string;
  legal_entity_id: string;
  counterparty_id: string;
  kind: "rental_equipment";
  number: string;
  date_from: string;
  date_to: string | null;
  status: ContractStatus;
};

export type RateRow = {
  id: string;
  contract_id: string;
  unit: UnitName;
  with_operator: boolean;
  amount: string;
  min_quantity: string | null;
  valid_from: string;
};

export type OperatorRow = {
  id: string;
  legal_entity_id: string;
  full_name: string;
  employment: "staff" | "lessor";
  user_id: string | null;
  counterparty_id: string | null;
};

export type CounterpartyRef = { id: string; name: string };

export type NamedRef = { id: string; name: string };

export type CalendarAssignment = {
  id: string;
  project_id: string;
  project_name: string;
  date_from: string;
  date_to: string;
  planned_hours_per_day: string;
  status: string;
};

export type CalendarDay = { date: string; hours: string };

export type CalendarMaintenance = {
  id: string;
  kind: MaintenanceKind;
  title: string;
  planned_from: string;
  planned_to: string | null;
  status: MaintenanceStatus;
};

export type CalendarWarning = { kind: DocumentKind; valid_until: string };

export type CalendarUnit = {
  id: string;
  registry_number: string;
  type_code: string;
  ownership: Ownership;
  assignments: CalendarAssignment[];
  days: CalendarDay[];
  maintenance: CalendarMaintenance[];
  warnings: CalendarWarning[];
};

export type CalendarPayload = { date_from: string; date_to: string; units: CalendarUnit[] };

export type ImportIssue = { row: number; column: string; code: string; message: string };

export type ImportReport = {
  batch_id: string | null;
  status: "checked" | "committed" | "rejected";
  rows_total: number;
  created: number;
  updated: number;
  unchanged: number;
  errors: ImportIssue[];
};

export type BatchRow = {
  id: string;
  kind: string;
  status: "checked" | "committed" | "rejected";
  created_count: number;
  updated_count: number;
  unchanged_count: number;
  error_count: number;
  created_at: string;
};
