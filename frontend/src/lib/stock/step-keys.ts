"use client";

import { useRef } from "react";

import { newIdempotencyKey } from "@/lib/api";
import { prepareUpload, type UploadWork } from "@/lib/documents/upload";
import { fileFingerprint, readDraft, writeDraft, type SavedKeys } from "@/lib/stock/draft-store";

// Ключи Idempotency-Key шагов формы склада. Ключ привязан к запросу шага (адрес и тело): повтор того же запроса
// после сбоя шлёт тот же ключ, и сервер отдаёт прежний ответ, а не заводит вторую запись. Другой запрос под тем же
// именем шага получает новый ключ. Ключи создания, строк и отправки лежат в sessionStorage: после перезагрузки
// вкладки повтор того же запроса тоже идемпотентен. Загрузки файлов ключей не сохраняют: после перезагрузки файл
// грузится заново с новым ключом объекта (повтор с прежним ключом дал бы 422). Ключ шага забывается после успеха.
export function useStepKeys(storageName: string | null) {
  const keysName = storageName === null ? null : `${storageName}#keys`;
  const saved = useRef<SavedKeys | null>(null);
  if (saved.current === null) {
    saved.current = (keysName === null ? null : readDraft<SavedKeys>(keysName)) ?? {};
  }
  const uploads = useRef(new Map<string, { file: File; work: UploadWork }>());
  const uploadKeys = useRef(new Map<string, { fingerprint: string; key: string }>());

  function persist(): void {
    if (keysName === null) return;
    const current = saved.current ?? {};
    writeDraft(keysName, Object.keys(current).length === 0 ? null : current);
  }

  function keyFor(step: string, request: string): string {
    const current = saved.current ?? {};
    const known = current[step];
    if (known !== undefined && known.request === request) return known.key;
    const key = newIdempotencyKey();
    saved.current = { ...current, [step]: { request, key } };
    persist();
    return key;
  }

  async function uploadFor(step: string, file: File): Promise<UploadWork> {
    const known = uploads.current.get(step);
    if (known !== undefined && known.file === file) return known.work;
    const fingerprint = fileFingerprint(file);
    const previous = uploadKeys.current.get(step);
    const key =
      previous !== undefined && previous.fingerprint === fingerprint ? previous.key : newIdempotencyKey();
    uploadKeys.current.set(step, { fingerprint, key });
    const work = await prepareUpload(file, key);
    uploads.current.set(step, { file, work });
    return work;
  }

  function forget(step: string): void {
    const current = { ...(saved.current ?? {}) };
    delete current[step];
    saved.current = current;
    uploads.current.delete(step);
    uploadKeys.current.delete(step);
    persist();
  }

  function clear(): void {
    saved.current = {};
    uploads.current.clear();
    uploadKeys.current.clear();
    persist();
  }

  return { keyFor, uploadFor, forget, clear };
}
