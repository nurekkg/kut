"use client";

import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { apiFetch } from "@/lib/auth/session";
import { API } from "@/lib/projects/keys";

import { putObject, type VersionTransport } from "./upload";
import type {
  CounterpartyOut,
  CreateBody,
  DocumentDetail,
  DocumentOut,
  DownloadOut,
  EventOut,
  KindOut,
  LinesOut,
  PresignOut,
  PriceDraft,
  VersionOut,
  ApprovalQueueItem,
  DecisionBody,
  RouteOut,
  RouteTemplate,
  SignedScanBody,
} from "./types";

export const DOCUMENTS_API = `${API}/documents`;
// Все ключи модуля начинаются с "documents": после любой записи сбрасывается всё сразу.
export const DOCUMENTS_KEY = ["documents"] as const;

export type DocumentFilters = {
  projectId?: string;
  kind?: string;
  status?: string;
  counterpartyId?: string;
  dateFrom?: string;
  dateTo?: string;
  q?: string;
  equipmentUnitId?: string;
  projectNodeId?: string;
};

export type Page = { limit: number; offset: number };

// Пустые значения api.get не отправляет, а экспорт собирает строку сам: поэтому параметры общие.
export function filterParams(filters: DocumentFilters): Record<string, string | undefined> {
  return {
    project_id: filters.projectId,
    kind: filters.kind,
    status: filters.status,
    counterparty_id: filters.counterpartyId,
    date_from: filters.dateFrom,
    date_to: filters.dateTo,
    q: filters.q,
    equipment_unit_id: filters.equipmentUnitId,
    project_node_id: filters.projectNodeId,
  };
}

export function exportPath(filters: DocumentFilters): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(filterParams(filters))) {
    if (value) search.set(key, value);
  }
  const text = search.toString();
  return text ? `${DOCUMENTS_API}/export.xlsx?${text}` : `${DOCUMENTS_API}/export.xlsx`;
}

export function useDocumentKinds(enabled = true) {
  return useQuery({
    queryKey: [...DOCUMENTS_KEY, "kinds"],
    queryFn: () => api.get<KindOut[]>(`${DOCUMENTS_API}/kinds`),
    staleTime: 300_000,
    enabled,
  });
}

// Серверная пагинация через offset: total сервер не отдаёт, поэтому страница заполнена - значит, есть следующая.
export function useDocuments(filters: DocumentFilters, page: Page, enabled = true) {
  return useQuery({
    queryKey: [...DOCUMENTS_KEY, "list", filters, page],
    queryFn: () =>
      api.get<DocumentOut[]>(DOCUMENTS_API, { ...filterParams(filters), limit: page.limit, offset: page.offset }),
    placeholderData: keepPreviousData,
    enabled,
  });
}

// Контрагенты для выбора: только id и название, не больше 50 совпадений; уточняется поиском q.
export function useDocumentCounterparties(search: string, enabled = true) {
  return useQuery({
    queryKey: [...DOCUMENTS_KEY, "counterparties", search],
    queryFn: () => api.get<CounterpartyOut[]>(`${DOCUMENTS_API}/counterparties`, { q: search || undefined }),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
    enabled,
  });
}

export function useDocument(id: string) {
  return useQuery({
    queryKey: [...DOCUMENTS_KEY, "detail", id],
    queryFn: () => api.get<DocumentDetail>(`${DOCUMENTS_API}/${id}`),
  });
}

export function useDocumentEvents(id: string, enabled: boolean) {
  return useQuery({
    queryKey: [...DOCUMENTS_KEY, "events", id],
    queryFn: () => api.get<EventOut[]>(`${DOCUMENTS_API}/${id}/events`),
    enabled,
  });
}

export function useInvalidateDocuments(): () => Promise<void> {
  const client = useQueryClient();
  return () => client.invalidateQueries({ queryKey: DOCUMENTS_KEY });
}

