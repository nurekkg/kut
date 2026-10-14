"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { collectPages } from "@/lib/paging";

import { EQUIPMENT_API } from "./queries";

// Путевые листы, ГСМ, количества и журнал затрат (SE-2): ответы API. Денег в листе и количествах нет;
// суммы заправки и журнал отдаёт API только с read:rates.

export type ShiftStatus = "draft" | "submitted" | "confirmed" | "rejected";

export type ShiftLineRow = {
  id: string;
  node_id: string | null;
  schedule_item_id: string | null;
  hours: string;
  trips: number | null;
  tonnes: string | null;
};

export type ShiftRow = {
  id: string;
  project_id: string;
  equipment_unit_id: string;
  assignment_id: string | null;
  work_date: string;
  operator_id: string;
  hours: string;
  odometer_from: string | null;
  odometer_to: string | null;
  trips: number | null;
  tonnes: string | null;
  downtime_hours: string;
  downtime_reason: string | null;
  source: string;
  status: ShiftStatus;
  reject_reason: string | null;
  confirmed_by: string | null;
  confirmed_at: string | null;
  created_by: string | null;
  posting_version: number;
  lines: ShiftLineRow[];
  unplanned: boolean;
  unit_registry_number: string | null;
};

export type ShiftCreateBody = {
  equipment_unit_id: string;
  project_id: string;
  work_date: string;
  operator_id: string;
  assignment_id?: string | null;
  hours: string;
  odometer_from?: string | null;
  odometer_to?: string | null;
  trips?: number | null;
  tonnes?: string | null;
  downtime_hours?: string;
  downtime_reason?: string | null;
};

export type FuelRow = {
  id: string;
  equipment_unit_id: string;
  project_id: string | null;
  shift_id: string | null;
  fuel_date: string;
  liters: string | null;
  has_receipt: boolean;
  reversed_at: string | null;
  reverse_reason: string | null;
};

export type PresignedUpload = {
  key: string;
  upload_url: string;
  headers: Record<string, string>;
  expires_in: number;
};

export type UsageRow = {
  project_id: string;
  equipment_unit_id: string;
  day: string;
  shifts: number;
  hours: string;
  trips: number;
  tonnes: string;
  downtime_hours: string;
  liters: string;
};

export type CostTotalRow = {
  project_id: string | null;
  category: string;
  amount: string;
  entries: number;
};

export type CostEntryRow = {
  id: string;
  legal_entity_id: string;
  project_id: string | null;
  node_id: string | null;
  schedule_item_id: string | null;
  equipment_unit_id: string | null;
  period: string;
  category: string;
  source_type: string;
  source_id: string;
  posting_version: number;
  line_no: number;
  quantity: string | null;
  tariff_unit: string | null;
  rate: string | null;
  amount: string;
  reversal_of_id: string | null;
};

export type DelegateRow = {
  id: string;
  project_id: string;
  delegate_user_id: string;
  date_from: string;
  date_to: string;
  reason: string;
  revoked_at: string | null;
};

export type DelegateCandidate = { user_id: string; full_name: string; role_code: string };

export type QuantityUnit = { tariff_unit: string };

const PAGE = 500;

export type ShiftScope = {
  status?: ShiftStatus;
  projectId?: string;
  unitId?: string;
  dateFrom?: string;
  dateTo?: string;
  // Только листы, которые вызывающий может подтвердить (счётчик «На согласовании»).
  approvable?: boolean;
};

export function shiftsKey(scope: ShiftScope) {
  return [
    "equipment",
    "shifts",
    scope.status ?? "",
    scope.projectId ?? "",
    scope.unitId ?? "",
    scope.dateFrom ?? "",
    scope.dateTo ?? "",
    scope.approvable ? "approvable" : "",
  ] as const;
}

export function useShifts(scope: ShiftScope, enabled = true) {
  return useQuery({
    queryKey: shiftsKey(scope),
    enabled,
    queryFn: () =>
      collectPages<ShiftRow>(
        (offset) =>
          api.get<ShiftRow[]>(`${EQUIPMENT_API}/shifts`, {
            status: scope.status,
            project_id: scope.projectId,
            unit_id: scope.unitId,
            date_from: scope.dateFrom,
            date_to: scope.dateTo,
            approvable: scope.approvable ? true : undefined,
            limit: PAGE,
            offset,
          }),
        PAGE,
      ),
    staleTime: 10_000,
  });
}

export function fuelKey(scope: { projectId?: string; dateFrom: string; dateTo: string }) {
  return ["equipment", "fuel", scope.projectId ?? "", scope.dateFrom, scope.dateTo] as const;
}

