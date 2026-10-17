import { ApiError } from "../auth/api";

import type { ConfirmBody, PresignBody, PresignOut, VersionOut } from "./types";

// Лимит и типы те же, что на сервере (backend/app/modules/documents/types.py).
export const UPLOAD_MAX_BYTES = 30 * 1024 * 1024;
export const UPLOAD_ACCEPT = ".pdf,.xlsx,.docx,.jpg,.jpeg,.png,.heic,.webp";

const CONTENT_TYPES = new Map<string, string>([
  ["pdf", "application/pdf"],
  ["xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"],
  ["docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
  ["jpg", "image/jpeg"],
  ["jpeg", "image/jpeg"],
  ["png", "image/png"],
  ["heic", "image/heic"],
  ["webp", "image/webp"],
]);

// Тип берём из расширения: сервер сверяет его с сигнатурой файла. MIME браузера не годится,
// Windows отдаёт для HEIC пустой тип.
export function contentTypeOf(fileName: string): string | null {
  const dot = fileName.lastIndexOf(".");
  if (dot < 0) return null;
  return CONTENT_TYPES.get(fileName.slice(dot + 1).toLowerCase()) ?? null;
}

export class UploadError extends Error {
  constructor(readonly code: string) {
    super(code);
    this.name = "UploadError";
  }
}

// Повтор помогает, когда файл или связь не дошли: обрыв сети, 5xx, 408/429, файл ещё не в хранилище.
// Отказ по существу (макросы, тип, размер, активное содержимое) повтором не лечится: нужен другой файл.
export function isRetryable(error: unknown): boolean {
  if (error instanceof UploadError) return error.code === "not_uploaded";
  if (error instanceof ApiError) {
    return (
      error.status >= 500 ||
      error.status === 408 ||
      error.status === 429 ||
      error.code === "document.not_uploaded" ||
      error.code === "request_in_progress"
    );
  }
  return true; // fetch бросает TypeError при обрыве связи
}

// Ссылка presign живёт expires_in секунд; за запас до конца берём новую, а не уходим в отказ на полпути.
const PRESIGN_MARGIN_SECONDS = 30;
const PUT_TIMEOUT_MS = 180_000;

// SHA-256 тела файла в base64: S3 сверит его при записи. crypto.subtle есть только в защищённом контексте.
export async function sha256Base64(file: Blob): Promise<string> {
  if (!globalThis.crypto?.subtle) throw new UploadError("insecure_context");
  const digest = await globalThis.crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return btoa(Array.from(new Uint8Array(digest), (byte) => String.fromCharCode(byte)).join(""));
}

export type IssuedPresign = PresignOut & { issuedAt: number };

// Рабочая копия загрузки создаётся один раз на выбранный файл. Ключ идемпотентности и ссылка S3
// переживают повторы, поэтому после сбоя версия не удваивается.
export type UploadWork = {
  name: string;
  file: Blob;
  contentType: string;
  checksum: string;
  idempotencyKey: string;
  presign: IssuedPresign | null;
  // confirm уже уходил: ответ мог потеряться, версия могла записаться.
  confirmSent: boolean;
};

export async function prepareUpload(file: File, idempotencyKey: string): Promise<UploadWork> {
  const contentType = contentTypeOf(file.name);
  if (contentType === null) throw new UploadError("type_not_allowed");
  if (file.size === 0) throw new UploadError("empty");
  if (file.size > UPLOAD_MAX_BYTES) throw new UploadError("too_large");
  const checksum = await sha256Base64(file);
  return {
    name: file.name,
    file,
    contentType,
    checksum,
    idempotencyKey,
    presign: null,
    confirmSent: false,
  };
}

// Результат PUT: "stored" - записано, "exists" - ключ уже записан прошлой попыткой (412, файл на месте),
// "expired" - ссылка истекла (403).
export type PutResult = "stored" | "exists" | "expired";

// Загрузка в S3 по ссылке presign: заголовки ссылки (тип, контрольная сумма, If-None-Match: *) уходят как
// выданы. Прогресс нужен, поэтому не fetch, а XMLHttpRequest.
export function putObject(
  url: string,
  headers: Record<string, string>,
  body: Blob,
  onProgress?: (loaded: number, total: number) => void,
): Promise<PutResult> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("PUT", url);
    for (const [name, value] of Object.entries(headers)) request.setRequestHeader(name, value);
    request.timeout = PUT_TIMEOUT_MS;
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress?.(event.loaded, event.total);
    };
    request.onload = () => {
      const { status } = request;
      if (status >= 200 && status < 300) resolve("stored");
      else if (status === 412) resolve("exists");
      else if (status === 403) resolve("expired");
      else reject(new UploadError("not_uploaded"));
    };
    request.onerror = () => reject(new UploadError("not_uploaded"));
    request.ontimeout = () => reject(new UploadError("not_uploaded"));
    request.send(body);
  });
}

export type VersionTransport = {
  presign: (body: PresignBody) => Promise<PresignOut>;
  confirm: (body: ConfirmBody, idempotencyKey: string) => Promise<VersionOut>;
  put: typeof putObject;
  // Версия того же содержимого, уже записанная документом (по контрольной сумме SHA-256 в base64).
  find: (checksum: string) => Promise<VersionOut | null>;
};

export type UploadOptions = {
  comment: string | null;
  // Сумма версии (R1): уходит в подтверждение; повтор шлёт то же значение, иначе ключ идемпотентности не сойдётся.
  amount: string | null;
  onProgress?: (loaded: number, total: number) => void;
  now?: () => number;
};

function isUsable(presign: IssuedPresign | null, now: number): presign is IssuedPresign {
  return presign !== null && now < presign.issuedAt + (presign.expires_in - PRESIGN_MARGIN_SECONDS) * 1000;
}

// presign -> PUT -> confirm. Повтор после сбоя берёт ту же ссылку и тот же ключ: PUT на уже записанный
// ключ даёт 412, а confirm с тем же ключом вернёт прежний ответ. Истёкшая ссылка (403) - один новый круг.
// Если confirm уже уходил и ссылка истекла, сначала ищем версию по контрольной сумме: ответ могли потерять,
// и новая загрузка дала бы дубль версии.
export async function uploadVersion(
  work: UploadWork,
  transport: VersionTransport,
  options: UploadOptions,
): Promise<VersionOut> {
  const now = options.now ?? Date.now;
  let stored: IssuedPresign | null = null;
  for (let attempt = 0; attempt < 2 && stored === null; attempt += 1) {
    if (!isUsable(work.presign, now())) {
      if (work.confirmSent) {
        const existing = await transport.find(work.checksum);
        if (existing !== null) return existing;
      }
      const issued = await transport.presign({
        content_type: work.contentType,
        size_bytes: work.file.size,
        checksum_sha256: work.checksum,
      });
      work.presign = { ...issued, issuedAt: now() };
    }
    const current: IssuedPresign = work.presign;
    const result = await transport.put(current.upload_url, current.headers, work.file, options.onProgress);
    if (result === "expired") work.presign = null;
    else stored = current;
  }
  if (stored === null) throw new UploadError("not_uploaded");
  work.confirmSent = true;
  return transport.confirm(
    { key: stored.key, comment: options.comment, amount: options.amount },
    work.idempotencyKey,
  );
}
