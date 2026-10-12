import { addDays } from "../schedule/dates";

import type { DocumentKind, DocumentRow, MaintenanceRow, UnitStatus } from "./types";

// Как на сервере (service.py: SOON_DAYS): документ истекает в ближайшие 30 дней.
export const SOON_DAYS = 30;
// Как на сервере (BLOCKING_KINDS): просроченные ТО и страховка блокируют назначение.
export const BLOCKING_DOCUMENT_KINDS: readonly DocumentKind[] = ["technical_inspection", "insurance"];

export type DocumentState = "expired" | "expiring" | "valid";

export function documentState(validUntil: string, today: string): DocumentState {
  if (validUntil < today) return "expired";
  if (validUntil <= addDays(today, SOON_DAYS)) return "expiring";
  return "valid";
}

// Последняя версия каждого вида документа: старые строки истории сюда не попадают.
export function latestDocuments(rows: DocumentRow[]): DocumentRow[] {
  const byKind = new Map<DocumentKind, DocumentRow>();
  for (const row of rows) {
    const current = byKind.get(row.kind);
    if (!current || row.version > current.version) byKind.set(row.kind, row);
  }
  return [...byKind.values()].sort((a, b) => a.kind.localeCompare(b.kind));
}

export type UnitTone = "ok" | "info" | "warn" | "bad";

export function unitStatusTone(status: UnitStatus): UnitTone {
  switch (status) {
    case "available":
      return "ok";
    case "assigned":
      return "info";
    case "day_full":
    case "doc_warning":
      return "warn";
    case "in_repair":
    case "doc_expired":
      return "bad";
  }
}

export type MaintenanceBucket = "overdue" | "inRepair" | "upcoming" | "later";

// Три раздела экрана ТО: просрочено, в ремонте сейчас, ближайшие 14 дней. Остальное - «позже».
export function maintenanceBucket(row: MaintenanceRow, today: string): MaintenanceBucket {
  const open = row.status === "planned" || row.status === "in_progress";
  if (!open) return "later";
  if (row.planned_to !== null && row.planned_to < today) return "overdue";
  if (row.status === "in_progress") return "inRepair";
  if (row.planned_from <= addDays(today, 14)) return "upcoming";
  return "later";
}
