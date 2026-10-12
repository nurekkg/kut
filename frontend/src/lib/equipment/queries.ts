"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { collectPages } from "@/lib/paging";
import { API } from "@/lib/projects/keys";
import { LEGAL_ENTITIES_REF_KEY, PROJECTS_KEY, treeKey } from "@/lib/projects/keys";
import type { Project, TreeNode } from "@/lib/projects/types";

import {
  assignmentsKey,
  batchesKey,
  calendarKey,
  contractsKey,
  counterpartiesKey,
  maintenanceKey,
  operatorsKey,
  ownRatesKey,
  ratesKey,
  requestsKey,
  typesKey,
  unitKey,
  unitsKey,
} from "./keys";
import type {
  AssignmentRow,
  BatchRow,
  CalendarPayload,
  ContractRow,
  CounterpartyRef,
  MaintenanceRow,
  NamedRef,
  OperatorRow,
  OwnRateRow,
  RateRow,
  RequestRow,
  TypeRow,
  UnitCard,
  UnitListItem,
} from "./types";

export const EQUIPMENT_API = `${API}/equipment`;
const PAGE = 500;

export type UnitQuery = {
  typeId?: string;
  ownership?: string;
  q?: string;
  status?: string;
  projectId?: string;
};

export function useEquipmentTypes() {
  return useQuery({
    queryKey: typesKey,
    queryFn: () => api.get<TypeRow[]>(`${EQUIPMENT_API}/types`),
    staleTime: 300_000,
  });
}

export function useUnits(query: UnitQuery) {
  return useQuery({
    queryKey: unitsKey({
      type: query.typeId,
      ownership: query.ownership,
      q: query.q,
      status: query.status,
      project: query.projectId,
    }),
    queryFn: () =>
      collectPages<UnitListItem>(
        (offset) =>
          api.get<UnitListItem[]>(`${EQUIPMENT_API}/units`, {
            type_id: query.typeId,
            ownership: query.ownership,
            q: query.q,
            status: query.status,
            project_id: query.projectId,
            limit: PAGE,
            offset,
          }),
        PAGE,
      ),
    staleTime: 20_000,
  });
}

export function useUnitCard(unitId: string) {
  return useQuery({
    queryKey: unitKey(unitId),
    queryFn: () => api.get<UnitCard>(`${EQUIPMENT_API}/units/${unitId}`),
  });
}

export function useAssignments(scope: { unitId?: string; projectId?: string }) {
  return useQuery({
    queryKey: assignmentsKey({ unit: scope.unitId, project: scope.projectId }),
    queryFn: () =>
      collectPages<AssignmentRow>(
        (offset) =>
          api.get<AssignmentRow[]>(`${EQUIPMENT_API}/assignments`, {
            unit_id: scope.unitId,
            project_id: scope.projectId,
            limit: PAGE,
            offset,
          }),
        PAGE,
      ),
    staleTime: 15_000,
  });
}

export function useRequests(scope: { projectId?: string; status?: string }) {
  return useQuery({
    queryKey: requestsKey({ project: scope.projectId, status: scope.status }),
    queryFn: () =>
      collectPages<RequestRow>(
        (offset) =>
          api.get<RequestRow[]>(`${EQUIPMENT_API}/requests`, {
            project_id: scope.projectId,
            status: scope.status,
            limit: PAGE,
            offset,
          }),
        PAGE,
      ),
    staleTime: 15_000,
  });
}

export function useMaintenance(status: string) {
  return useQuery({
    queryKey: maintenanceKey(status),
    queryFn: () =>
      collectPages<MaintenanceRow>(
        (offset) =>
          api.get<MaintenanceRow[]>(`${EQUIPMENT_API}/maintenance`, {
            status,
            limit: PAGE,
            offset,
          }),
        PAGE,
      ),
    staleTime: 15_000,
  });
}

export function useCalendar(dateFrom: string, dateTo: string, typeId: string | undefined) {
  return useQuery({
    queryKey: calendarKey(dateFrom, dateTo, typeId),
    queryFn: () =>
      api.get<CalendarPayload>(`${EQUIPMENT_API}/calendar`, {
        date_from: dateFrom,
        date_to: dateTo,
        type_id: typeId,
        limit: 2000,
      }),
    staleTime: 15_000,
  });
}

export function useContracts() {
  return useQuery({
    queryKey: contractsKey,
    queryFn: () => api.get<ContractRow[]>(`${EQUIPMENT_API}/contracts`),
  });
}

export function useRates(contractId: string | null) {
  return useQuery({
    queryKey: ratesKey(contractId ?? "none"),
    queryFn: () => api.get<RateRow[]>(`${EQUIPMENT_API}/contracts/${contractId}/rates`),
    enabled: contractId !== null,
  });
}

export function useOwnRates(legalEntityId: string, typeId: string, enabled: boolean) {
  return useQuery({
    queryKey: ownRatesKey(legalEntityId, typeId),
    queryFn: () =>
      api.get<OwnRateRow[]>(`${EQUIPMENT_API}/own-rates`, {
        legal_entity_id: legalEntityId,
        type_id: typeId,
      }),
    enabled,
  });
}

export function useOperators() {
  return useQuery({
    queryKey: operatorsKey,
    queryFn: () => api.get<OperatorRow[]>(`${EQUIPMENT_API}/operators`),
    staleTime: 60_000,
  });
}

export function useCounterparties(enabled = true) {
  return useQuery({
    queryKey: counterpartiesKey,
    queryFn: () => api.get<CounterpartyRef[]>(`${EQUIPMENT_API}/counterparties`),
    staleTime: 60_000,
    enabled,
  });
}

export function useImportBatches() {
  return useQuery({
    queryKey: batchesKey,
    queryFn: () => api.get<BatchRow[]>(`${EQUIPMENT_API}/import/batches`, { limit: 20 }),
  });
}

export function useLegalEntities() {
  return useQuery({
    queryKey: LEGAL_ENTITIES_REF_KEY,
    queryFn: () => api.get<NamedRef[]>(`${API}/legal-entities`),
    staleTime: 60_000,
  });
}

export function useVisibleProjects(enabled = true) {
  return useQuery({
    queryKey: PROJECTS_KEY,
    queryFn: () => api.get<Project[]>(`${API}/projects`),
    staleTime: 60_000,
    enabled,
  });
}

export function useProjectNodes(projectId: string | null) {
  return useQuery({
    queryKey: treeKey(projectId ?? "none"),
    queryFn: () => api.get<TreeNode[]>(`${API}/projects/${projectId}/tree`),
    enabled: projectId !== null,
    staleTime: 60_000,
  });
}
