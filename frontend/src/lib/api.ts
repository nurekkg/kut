import { apiFetch } from "@/lib/auth/session";

type QueryValue = string | number | boolean | null | undefined;
// Массив повторяет параметр (?id=a&id=b): так сервер принимает несколько значений одного фильтра.
type Query = Record<string, QueryValue | QueryValue[]>;

function withQuery(path: string, params?: Query): string {
  if (!params) return path;
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    for (const item of Array.isArray(value) ? value : [value]) {
      if (item !== undefined && item !== null && item !== "") search.append(key, String(item));
    }
  }
  const text = search.toString();
  return text ? `${path}?${text}` : path;
}

function jsonInit(method: string, body: unknown, key?: string): RequestInit {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (key) headers["Idempotency-Key"] = key;
  return { method, headers, body: body === undefined ? undefined : JSON.stringify(body) };
}

// Ключ повтора создаётся один раз на открытие формы: повторная отправка после сбоя сети
// не заведёт вторую запись, а после успеха форма получит новый ключ.
export function newIdempotencyKey(): string {
  // randomUUID есть только в защищённом контексте (https, localhost); по http запасной путь.
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export const api = {
  get: <T>(path: string, params?: Query) => apiFetch<T>(withQuery(path, params)),
  post: <T>(path: string, body?: unknown, key?: string) =>
    apiFetch<T>(path, jsonInit("POST", body, key)),
  put: <T>(path: string, body?: unknown) => apiFetch<T>(path, jsonInit("PUT", body)),
  patch: <T>(path: string, body?: unknown) => apiFetch<T>(path, jsonInit("PATCH", body)),
  delete: (path: string) => apiFetch<void>(path, { method: "DELETE" }),
};