export function useFuel(scope: { projectId?: string; dateFrom: string; dateTo: string }) {
  return useQuery({
    queryKey: fuelKey(scope),
    queryFn: () =>
      collectPages<FuelRow>(
        (offset) =>
          api.get<FuelRow[]>(`${EQUIPMENT_API}/fuel`, {
            project_id: scope.projectId,
            date_from: scope.dateFrom,
            date_to: scope.dateTo,
            limit: PAGE,
            offset,
          }),
        PAGE,
      ),
    staleTime: 10_000,
  });
}

export function usageKey(scope: { projectId?: string; dateFrom: string; dateTo: string }) {
  return ["equipment", "usage", scope.projectId ?? "", scope.dateFrom, scope.dateTo] as const;
}

export function useUsage(scope: { projectId?: string; dateFrom: string; dateTo: string }) {
  return useQuery({
    queryKey: usageKey(scope),
    queryFn: () =>
      api.get<UsageRow[]>(`${EQUIPMENT_API}/usage/totals`, {
        project_id: scope.projectId,
        date_from: scope.dateFrom,
        date_to: scope.dateTo,
      }),
    staleTime: 15_000,
  });
}

export function delegatesKey(projectId?: string) {
  return ["equipment", "delegates", projectId ?? ""] as const;
}

// Без объекта - все делегаты, которых видит пользователь (для баннера «вы делегат»).
export function useDelegates(projectId?: string) {
  return useQuery({
    queryKey: delegatesKey(projectId),
    queryFn: () =>
      collectPages<DelegateRow>(
        (offset) =>
          api.get<DelegateRow[]>(`${EQUIPMENT_API}/delegates`, {
            project_id: projectId,
            limit: PAGE,
            offset,
          }),
        PAGE,
      ),
    staleTime: 15_000,
  });
}

export function candidatesKey(projectId: string) {
  return ["equipment", "delegate-candidates", projectId] as const;
}

export function useDelegateCandidates(projectId: string, enabled: boolean) {
  return useQuery({
    queryKey: candidatesKey(projectId),
    enabled,
    queryFn: () =>
      api.get<DelegateCandidate[]>(`${EQUIPMENT_API}/delegate-candidates`, {
        project_id: projectId,
      }),
    staleTime: 60_000,
  });
}

export function costTotalsKey(scope: { projectId?: string; dateFrom: string; dateTo: string }) {
  return ["equipment", "costs", "totals", scope.projectId ?? "", scope.dateFrom, scope.dateTo] as const;
}

export function useCostTotals(scope: { projectId?: string; dateFrom: string; dateTo: string }) {
  return useQuery({
    queryKey: costTotalsKey(scope),
    queryFn: () =>
      api.get<CostTotalRow[]>(`${EQUIPMENT_API}/costs/totals`, {
        project_id: scope.projectId,
        date_from: scope.dateFrom,
        date_to: scope.dateTo,
      }),
    staleTime: 15_000,
  });
}

export type CostEntryScope = {
  projectId?: string;
  unitId?: string;
  category?: string;
  dateFrom: string;
  dateTo: string;
};

export function costEntriesKey(scope: CostEntryScope) {
  return [
    "equipment",
    "costs",
    "entries",
    scope.projectId ?? "",
    scope.unitId ?? "",
    scope.category ?? "",
    scope.dateFrom,
    scope.dateTo,
  ] as const;
}

export function useCostEntries(scope: CostEntryScope) {
  return useQuery({
    queryKey: costEntriesKey(scope),
    queryFn: () =>
      collectPages<CostEntryRow>(
        (offset) =>
          api.get<CostEntryRow[]>(`${EQUIPMENT_API}/costs/entries`, {
            project_id: scope.projectId,
            unit_id: scope.unitId,
            category: scope.category,
            date_from: scope.dateFrom,
            date_to: scope.dateTo,
            limit: PAGE,
            offset,
          }),
        PAGE,
      ),
    staleTime: 10_000,
  });
}

export function quantityUnitKey(unitId: string, operatorId: string, day: string) {
  return ["equipment", "quantity-unit", unitId, operatorId, day] as const;
}

// Что вводить по единице тарифа: только код единицы, ставки в ответе нет.
export function useQuantityUnit(unitId: string | null, operatorId: string | null, day: string) {
  return useQuery({
    queryKey: quantityUnitKey(unitId ?? "", operatorId ?? "", day),
    enabled: unitId !== null && operatorId !== null,
    queryFn: () =>
      api.get<QuantityUnit>(`${EQUIPMENT_API}/quantity-unit`, {
        equipment_unit_id: unitId ?? undefined,
        operator_id: operatorId ?? undefined,
        work_date: day,
      }),
    staleTime: 60_000,
    retry: false,
  });
}
