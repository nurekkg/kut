import { useSyncExternalStore } from "react";

import { apiAs, newIdempotencyKey } from "@/lib/api";
import { ApiError } from "@/lib/auth/api";
import { API } from "@/lib/projects/keys";

import { outboxDb } from "./idb";
import type { OutboxEntry, OutboxPhoto, UploadProgress } from "./outbox-types";
import { selectRemovable } from "./retention";
import type { Actual, PresignedUpload } from "./types";

export type OutboxState = {
  ready: boolean;
  entries: OutboxEntry[];
  progress: Record<string, UploadProgress>;
  busy: string | null;
  // Растёт при каждой доставленной записи: экраны по нему обновляют списки с сервера.
  completed: number;
};

const EMPTY: OutboxState = { ready: false, entries: [], progress: {}, busy: null, completed: 0 };

let state: OutboxState = EMPTY;
const listeners = new Set<() => void>();

function publish(patch: Partial<OutboxState>): void {
  state = { ...state, ...patch };
  listeners.forEach((listener) => listener());
}

export function useOutbox(): OutboxState {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    () => state,
    () => EMPTY,
  );
}

// --- классификация ошибок ------------------------------------------------------------------------

class TransientError extends Error {}

// Запись удалена человеком или очисткой (выход, смена пользователя, срок хранения): шаги, идущие в этот
// момент, прекращаются и ничего не записывают обратно в IndexedDB.
class CancelledError extends Error {}
const cancelled = new Set<string>();
const uploads = new Map<string, XMLHttpRequest>();

function ensureLive(entry: OutboxEntry): void {
  if (cancelled.has(entry.id)) throw new CancelledError();
}

type Kind = "transient" | "permanent";

// Обрыв связи, таймаут и ответы 5xx лечатся повтором; отказ сервера по существу (4xx) повтор не
// исправит: запись остаётся в очереди со своей причиной, пока человек её не поправит или не удалит.
function classify(error: unknown): Kind {
  if (error instanceof TransientError) return "transient";
  if (error instanceof ApiError) {
    if (error.status === 401 || error.status >= 500) return "transient";
    if ([408, 425, 429].includes(error.status) || error.code === "request_in_progress") {
      return "transient";
    }
    return "permanent";
  }
  return "transient"; // fetch бросает TypeError при обрыве связи
}

// --- загрузка файла в S3 по выданной ссылке --------------------------------------------------------

type Put = "ok" | "exists" | "expired";

function putFile(
  entryId: string,
  photo: OutboxPhoto,
  onProgress: (loaded: number, total: number) => void,
): Promise<Put> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    uploads.set(entryId, request);
    request.onloadend = () => uploads.delete(entryId);
    request.onabort = () => reject(new CancelledError());
    request.open("PUT", photo.uploadUrl ?? "");
    for (const [name, value] of Object.entries(photo.headers ?? {})) {
      request.setRequestHeader(name, value);
    }
    request.timeout = 180_000;
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded, event.total);
    };
    request.onload = () => {
      if (request.status >= 200 && request.status < 300) resolve("ok");
      // 412: ключ уже записан прошлой попыткой (If-None-Match: *), файл на месте.
      else if (request.status === 412) resolve("exists");
      else if (request.status === 403) resolve("expired");
      else reject(new TransientError(`s3_${request.status}`));
    };
    request.onerror = () => reject(new TransientError("network"));
    request.ontimeout = () => reject(new TransientError("timeout"));
    request.send(photo.blob);
  });
}

function presignIsValid(photo: OutboxPhoto): boolean {
  return (
    photo.key !== null &&
    photo.uploadUrl !== null &&
    photo.presignedAt !== null &&
    photo.expiresIn !== null &&
    Date.now() < photo.presignedAt + (photo.expiresIn - 30) * 1000
  );
}

// --- шаги одной записи -------------------------------------------------------------------------------

function copy(entry: OutboxEntry): OutboxEntry {
  return { ...entry, photos: entry.photos.map((photo) => ({ ...photo })) };
}

async function persist(entry: OutboxEntry): Promise<void> {
  ensureLive(entry);
  await outboxDb.put(entry);
  publish({
    entries: state.entries.map((item) => (item.id === entry.id ? copy(entry) : item)),
  });
}

function reportProgress(entry: OutboxEntry, index: number, loaded: number, total: number): void {
  publish({
    progress: {
      ...state.progress,
      [entry.id]: { photo: index + 1, photos: entry.photos.length, loaded, total },
    },
  });
}

