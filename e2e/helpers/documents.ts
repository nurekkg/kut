import { createHash } from "node:crypto";

import { expect, type APIRequestContext } from "@playwright/test";

import { post, type Session } from "./api";

// Документы через API для сценариев, где важен результат, а не путь в интерфейсе.
export const DOCUMENTS = "/api/v1/documents";

export type VersionRow = {
  id: string;
  version_no: number;
  status: string;
  content_type: string;
  size_bytes: number;
  author_name: string | null;
  amount: string | null;
};

export type DocumentRow = {
  id: string;
  kind: string;
  title: string;
  financial: boolean;
  registration_no: string | null;
  legal_entity_name: string | null;
  amount: string | null;
  amount_version_no: number | null;
  effective_version_no: number | null;
  latest_version_no: number | null;
  latest_status: string | null;
  current_version: VersionRow | null;
  versions: VersionRow[];
};

export function sha256Base64(content: Buffer): string {
  return createHash("sha256").update(content).digest("base64");
}

export async function createDocument(
  request: APIRequestContext,
  session: Session,
  body: Record<string, unknown>,
): Promise<DocumentRow> {
  return post<DocumentRow>(request, session, DOCUMENTS, body);
}

// Загрузка как в браузере: presign с контрольной суммой, PUT напрямую в S3, подтверждение. Сумма версии -
// в подтверждении (R1); без неё поле уходит пустым.
export async function uploadVersion(
  request: APIRequestContext,
  session: Session,
  documentId: string,
  content: Buffer,
  contentType: string,
  amount?: string,
): Promise<VersionRow> {
  const presigned = await post<{ key: string; upload_url: string; headers: Record<string, string> }>(
    request,
    session,
    `${DOCUMENTS}/${documentId}/versions/presign`,
    { content_type: contentType, size_bytes: content.length, checksum_sha256: sha256Base64(content) },
  );
  const stored = await request.put(presigned.upload_url, { data: content, headers: presigned.headers });
  expect(stored.ok(), `PUT в хранилище: ${stored.status()}`).toBeTruthy();
  return post<VersionRow>(request, session, `${DOCUMENTS}/${documentId}/versions/confirm`, {
    key: presigned.key,
    amount: amount ?? null,
  });
}

// Сумма версии правится только у черновика: ответ отдаём целиком, чтобы сценарий сверил отказ.
export async function setVersionAmount(
  request: APIRequestContext,
  session: Session,
  documentId: string,
  versionId: string,
  amount: string,
) {
  return request.put(`${DOCUMENTS}/${documentId}/versions/${versionId}/amount`, {
    headers: { Authorization: `Bearer ${session.token}` },
    data: { amount },
  });
}

export async function documentById(
  request: APIRequestContext,
  session: Session,
  documentId: string,
): Promise<DocumentRow> {
  const response = await request.get(`${DOCUMENTS}/${documentId}`, {
    headers: { Authorization: `Bearer ${session.token}` },
  });
  expect(response.ok(), `GET документа: ${response.status()}`).toBeTruthy();
  return (await response.json()) as DocumentRow;
}
