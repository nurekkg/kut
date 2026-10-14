// Очередь машиниста (SE-2): лист, заправка и чек уходят на сервер по шагам; каждый шаг сохраняется в
// IndexedDB, чтобы повтор не создавал дубль (ключи Idempotency-Key создаются до первого запроса).

export type ReceiptDraft = {
  blob: Blob;
  sizeBytes: number;
  contentType: "image/jpeg";
  key: string | null;
  uploadUrl: string | null;
  headers: Record<string, string>;
  presignedAt: number | null;
  expiresIn: number;
  uploaded: boolean;
  confirmed: boolean;
};

export type FuelDraft = {
  liters: string | null;
  amount: string;
  key: string;
  fuelId: string | null;
  receipt: ReceiptDraft | null;
};

export type ShiftDraft = {
  equipment_unit_id: string;
  project_id: string;
  work_date: string;
  operator_id: string;
  hours: string;
  odometer_from: string | null;
  odometer_to: string | null;
  trips: number | null;
  tonnes: string | null;
  downtime_hours: string;
  downtime_reason: string | null;
};

export type ShiftPatchDraft = Partial<{
  hours: string;
  odometer_from: string | null;
  odometer_to: string | null;
  trips: number | null;
  tonnes: string | null;
  downtime_hours: string;
  downtime_reason: string | null;
}>;

export type ShiftEntry = {
  id: string;
  userId: string;
  createdAt: number;
  mode: "create" | "resubmit";
  label: { unit: string; project: string; date: string };
  shift: ShiftDraft;
  createKey: string;
  shiftId: string | null;
  patch: ShiftPatchDraft | null;
  patched: boolean;
  fuel: FuelDraft | null;
  submitted: boolean;
  stage: "pending" | "failed";
  error: string | null;
  attempts: number;
};

// kept: лист уже создан на сервере (запись остаётся в очереди, чтобы довести её до конца).
export type SubmitOutcome =
  | { kind: "sent" }
  | { kind: "queued" }
  | { kind: "failed"; code: string; kept: boolean };
