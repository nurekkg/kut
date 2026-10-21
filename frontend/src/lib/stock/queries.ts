"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, newIdempotencyKey } from "@/lib/api";
import { apiFetch } from "@/lib/auth/session";
import { API } from "@/lib/projects/keys";

import type {
  BalanceOut,
  BatchBalanceOut,
  BatchOut,
  ImportReport,
  MaterialOut,
  MovementOut,
  PostOut,
  ReverseOut,
  WarehouseOut,
} from "./types";

export const MATERIALS_KEY = ["materials"] as const;
export const STOCK_KEY = ["stock"] as const;

// Все записи склада сбрасывают остатки, журнал и справочник разом: они связаны.
function useStockInvalidate() {
  const queryClient = useQueryClient();
  return () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: STOCK_KEY }),
      queryClient.invalidateQueries({ queryKey: MATERIALS_KEY }),
    ]);
}

export function useMaterials(legalEntityId: string | null, activeOnly = true) {
  return useQuery({
    queryKey: [...MATERIALS_KEY, "list", legalEntityId, activeOnly],
    enabled: legalEntityId !== null,
    queryFn: () =>
      api.get<MaterialOut[]>(`${API}/materials`, {
        legal_entity_id: legalEntityId ?? undefined,
        active: activeOnly ? true : undefined,
      }),
    placeholderData: keepPreviousData,
  });
}

export function useMaterial(materialId: string) {
  return useQuery({
    queryKey: [...MATERIALS_KEY, "item", materialId],
    queryFn: () => api.get<MaterialOut>(`${API}/materials/${materialId}`),
  });
}

export function useWarehouses(projectId: string | null) {
  return useQuery({
    queryKey: [...STOCK_KEY, "warehouses", projectId],
    enabled: projectId !== null,
    queryFn: () => api.get<WarehouseOut[]>(`${API}/warehouses`, { project_id: projectId ?? undefined }),
  });
}

export function useBalances(warehouseId: string | null) {
  return useQuery({
    queryKey: [...STOCK_KEY, "balances", warehouseId],
    enabled: warehouseId !== null,
    queryFn: () => api.get<BalanceOut[]>(`${API}/stock/balances`, { warehouse_id: warehouseId ?? undefined }),
  });
}

export function useBatchBalances(warehouseId: string | null) {
  return useQuery({
    queryKey: [...STOCK_KEY, "batch-balances", warehouseId],
    enabled: warehouseId !== null,
    queryFn: () =>
      api.get<BatchBalanceOut[]>(`${API}/stock/balances`, {
        warehouse_id: warehouseId ?? undefined,
        by_batch: true,
      }),
  });
}

export function useMovements(warehouseId: string | null) {
  return useQuery({
    queryKey: [...STOCK_KEY, "movements", warehouseId],
    enabled: warehouseId !== null,
    queryFn: () =>
      api.get<MovementOut[]>(`${API}/stock/movements`, { warehouse_id: warehouseId ?? undefined, limit: 200 }),
  });
}

export function useReverseMovement() {
  const invalidate = useStockInvalidate();
  return useMutation({
    mutationFn: (movementId: string) =>
      api.post<ReverseOut>(`${API}/stock/movements/${movementId}/reverse`),
    onSuccess: invalidate,
  });
}

export type BatchBody = {
  batch_no: string;
  certificate_document_id: string | null;
  customs_document_id: string | null;
  country_of_origin: string | null;
};

export function useCreateBatch(materialId: string) {
  const invalidate = useStockInvalidate();
  return useMutation({
    mutationFn: (body: BatchBody) =>
      api.post<BatchOut>(`${API}/materials/${materialId}/batches`, body),
    onSuccess: invalidate,
  });
}

export function usePostWaybill() {
  const invalidate = useStockInvalidate();
  return useMutation({
    mutationFn: ({ documentId, versionId }: { documentId: string; versionId: string }) =>
      api.post<PostOut>(`${API}/stock/waybills/${documentId}/versions/${versionId}/post`),
    onSuccess: invalidate,
  });
}

// Импорт XLSX/CSV: файл уходит телом запроса (сервер читает сырое тело, без multipart).
// dryRun=true - только проверка: ошибки по строкам в ответе 422 (detail.issues), записи нет.
export async function importMaterials(
  legalEntityId: string,
  file: File,
  dryRun: boolean,
): Promise<ImportReport> {
  const params = new URLSearchParams({
    legal_entity_id: legalEntityId,
    filename: file.name,
    dry_run: String(dryRun),
  });
  return apiFetch<ImportReport>(`${API}/materials/import?${params.toString()}`, {
    method: "POST",
    headers: { "Content-Type": "application/octet-stream" },
    body: file,
  });
}

export function newStockKey(): string {
  return newIdempotencyKey();
}