async function uploadPhotos(entry: OutboxEntry): Promise<void> {
  const api = apiAs(entry.userId);
  const base = `${API}/projects/${entry.projectId}`;
  for (let index = 0; index < entry.photos.length; index += 1) {
    const photo = entry.photos[index];
    for (let attempt = 0; attempt < 2 && !photo.uploaded; attempt += 1) {
      ensureLive(entry);
      if (!presignIsValid(photo)) {
        const issued = await api.post<{ items: PresignedUpload[] }>(`${base}/photos/presign`, {
          work_actual_id: entry.factId,
          files: [{ content_type: photo.blob.type || "image/jpeg", size_bytes: photo.blob.size }],
        });
        const item = issued.items[0];
        photo.key = item.key;
        photo.uploadUrl = item.upload_url;
        photo.headers = item.headers;
        photo.presignedAt = Date.now();
        photo.expiresIn = item.expires_in;
        await persist(entry);
      }
      const result = await putFile(entry.id, photo, (loaded, total) =>
        reportProgress(entry, index, loaded, total),
      );
      if (result === "expired") {
        photo.key = null;
        photo.uploadUrl = null;
        continue;
      }
      photo.uploaded = true;
      await persist(entry);
    }
    if (!photo.uploaded) throw new TransientError("presign_expired");
  }
}

// Работает с переданной рабочей копией и обновляет её на месте: что бы ни случилось, копия хранит все
// сделанные шаги. Запросы идут от имени владельца записи, а не того, кто сейчас вошёл на устройстве.
async function runEntry(entry: OutboxEntry): Promise<void> {
  const api = apiAs(entry.userId);
  const base = `${API}/projects/${entry.projectId}`;

  if (entry.mode === "resubmit" && entry.patch && !entry.patched) {
    ensureLive(entry);
    const patched = await api.patch<Actual>(`${base}/work-actuals/${entry.factId}`, entry.patch);
    entry.patched = true;
    entry.factStatus = patched.status;
    await persist(entry);
  }
  if (entry.factId === null) {
    // Черновик: фото добавляются к факту, пока он не отправлен на проверку.
    ensureLive(entry);
    const created = await api.post<Actual>(
      `${base}/work-actuals`,
      { ...entry.payload, submit: false },
      entry.createKey,
    );
    entry.factId = created.id;
    entry.factStatus = created.status;
    await persist(entry);
  }
  if (entry.photos.some((photo) => !photo.uploaded)) {
    await uploadPhotos(entry);
  }
  if (!entry.confirmed && entry.photos.length > 0) {
    ensureLive(entry);
    // Ключ повтора привязан к составу ключей хранилища: другой состав - другой ключ.
    const fingerprint = entry.photos.map((photo) => photo.key).join(",");
    if (entry.confirmFingerprint !== fingerprint) {
      entry.confirmKey = newIdempotencyKey();
      entry.confirmFingerprint = fingerprint;
      await persist(entry);
    }
    await api.post(
      `${base}/photos/confirm`,
      {
        work_actual_id: entry.factId,
        items: entry.photos.map((photo) => ({
          key: photo.key,
          latitude: entry.payload.latitude,
          longitude: entry.payload.longitude,
          taken_at: photo.takenAt,
        })),
      },
      entry.confirmKey,
    );
    entry.confirmed = true;
    await persist(entry);
  }
  // Факт прораба и техдиректора база согласует сразу: отправлять на проверку нечего.
  if (!entry.submitted && entry.factStatus !== "approved" && entry.factStatus !== "submitted") {
    ensureLive(entry);
    await api.post<Actual>(`${base}/work-actuals/${entry.factId}/submit`);
    entry.submitted = true;
    await persist(entry);
  }
}

// --- очередь ----------------------------------------------------------------------------------------

const BASE_DELAY = 5_000;
const MAX_DELAY = 120_000;

let currentUser: string | null = null;
let draining = false;
let timer: number | null = null;
let delay = BASE_DELAY;

function schedule(): void {
  if (timer !== null) window.clearTimeout(timer);
  timer = window.setTimeout(() => {
    timer = null;
    void drain();
  }, delay);
  delay = Math.min(delay * 2, MAX_DELAY);
}

async function loop(): Promise<void> {
  for (;;) {
    const next = state.entries.find(
      (entry) =>
        entry.userId === currentUser && entry.stage === "pending" && !cancelled.has(entry.id),
    );
    if (!next) {
      delay = BASE_DELAY;
      return;
    }
    publish({ busy: next.id });
    const working = copy(next);
    try {
      await runEntry(working);
      await outboxDb.remove(next.id);
      const progress = { ...state.progress };
      delete progress[next.id];
      publish({
        entries: state.entries.filter((entry) => entry.id !== next.id),
        progress,
        busy: null,
        completed: state.completed + 1,
      });
      delay = BASE_DELAY;
    } catch (error) {
      if (error instanceof CancelledError) {
        // Запись удалена на ходу: обратно в IndexedDB её не кладём и в очереди больше не берём.
        publish({ entries: state.entries.filter((entry) => entry.id !== next.id), busy: null });
        continue;
      }
      // Рабочая копия всегда свежее состояния экрана: после выхода пользователя оно очищено, а запись
      // должна сохранить всё, что успели сделать (id факта, загруженные снимки).
      const failed = copy(working);
      failed.attempts += 1;
      if (classify(error) === "permanent") {
        failed.stage = "failed";
        failed.error = error instanceof ApiError ? error.code : "unknown";
        await persist(failed);
        publish({ busy: null });
        continue;
      }
      await persist(failed);
      publish({ busy: null });
      schedule();
      return;
    }
  }
}

