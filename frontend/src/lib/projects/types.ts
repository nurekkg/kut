export const PROJECT_STATUSES = ["active", "suspended", "completed", "archived"] as const;
export type ProjectStatus = (typeof PROJECT_STATUSES)[number];

// Девять стадий ЖЦ (ТЗ §4.1); «гарантия» вне MVP, но в списке есть.
export const LIFECYCLE_STAGES = [
  "land_purchase",
  "concept",
  "design",
  "permits",
  "site_preparation",
  "construction",
  "commissioning",
  "handover",
  "warranty",
] as const;
export type LifecycleStage = (typeof LIFECYCLE_STAGES)[number];

export const NODE_TYPES = ["stage", "building", "section", "floor", "room"] as const;
export type NodeType = (typeof NODE_TYPES)[number];

// Допустимые дочерние типы (решение PO); те же правила проверяет БД, здесь они только сужают выбор.
export const ALLOWED_CHILDREN: Record<NodeType | "project", readonly NodeType[]> = {
  project: ["stage", "building"],
  stage: ["building", "section"],
  building: ["section", "floor"],
  section: ["floor"],
  floor: ["room"],
  room: [],
};

export function allowedChildren(parent: NodeType | null): readonly NodeType[] {
  return ALLOWED_CHILDREN[parent ?? "project"];
}

export function isNodeType(value: string): value is NodeType {
  return (NODE_TYPES as readonly string[]).includes(value);
}

export function isStage(value: string | null): value is LifecycleStage {
  return value !== null && (LIFECYCLE_STAGES as readonly string[]).includes(value);
}

export function isStatus(value: string): value is ProjectStatus {
  return (PROJECT_STATUSES as readonly string[]).includes(value);
}

// Поля после legal_entity_id внешним ролям приходят пустыми: их колонки закрыты для портала.
export type Project = {
  id: string;
  name: string;
  status: string;
  legal_entity_id: string;
  lifecycle_stage: string | null;
  planned_start: string | null;
  planned_end: string | null;
  planned_budget: string | null;
  budget_currency: string | null;
  customer_id: string | null;
  customer_name: string | null;
  address: string | null;
  latitude: string | null;
  longitude: string | null;
  warranty_until: string | null;
  node_count: number | null;
};

export type TreeNode = {
  id: string;
  project_id: string;
  parent_id: string | null;
  type: string;
  name: string;
  code: string | null;
  sort_order: number;
  path: string[];
  planned_start: string | null;
  planned_end: string | null;
  polygon: number[][] | null;
  foreman_user_id: string | null;
  master_user_id: string | null;
};

export type NodeDetail = TreeNode & {
  foreman_name: string | null;
  master_name: string | null;
};

export type Work = {
  id: string;
  code: string;
  name: string;
  unit: string;
  category: string;
};

export type NamedRef = { id: string; name: string };
export type UserRef = { id: string; full_name: string; role: string };

// Коды ошибок API объектов, для которых есть текст в сообщениях.
export const OBJECTS_ERRORS = [
  "node_type_not_allowed",
  "node_cycle",
  "node_parent_invalid",
  "node_has_dependents",
  "node_code_taken",
  "project_has_nodes",
  "customer_invalid",
  "unknown_legal_entity",
  "work_code_taken",
  "polygon_invalid",
  "dates_invalid",
  "coordinates_invalid",
  "budget_invalid",
  "foreman_invalid",
  "master_invalid",
] as const;
