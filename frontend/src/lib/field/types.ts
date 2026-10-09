export const ACTUAL_STATUSES = ["draft", "submitted", "approved", "rejected"] as const;
export type ActualStatus = (typeof ACTUAL_STATUSES)[number];

export function isActualStatus(value: string): value is ActualStatus {
  return (ACTUAL_STATUSES as readonly string[]).includes(value);
}

// Факт работ. Люди, геометка, причина отклонения и объёмы против плана приходят только внутренним
// ролям; внешним - подтверждённые факты без них (поля null).
export type Actual = {
  id: string;
  project_id: string;
  node_id: string;
  work_id: string;
  work_code: string | null;
  work_name: string | null;
  unit: string | null;
  schedule_item_id: string | null;
  volume: string;
  work_date: string;
  status: string;
  approved_at: string | null;
  created_at: string;
  author_id: string | null;
  author_name: string | null;
  approved_by: string | null;
  approved_by_name: string | null;
  reject_reason: string | null;
  latitude: string | null;
  longitude: string | null;
  accuracy_m: string | null;
  photo_count: number | null;
  item_plan_volume: string | null;
  item_confirmed_volume: string | null;
  unplanned: boolean;
  warnings: string[];
};

export type Photo = {
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

export type DownloadUrl = { url: string; expires_in: number };

export type PresignedUpload = {
  key: string;
  upload_url: string;
  method: "PUT";
  headers: Record<string, string>;
  expires_in: number;
};