export const documentsApi = {
  create: (body: CreateBody, key: string) => api.post<DocumentDetail>(DOCUMENTS_API, body, key),
  archive: (id: string) => api.post<void>(`${DOCUMENTS_API}/${id}/archive`),
  setVersionAmount: (id: string, versionId: string, amount: string) =>
    api.put<void>(`${DOCUMENTS_API}/${id}/versions/${versionId}/amount`, { amount }),
  downloadUrl: (id: string, versionId: string) =>
    api.get<DownloadOut>(`${DOCUMENTS_API}/${id}/versions/${versionId}/download-url`),
  putLines: (id: string, versionId: string, lines: LineBody[], key: string) =>
    api.put<LinesOut>(`${DOCUMENTS_API}/${id}/versions/${versionId}/lines`, { lines }, key),
  putPrices: (id: string, versionId: string, prices: PriceDraft[], key: string) =>
    api.put<LinesOut>(`${DOCUMENTS_API}/${id}/versions/${versionId}/line-prices`, { prices }, key),
  importLines: async (id: string, versionId: string, file: File, key: string) =>
    apiFetch<LinesOut>(`${DOCUMENTS_API}/${id}/versions/${versionId}/lines/import`, {
      method: "POST",
      headers: { "Content-Type": file.type || "application/octet-stream", "Idempotency-Key": key },
      body: await file.arrayBuffer(),
    }),
  sendRoute: (id: string, versionId: string, key: string) =>
    api.post<RouteOut>(`${DOCUMENTS_API}/${id}/versions/${versionId}/send`, undefined, key),
  decide: (id: string, versionId: string, body: DecisionBody, key: string) =>
    api.post<RouteOut>(`${DOCUMENTS_API}/${id}/versions/${versionId}/decisions`, body, key),
  saveRouteTemplate: (code: string, roles: string[]) =>
    api.put<RouteTemplate>(`${DOCUMENTS_API}/route-templates/${code}`, { roles }),
  signedScan: (id: string, versionId: string, body: SignedScanBody, key: string) =>
    api.post<VersionOut>(`${DOCUMENTS_API}/${id}/versions/${versionId}/signed`, body, key),
};

// Тело позиции: номер позиции (null - новая), связи с работой и узлом сохраняются как пришли.
export type LineBody = {
  position_no: number | null;
  name: string;
  unit: string;
  quantity: string;
  work_id: string | null;
  project_node_id: string | null;
};

export function useVersionLines(documentId: string, versionId: string, enabled: boolean) {
  return useQuery({
    queryKey: [...DOCUMENTS_KEY, "lines", documentId, versionId],
    queryFn: () => api.get<LinesOut>(`${DOCUMENTS_API}/${documentId}/versions/${versionId}/lines`),
    enabled,
  });
}

export function versionTransport(documentId: string): VersionTransport {
  return {
    presign: (body) => api.post<PresignOut>(`${DOCUMENTS_API}/${documentId}/versions/presign`, body),
    confirm: (body, key) =>
      api.post<VersionOut>(`${DOCUMENTS_API}/${documentId}/versions/confirm`, body, key),
    put: putObject,
  };
}

export function useVersionRoute(documentId: string, versionId: string, enabled: boolean) {
  return useQuery({
    queryKey: [...DOCUMENTS_KEY, "route", documentId, versionId],
    queryFn: () => api.get<RouteOut>(`${DOCUMENTS_API}/${documentId}/versions/${versionId}/route`),
    enabled,
  });
}

// Очередь согласования: документы, где текущий шаг мой. Общий кэш с счётчиком в навигации.
export function useDocumentApprovals(enabled: boolean) {
  return useQuery({
    queryKey: [...DOCUMENTS_KEY, "approvals"],
    queryFn: () => api.get<ApprovalQueueItem[]>(`${DOCUMENTS_API}/approvals`),
    enabled,
    staleTime: 15_000,
    refetchInterval: 60_000,
  });
}

export function useRouteTemplates(enabled: boolean) {
  return useQuery({
    queryKey: [...DOCUMENTS_KEY, "route-templates"],
    queryFn: () => api.get<RouteTemplate[]>(`${DOCUMENTS_API}/route-templates`),
    enabled,
  });
}
