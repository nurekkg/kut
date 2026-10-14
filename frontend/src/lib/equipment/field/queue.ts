"use client";

import { useSyncExternalStore } from "react";

import { apiAs } from "@/lib/api";
import { ApiError } from "@/lib/auth/api";

import { EQUIPMENT_API } from "../queries";
import type { FuelRow, PresignedUpload, ShiftRow } from "../shifts";

import { shiftDb } from "./db";
import { OUTBOX_TTL_MS } from "./retention";
import type { FuelDraft, ReceiptDraft, ShiftEntry, SubmitOutcome } from "./types";

// Очередь машиниста: один проход записи идёт по шагам (лист, заправка, чек, отправка). Каждый шаг
// сохраняется в IndexedDB, поэтому повтор после обрыва продолжает с места, а не заводит дубль.
// Временные ошибки (нет связи, 5xx, 401) откладывают запись; постоянные (422 и прочие) помечают её
// как неотправленную с кодом причины - мастер и машинист видят текст в очереди и на экране.

type QueueState = { ready: boolean; entries: ShiftEntry[]; busy: string | null };

let state: QueueState = { ready: false, entries: [], busy: null };
const listeners = new Set<() => void>();

function publish(patch: Partial<QueueState>): void {
  state = { ...state, ...patch };
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useShiftQueue(): QueueState {
  return useSyncExternalStore(
    subscribe,
    () => state,
    () => state,
  );
}

class TransientError extends Error {}
class CancelledError extends Error {}

function classify(error: unknown): "transient" | "permanent" {
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

const BASE_DELAY = 5_000;
const MAX_DELAY = 120_000;
const FLUSH_TIMEOUT_MS = 10 * 60 * 1000;

let currentUser: string | null = null;
let timer: number | null = null;
let delay = BASE_DELAY;
const cancelled = new Set<string>();
const inflight = new Set<string>();

function ensureLive(id: string): void {
  if (cancelled.has(id)) throw new CancelledError();
}

function clone(entry: ShiftEntry): ShiftEntry {
  const receipt = entry.fuel?.receipt ? { ...entry.fuel.receipt } : null;
  const fuel: FuelDraft | null = entry.fuel ? { ...entry.fuel, receipt } : null;
  return { ...entry, fuel, patch: entry.patch ? { ...entry.patch } : null };
}

function upsert(entries: ShiftEntry[], entry: ShiftEntry): ShiftEntry[] {
  const rest = entries.filter((item) => item.id !== entry.id);
  return [...rest, entry].sort((a, b) => a.createdAt - b.createdAt);
}

async function persist(entry: ShiftEntry): Promise<void> {
  if (cancelled.has(entry.id)) return;
  await shiftDb.put(entry);
  publish({ entries: upsert(state.entries, entry) });
}

function presignExpired(receipt: ReceiptDraft): boolean {
  if (receipt.presignedAt === null || receipt.uploadUrl === null) return true;
  return Date.now() > receipt.presignedAt + (receipt.expiresIn - 60) * 1000;
}

async function runFuel(
  entry: ShiftEntry,
  save: () => Promise<void>,
  shiftId: string,
): Promise<void> {
  const fuel = entry.fuel;
  if (!fuel) return;
  const api = apiAs(entry.userId);
  const base = `${EQUIPMENT_API}/shifts/${shiftId}`;
  if (fuel.fuelId === null) {
    ensureLive(entry.id);
    const row = await api.post<FuelRow>(
      `${base}/fuel`,
      { liters: fuel.liters, amount: fuel.amount },
      fuel.key,
    );
    fuel.fuelId = row.id;
    await save();
  }
  const receipt = fuel.receipt;
  if (!receipt || receipt.confirmed || fuel.fuelId === null) return;
  const fuelBase = `${base}/fuel/${fuel.fuelId}/receipt`;
  if (presignExpired(receipt) || receipt.key === null || receipt.uploadUrl === null) {
    ensureLive(entry.id);
    const presigned = await api.post<PresignedUpload>(fuelBase, {
      content_type: receipt.contentType,
      size_bytes: receipt.sizeBytes,
    });
    receipt.key = presigned.key;
    receipt.uploadUrl = presigned.upload_url;
    receipt.headers = presigned.headers;
    receipt.presignedAt = Date.now();
    receipt.expiresIn = presigned.expires_in;
    receipt.uploaded = false;
    await save();
  }
  if (!receipt.uploaded) {
    ensureLive(entry.id);
    const response = await fetch(receipt.uploadUrl ?? "", {
      method: "PUT",
      headers: receipt.headers,
      body: receipt.blob,
    });
    if (!response.ok) {
      // Подпись истекла или файл не принят: в следующий раз подписываем заново.
      receipt.presignedAt = null;
      await save();
      throw new TransientError(`upload_${response.status}`);
    }
    receipt.uploaded = true;
    await save();
  }
  ensureLive(entry.id);
  await api.post(`${fuelBase}/confirm`, { key: receipt.key });
  receipt.confirmed = true;
  await save();
}

async function runSteps(entry: ShiftEntry, save: () => Promise<void>): Promise<void> {
  const api = apiAs(entry.userId);
  const base = `${EQUIPMENT_API}/shifts`;
  if (entry.shiftId === null) {
    ensureLive(entry.id);
    const created = await api.post<ShiftRow>(base, entry.shift, entry.createKey);
    entry.shiftId = created.id;
    await save();
  }
  const shiftId = entry.shiftId;
  if (entry.mode === "resubmit") {
    ensureLive(entry.id);
    const current = await api.get<ShiftRow>(`${base}/${shiftId}`);
    if (current.status === "submitted" || current.status === "confirmed") {
      entry.submitted = true;
      await save();
      return;
    }
    if (current.status === "rejected") {
      await api.post<ShiftRow>(`${base}/${shiftId}/reopen`);
    }
    if (entry.patch && !entry.patched) {
      await api.patch<ShiftRow>(`${base}/${shiftId}`, entry.patch);
      entry.patched = true;
      await save();
    }
  }
  if (entry.fuel) await runFuel(entry, save, shiftId);
  if (!entry.submitted) {
    ensureLive(entry.id);
    const current = await api.get<ShiftRow>(`${base}/${shiftId}`);
    if (current.status === "draft") {
      await api.post<ShiftRow>(`${base}/${shiftId}/submit`);
    }
    entry.submitted = true;
    await save();
  }
}

// Одна запись: шаги, затем либо удаление из очереди, либо отметка об ошибке или отложенный повтор.
async function execute(source: ShiftEntry): Promise<SubmitOutcome> {
  const working = clone(source);
  inflight.add(working.id);
  publish({ busy: working.id });
  try {
    await runSteps(working, () => persist(working));
    await shiftDb.remove(working.id);
    publish({
      entries: state.entries.filter((item) => item.id !== working.id),
      busy: null,
    });
    delay = BASE_DELAY;
    return { kind: "sent" };
  } catch (error) {
    if (error instanceof CancelledError) {
      publish({ busy: null });
      return { kind: "queued" };
    }
    working.attempts += 1;
    if (classify(error) === "permanent") {
      const code = error instanceof ApiError ? error.code : "unknown";
      const kept = working.shiftId !== null;
      if (kept) {
        working.stage = "failed";
        working.error = code;
        await persist(working);
      } else {
        // Лист на сервере не создан: запись не нужна, экран покажет ошибку и даст исправить.
        cancelled.add(working.id);
        await shiftDb.remove(working.id);
        publish({ entries: state.entries.filter((item) => item.id !== working.id) });
      }
      publish({ busy: null });
      return { kind: "failed", code, kept };
    }
    working.stage = "pending";
    await persist(working);
    publish({ busy: null });
    schedule();
    return { kind: "queued" };
  } finally {
    inflight.delete(working.id);
  }
}

// Без связи проверяем очередь каждые пять секунд и паузу не растим: это не сбой сервера.
function schedule(): void {
  if (timer !== null) window.clearTimeout(timer);
  const offline = !navigator.onLine;
  timer = window.setTimeout(() => {
    timer = null;
    void drain();
  }, offline ? BASE_DELAY : delay);
  if (!offline) delay = Math.min(delay * 2, MAX_DELAY);
}

async function loop(): Promise<void> {
  for (;;) {
    const next = state.entries.find(
      (entry) =>
        entry.userId === currentUser &&
        entry.stage === "pending" &&
        !cancelled.has(entry.id) &&
        !inflight.has(entry.id),
    );
    if (!next) {
      delay = BASE_DELAY;
      return;
    }
    const outcome = await execute(next);
    if (outcome.kind === "queued") return;
  }
}

async function drain(): Promise<void> {
  if (currentUser === null) return;
  if ("locks" in navigator) {
    // Одна вкладка за раз: две вкладки с общей очередью отправили бы лист дважды. Ждём блокировку,
    // а не пропускаем проход: иначе запись ждала бы следующего таймера.
    await navigator.locks.request("kut-equipment-outbox", async () => {
      await loop();
    });
  } else {
    await loop();
  }
}

function onOnline(): void {
  delay = BASE_DELAY;
  void drain();
}

export async function attachShiftQueue(userId: string): Promise<void> {
  currentUser = userId;
  window.addEventListener("online", onOnline);
  await purgeShifts(userId);
  try {
    const stored = await shiftDb.all();
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

export function detachShiftQueue(): void {
  currentUser = null;
  window.removeEventListener("online", onOnline);
  if (timer !== null) window.clearTimeout(timer);
  timer = null;
  publish({ ready: false, entries: [], busy: null });
}

// Новый лист: запись сохраняется сразу, первый проход - тут же. Ответ нужен экрану: отправлен, ждёт
// связи или отказан с кодом (например, суточный лимит часов).
export async function submitNewShift(entry: ShiftEntry): Promise<SubmitOutcome> {
  await persist(entry);
  if (!navigator.onLine) {
    schedule();
    return { kind: "queued" };
  }
  return execute(entry);
}

// Исправленный отклонённый лист: повтор с правкой содержимого и отправкой снова.
export async function submitResubmit(entry: ShiftEntry): Promise<SubmitOutcome> {
  return submitNewShift(entry);
}

export async function retryShift(id: string): Promise<void> {
  const entry = state.entries.find((item) => item.id === id);
  if (!entry) return;
  const next: ShiftEntry = { ...entry, stage: "pending", error: null, attempts: 0 };
  await persist(next);
  delay = BASE_DELAY;
  void drain();
}

export async function discardShift(id: string): Promise<void> {
  cancelled.add(id);
  await shiftDb.remove(id);
  publish({ entries: state.entries.filter((item) => item.id !== id) });
}

export async function discardShifts(userId: string): Promise<void> {
  const stored = await shiftDb.all();
  for (const entry of stored) {
    if (entry.userId !== userId) continue;
    cancelled.add(entry.id);
    await shiftDb.remove(entry.id);
  }
  publish({ entries: state.entries.filter((entry) => entry.userId !== userId) });
}

// Удаляет чужие записи и записи старше недели (тот же срок, что у очереди /m).
export async function purgeShifts(userId: string, now: number = Date.now()): Promise<number> {
  const stored = await shiftDb.all();
  let removed = 0;
  for (const entry of stored) {
    if (entry.userId !== userId || now - entry.createdAt > OUTBOX_TTL_MS) {
      await shiftDb.remove(entry.id);
      removed += 1;
    }
  }
  return removed;
}

export async function countUnsentShifts(userId: string): Promise<number> {
  const stored = await shiftDb.all();
  return stored.filter((entry) => entry.userId === userId).length;
}

// Для выхода: «Отправить сейчас». Ждём, пока очередь пользователя опустеет или выйдет срок.
export async function flushShifts(userId: string): Promise<number> {
  const deadline = Date.now() + FLUSH_TIMEOUT_MS;
  currentUser = currentUser ?? userId;
  for (;;) {
    for (const entry of state.entries) {
      if (entry.userId === userId && entry.stage === "failed") {
        await retryShift(entry.id);
      }
    }
    await drain();
    const left = await countUnsentShifts(userId);
    if (left === 0 || Date.now() > deadline) return left;
    await new Promise((resolve) => window.setTimeout(resolve, 2_000));
  }
}