let rerun = false;

async function drain(): Promise<void> {
  if (currentUser === null) return;
  if (draining) {
    rerun = true; // запись добавили, пока шёл разбор очереди: после него пройти ещё раз
    return;
  }
  draining = true;
  try {
    do {
      rerun = false;
      // Одна вкладка за раз: две вкладки с общей очередью послали бы запись дважды.
      if ("locks" in navigator) {
        await navigator.locks.request("kut-outbox", { ifAvailable: true }, async (lock) => {
          if (lock) await loop();
        });
      } else {
        await loop();
      }
    } while (rerun);
  } finally {
    draining = false;
  }
}

function onOnline(): void {
  delay = BASE_DELAY;
  void drain();
}

export async function attach(userId: string): Promise<void> {
  currentUser = userId;
  window.addEventListener("online", onOnline);
  await purgeOutbox(userId);
  try {
    const stored = await outboxDb.all();
    publish({
      ready: true,
      entries: stored
        .filter((entry) => entry.userId === userId)
        .sort((a, b) => a.createdAt - b.createdAt),
    });
  } catch {
    publish({ ready: true, entries: [] }); // IndexedDB недоступна (приватный режим): очереди нет
  }
  void drain();
}

export function detach(): void {
  currentUser = null;
  window.removeEventListener("online", onOnline);
  if (timer !== null) window.clearTimeout(timer);
  timer = null;
  publish({ ...EMPTY, completed: state.completed });
}

export async function enqueue(entry: OutboxEntry): Promise<void> {
  await outboxDb.put(entry);
  publish({ entries: [...state.entries, entry] });
  delay = BASE_DELAY;
  void drain();
}

export async function retryEntry(id: string): Promise<void> {
  const entry = state.entries.find((item) => item.id === id);
  if (!entry) return;
  const next = copy(entry);
  next.stage = "pending";
  next.error = null;
  await persist(next);
  delay = BASE_DELAY;
  void drain();
}

async function storedEntries(): Promise<OutboxEntry[]> {
  try {
    return await outboxDb.all();
  } catch {
    return []; // IndexedDB недоступна (приватный режим): очереди нет
  }
}

async function removeEntries(ids: string[]): Promise<void> {
  for (const id of ids) {
    cancelled.add(id);
    uploads.get(id)?.abort();
    try {
      await outboxDb.remove(id);
    } catch {
      // нечего удалять: IndexedDB недоступна
    }
  }
  if (ids.length > 0) {
    const gone = new Set(ids);
    publish({ entries: state.entries.filter((entry) => !gone.has(entry.id)) });
  }
}

export async function discardEntry(id: string): Promise<void> {
  await removeEntries([id]);
}

// Удалить всё неотправленное этого пользователя (выход: «удалить»).
export async function discardAll(userId: string): Promise<void> {
  const ids = new Set(
    [...(await storedEntries()), ...state.entries]
      .filter((entry) => entry.userId === userId)
      .map((entry) => entry.id),
  );
  await removeEntries([...ids]);
}

// Очистка очереди при запуске приложения и входе: записи другого пользователя и записи старше срока
// хранения стираются, в том числе те, что отправляются прямо сейчас.
export async function purgeOutbox(userId: string, now: number = Date.now()): Promise<number> {
  const removable = selectRemovable(await storedEntries(), userId, now);
  await removeEntries(removable.map((entry) => entry.id));
  return removable.length;
}

// Сколько записей пользователя ещё не дошло до сервера (в том числе отклонённых сервером).
export async function countUnsent(userId: string): Promise<number> {
  return (await storedEntries()).filter((entry) => entry.userId === userId).length;
}

const FLUSH_TIMEOUT_MS = 10 * 60 * 1000;

// «Отправить сейчас» при выходе: прогнать очередь пользователя (в том числе отклонённые записи) и
// дождаться конца разбора. Возвращает, сколько записей осталось (связи нет, сервер отказал).
export async function flush(userId: string): Promise<number> {
  if (currentUser !== userId) await attach(userId);
  for (const entry of state.entries.filter((item) => item.stage === "failed")) {
    await retryEntry(entry.id);
  }
  delay = BASE_DELAY;
  void drain();
  const deadline = Date.now() + FLUSH_TIMEOUT_MS;
  while ((draining || state.busy !== null) && Date.now() < deadline) {
    await new Promise((resolve) => window.setTimeout(resolve, 250));
  }
  return countUnsent(userId);
}

export function retryAll(): void {
  delay = BASE_DELAY;
  void drain();
}
