// Десятичные числа API приходят строками ("12.500"): точность сохраняется, в число их переводят
// только для показа и расчёта геометрии.
export const ITEM_STATES = [
  "done",
  "in_progress",
  "not_started",
  "late_start",
  "overdue",
  "overdue_no_fact",
] as const;
export type ItemState = (typeof ITEM_STATES)[number];

export function isItemState(value: string): value is ItemState {
  return (ITEM_STATES as readonly string[]).includes(value);
}

export type ItemProgress = {
  weight: string;
  confirmed_volume: string;
  percent: string;
  planned_percent: string;
  delay_days: string;
  state: string;
  overdue_days: number;
  first_fact_date: string | null;
  last_fact_date: string | null;
};

export type ScheduleItem = {
  id: string;
  project_id: string;
  node_id: string;
  work_id: string;
  work_code: string;
  work_name: string;
  unit: string;
  plan_start: string;
  plan_end: string;
  plan_volume: string;
  weight: string | null;
  responsible_user_id: string | null;
  responsible_name: string | null;
  duration_days: number;
  progress: ItemProgress | null;
};

export type Dependency = {
  id: string;
  project_id: string;
  predecessor_id: string;
  successor_id: string;
};

export type Aggregate = {
  items: number;
  weight: string;
  percent: string;
  planned_percent: string;
  deviation: string;
  delay_days: string;
  completed: number;
  overdue: number;
  overdue_no_fact: number;
};

export type NodeReadiness = {
  node_id: string;
  parent_id: string | null;
  own: Aggregate | null;
  subtree: Aggregate;
};

export type ProjectReadiness = {
  as_of: string;
  project: Aggregate | null;
  nodes: NodeReadiness[];
};

export type NodeItemProgress = {
  item_id: string;
  node_id: string;
  work_id: string;
  work_code: string | null;
  work_name: string | null;
  unit: string | null;
  plan_start: string;
  plan_end: string;
  plan_volume: string;
  weight: string;
  confirmed_volume: string;
  overrun_volume: string;
  percent: string;
  planned_percent: string;
  delay_days: string;
  state: string;
  overdue_days: number;
};

export type UnplannedVolume = {
  work_id: string;
  work_name: string | null;
  unit: string | null;
  volume: string;
  facts: number;
};

export type NodeReadinessDetail = {
  as_of: string;
  node_id: string;
  own: Aggregate | null;
  subtree: Aggregate | null;
  children: NodeReadiness[];
  items: NodeItemProgress[];
  unplanned: UnplannedVolume[];
};

// Коды ошибок ГПР, факта и фото, для которых в сообщениях есть текст (FieldErrors).
export const FIELD_ERRORS = [
  "schedule_node_invalid",
  "schedule_work_invalid",
  "schedule_item_has_dependents",
  "schedule_item_exists",
  "dependency_cycle",
  "dependency_item_invalid",
  "dependency_exists",
  "actual_transition_forbidden",
  "actual_locked",
  "actual_date_in_future",
  "actual_reason_required",
  "actual_node_invalid",
  "actual_work_invalid",
  "actual_item_invalid",
  "actual_has_photos",
  "photo_not_uploaded",
  "photo_invalid",
  "photo_key_invalid",
  "photo_already_confirmed",
  "storage_unavailable",
] as const;
