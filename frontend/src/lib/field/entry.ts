import { newIdempotencyKey } from "@/lib/api";

import type { Compressed } from "./image";
import type { FactPayload, OutboxEntry, OutboxLabel, OutboxPhoto } from "./outbox-types";

export function newPhoto(compressed: Compressed, name: string): OutboxPhoto {
  return {
    id: newIdempotencyKey(),
    blob: compressed.blob,
    thumb: compressed.thumb,
    name,
    takenAt: new Date().toISOString(),
    key: null,
    uploadUrl: null,
    headers: null,
    presignedAt: null,
    expiresIn: null,
    uploaded: false,
  };
}

type Common = {
  userId: string;
  projectId: string;
  label: OutboxLabel;
  payload: FactPayload;
  photos: OutboxPhoto[];
};

function base(common: Common): OutboxEntry {
  return {
    id: newIdempotencyKey(),
    userId: common.userId,
    projectId: common.projectId,
    mode: "create",
    createdAt: Date.now(),
    label: common.label,
    payload: common.payload,
    factId: null,
    factStatus: null,
    // Ключи созданы до первого запроса и лежат в очереди: повтор после обрыва получит тот же ответ.
    createKey: newIdempotencyKey(),
    patch: null,
    patched: false,
    photos: common.photos,
    confirmKey: newIdempotencyKey(),
    confirmFingerprint: "",
    confirmed: false,
    submitted: false,
    stage: "pending",
    error: null,
    attempts: 0,
  };
}

export function newFactEntry(common: Common): OutboxEntry {
  return base(common);
}

// Исправление отклонённого факта: правка полей, новые снимки и повторная отправка на проверку.
export function resubmitEntry(
  common: Common & { factId: string; patch: Record<string, string | null> },
): OutboxEntry {
  return {
    ...base(common),
    mode: "resubmit",
    factId: common.factId,
    factStatus: "rejected",
    patch: Object.keys(common.patch).length > 0 ? common.patch : null,
    patched: Object.keys(common.patch).length === 0,
  };
}
