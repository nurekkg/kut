// Типы модуля «Документы» зеркалят схемы бэкенда (backend/app/modules/documents/schemas.py).

export const DOCUMENT_STATUSES = ["draft", "on_approval", "approved", "signed", "archived"] as const;
export type DocumentStatus = (typeof DOCUMENT_STATUSES)[number];

// Виды из backend/app/modules/documents/types.py: подпись каждого берётся из ru.json.
export const DOCUMENT_KIND_CODES = [
  "contract_customer",
  "contract_subcontract",
  "contract_supply",
  "contract_rental",
  "addendum",
  "estimate_local",
  "estimate_summary",
  "ks2",
  "ks3",
  "ks6",
  "hidden_works_act",
  "ird",
  "waybill_torg12",
  "waybill_m15",
  "waybill_internal",
  "invoice_esf",
  "m7_act",
  "material_request",
  "commissioning_act",
  "equipment_passport",
  "equipment_technical_inspection",
  "equipment_insurance",
  "equipment_permit",
  "equipment_other",
] as const;
export type DocumentKindCode = (typeof DOCUMENT_KIND_CODES)[number];

export const LINK_KIND_CODES = ["project_node", "work", "work_actual", "equipment_unit"] as const;
export type LinkKindCode = (typeof LINK_KIND_CODES)[number];

export type KindOut = {
  code: string;
  prefix: string;
  scope: "object" | "legal_entity" | "either";
  financial: boolean;
  counterparty_required: boolean;
  contract_required: boolean;
  links: string[];
  lines: boolean;
};

export type VersionOut = {
  id: string;
  document_id: string;
  version_no: number;
  status: string;
  content_type: string;
  size_bytes: number;
  created_at: string;
  comment: string | null;
  checksum_sha256: string | null;
  author_name: string | null;
  // Сумма принадлежит версии (R1): внутренним с правом на вид, внешним - утверждённой версии; иначе null.
  amount: string | null;
};

export type DocumentLink = { kind: string; id: string };

export type CounterpartyOut = { id: string; name: string };

export type DocumentOut = {
  id: string;
  kind: string;
  financial: boolean;
  title: string;
  project_id: string | null;
  counterparty_id: string | null;
  created_at: string;
  counterparty_name: string | null;
  project_name: string | null;
  latest_version_no: number | null;
  latest_status: string | null;
  registration_no: string | null;
  legal_entity_id: string | null;
  legal_entity_name: string | null;
  contract_id: string | null;
  archived_at: string | null;
  current_version: VersionOut | null;
  // Текущая сумма - сумма действующей версии (утверждённой или подписанной), иначе последней видимой.
  amount: string | null;
  amount_version_no: number | null;
  effective_version_no: number | null;
  links: DocumentLink[];
};

export type DocumentDetail = DocumentOut & { versions: VersionOut[] };

export type EventOut = {
  id: string;
  version_no: number;
  from_status: string | null;
  to_status: string;
  comment: string | null;
  actor_name: string | null;
  created_at: string;
};

export type PresignOut = {
  key: string;
  upload_url: string;
  headers: Record<string, string>;
  expires_in: number;
};

export type DownloadOut = { url: string; expires_in: number };

export type PresignBody = { content_type: string; size_bytes: number; checksum_sha256: string };

export type ConfirmBody = { key: string; comment: string | null; amount: string | null };

// Позиции версии (ADR-015 часть 4): цена и сумма - null, если право на деньги не выдано (сервер отдаёт null).
export type LineOut = {
  position_no: number;
  name: string;
  unit: string;
  quantity: string;
  work_id: string | null;
  project_node_id: string | null;
  unit_price: string | null;
  amount: string | null;
};

export type LinesOut = { lines: LineOut[]; total: string | null };

// Черновик позиции в форме: количество - строкой, как введено; проверка - в диалоге. Номер позиции (position_no)
// держит идентичность: цена и связи переходят к той же позиции после сохранения; у новой позиции номера нет.
export type LineDraft = {
  position_no: number | null;
  name: string;
  unit: string;
  quantity: string;
  work_id: string | null;
  project_node_id: string | null;
};

export type PriceDraft = { position_no: number; unit_price: string };

// Создание: поля вне контракта бэкенд отвергает (extra=forbid), поэтому тело собирается по полям.
// Сумма в создании не передаётся: она принадлежит версии и вносится при загрузке (R1).
export type CreateBody = {
  kind: string;
  legal_entity_id: string;
  project_id?: string;
  counterparty_id?: string;
  title: string;
  links: DocumentLink[];
};

// Маршрут согласования (ADR-015 часть 5): шаги - снимок на попытку, решения - журнал решений.
export type RouteStepStatus = "pending" | "approved" | "rejected" | "skipped";

export type RouteStep = {
  attempt: number;
  position: number;
  role_code: string;
  status: RouteStepStatus;
  decided_by_name: string | null;
  on_behalf_of_name: string | null;
  decided_at: string | null;
};

export type RouteDecision = {
  id: string;
  attempt: number;
  position: number;
  role_code: string;
  decision: "approve" | "reject";
  // Причину отдаёт сервер только тем, кто её видит (ADR-015 часть 5, раздел 4); иначе null.
  reason: string | null;
  actor_name: string | null;
  on_behalf_of_name: string | null;
  decided_at: string;
};

export type RouteOut = {
  version_id: string;
  version_status: string;
  attempts: number;
  current_position: number | null;
  can_decide: boolean;
  steps: RouteStep[];
  decisions: RouteDecision[];
};

export type RouteTemplateStep = { position: number; role_code: string };

export type RouteTemplate = { code: string; name: string; steps: RouteTemplateStep[] };

// Очередь /approvals: документы, где текущий шаг мой (роль или замещение).
export type ApprovalQueueItem = {
  document_id: string;
  version_id: string;
  version_no: number;
  document_title: string;
  kind: string;
  project_id: string | null;
  project_name: string | null;
  attempt: number;
  position: number;
  role_code: string;
};

export type DecisionBody = { position: number; decision: "approve" | "reject"; reason?: string };

export type SignedScanBody = { key: string; comment: string | null };
