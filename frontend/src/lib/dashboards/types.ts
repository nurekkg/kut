// Десятичные числа API приходят строками ("61.96"); в число переводят только для графиков и сортировки.
export type Thresholds = {
  lag_days: number;
  approval_stale_days: number;
  recent_days: number;
  top_n: number;
};

export type Progress = {
  percent: string;
  planned_percent: string;
  deviation: string;
  delay_days: string;
};

// Вид заказчика: только факт. Плана, отклонения и отставания в ответе нет, и тип их не содержит.
export type PortalProgress = { percent: string };

export type Counts = {
  works: number;
  completed: number;
  overdue: number;
  no_fact_started: number;
  problems: number;
};

export type FactsSummary = {
  awaiting_approval: number;
  awaiting_approval_stale: number;
  recent: number;
};

export type SeriesPoint = {
  date: string;
  plan_percent: string;
  fact_percent: string | null;
};

// Ряд заказчика: только факт, без плановых значений и без будущих дат.
export type FactPoint = { date: string; fact_percent: string };

export type NodeTile = {
  node_id: string;
  name: string;
  type: string;
  progress: Progress;
  items: number;
  overdue: number;
};

export type ProblemState =
  | "done"
  | "in_progress"
  | "not_started"
  | "late_start"
  | "overdue"
  | "overdue_no_fact";

export type ProblemWork = {
  item_id: string;
  node_id: string;
  node_name: string;
  work_id: string;
  work_name: string | null;
  unit: string | null;
  plan_start: string;
  plan_end: string;
  plan_volume: string;
  confirmed_volume: string;
  percent: string;
  delay_days: string;
  overdue_days: number;
  state: ProblemState;
};

export type PhotoOut = {
  id: string;
  work_actual_id: string;
  content_type: string;
  size_bytes: number;
  latitude: string | null;
  longitude: string | null;
  taken_at: string | null;
  uploaded_by: string;
  created_at: string;
  url: string | null;
  url_expires_in: number | null;
  uploaded_by_name: string | null;
  node_id: string | null;
  work_name: string | null;
  work_date: string | null;
};

export type Budget = {
  planned_budget: string;
  currency: string;
};

export type ObjectDashboardData = {
  as_of: string;
  project_id: string;
  name: string;
  status: string;
  lifecycle_stage: string;
  planned_start: string | null;
  planned_end: string | null;
  thresholds: Thresholds;
  progress: Progress | null;
  counts: Counts | null;
  facts: FactsSummary | null;
  series: SeriesPoint[];
  nodes: NodeTile[];
  zones: NodeTile[];
  problems: ProblemWork[];
  photos: PhotoOut[] | null;
  budget: Budget | null;
};

export type PortalDashboardData = {
  as_of: string;
  project_id: string;
  name: string;
  status: string;
  progress: PortalProgress | null;
  series: FactPoint[];
};

export type GroupRow = {
  project_id: string;
  name: string;
  status: string;
  lifecycle_stage: string;
  progress: Progress | null;
  items: number;
  problems: number;
  awaiting_approval: number;
  budget: Budget | null;
};

export type GroupDashboardData = {
  as_of: string;
  thresholds: Thresholds;
  rows: GroupRow[];
};
