"use client";

import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { API } from "@/lib/projects/keys";

import { putObject, type VersionTransport } from "./upload";
import type {
  CreateBody,
  DocumentDetail,
  DocumentOut,
  DownloadOut,
  EventOut,
  KindOut,
  PresignOut,
  VersionOut,
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
  setAmount: (id: string, amount: string) => api.put<void>(`${DOCUMENTS_API}/${id}/amount`, { amount }),
  downloadUrl: (id: string, versionId: string) =>
    api.get<DownloadOut>(`${DOCUMENTS_API}/${id}/versions/${versionId}/download-url`),
};

// Сервер отдаёт контрольную сумму версии в hex, у клиента она в base64 (как в ChecksumSHA256 ссылки).
function base64ToHex(value: string): string {
  return Array.from(atob(value), (char) => char.charCodeAt(0).toString(16).padStart(2, "0")).join("");
}

export function versionTransport(documentId: string): VersionTransport {
  return {
    presign: (body) => api.post<PresignOut>(`${DOCUMENTS_API}/${documentId}/versions/presign`, body),
    confirm: (body, key) =>
      api.post<VersionOut>(`${DOCUMENTS_API}/${documentId}/versions/confirm`, body, key),
    put: putObject,
    find: async (checksum) => {
      const detail = await api.get<DocumentDetail>(`${DOCUMENTS_API}/${documentId}`);
      const hex = base64ToHex(checksum);
      return detail.versions.find((version) => version.checksum_sha256 === hex) ?? null;
    },
  };
}
