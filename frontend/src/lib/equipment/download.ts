"use client";

import { toApiError } from "@/lib/auth/api";
import { refreshSession, sessionStore } from "@/lib/auth/session";

// Обычный API-клиент читает только JSON, а шаблон и отчёт - файлы: запрос идёт с токеном сессии
// и тем же обновлением токена, что и остальные запросы.
export async function fetchBlob(path: string): Promise<Blob> {
  const token = sessionStore.get().session?.token ?? (await refreshSession())?.token;
  const send = (value: string | undefined) =>
    fetch(path, {
      headers: value ? { Authorization: `Bearer ${value}` } : {},
      credentials: "same-origin",
      cache: "no-store",
    });
  let response = await send(token);
  if (response.status === 401) {
    const renewed = await refreshSession();
    if (renewed) response = await send(renewed.token);
  }
  if (!response.ok) throw await toApiError(response);
  return response.blob();
}

export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
