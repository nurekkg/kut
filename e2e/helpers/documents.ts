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
};

export type DocumentRow = {
  id: string;
  kind: string;
  title: string;
  financial: boolean;
  registration_no: string | null;
  amount: string | null;
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

// Загрузка как в браузере: presign с контрольной суммой, PUT напрямую в S3, подтверждение.
export async function uploadVersion(
  request: APIRequestContext,
  session: Session,
  documentId: string,
  content: Buffer,
  contentType: string,
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
