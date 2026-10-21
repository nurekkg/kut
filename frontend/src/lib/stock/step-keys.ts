"use client";

import { useRef } from "react";

import { newIdempotencyKey } from "@/lib/api";
import { prepareUpload, type UploadWork } from "@/lib/documents/upload";

// Ключи Idempotency-Key и загрузки шагов формы склада. Ключ привязан к запросу шага (адрес и тело): повтор
// того же запроса после сбоя шлёт тот же ключ, и сервер отдаёт прежний ответ; другой запрос под тем же именем
// шага получает новый ключ. Файл грузится один раз на шаг (presign, ключ объекта, ключ подтверждения), повтор
// подтверждает тот же объект. Всё забывается после успеха шага.
export function useStepKeys() {
  const keys = useRef(new Map<string, { request: string; key: string }>());
  const uploads = useRef(new Map<string, { file: File; work: UploadWork }>());

  function keyFor(step: string, request: string): string {
    const known = keys.current.get(step);
    if (known !== undefined && known.request === request) return known.key;
    const key = newIdempotencyKey();
    keys.current.set(step, { request, key });
    return key;
  }

  async function uploadFor(step: string, file: File): Promise<UploadWork> {
    const known = uploads.current.get(step);
    if (known !== undefined && known.file === file) return known.work;
    const work = await prepareUpload(
      file,
      keyFor(step, `file:${file.name}:${file.size}:${file.lastModified}`),
    );
    uploads.current.set(step, { file, work });
    return work;
  }

  function forget(step: string): void {
    keys.current.delete(step);
    uploads.current.delete(step);
  }

  function clear(): void {
    keys.current.clear();
    uploads.current.clear();
  }

  return { keyFor, uploadFor, forget, clear };
}
