import { ApiError, authApi, toApiError } from "./api";
import type { Me, TokenResponse } from "./types";

// Access-токен живёт только в памяти страницы: ни localStorage, ни cookie, доступной скриптам.
// Долгую сессию держит refresh-токен в HttpOnly-cookie, сервер ротирует его при каждом обновлении.
export type Session = { token: string; user: Me; expiresAt: number };
export type SessionState = { ready: boolean; session: Session | null };

let state: SessionState = { ready: false, session: null };
const subscribers = new Set<() => void>();

export const sessionStore = {
  get: (): SessionState => state,
  subscribe(listener: () => void): () => void {
    subscribers.add(listener);
    return () => {
      subscribers.delete(listener);
    };
  },
};

function publish(next: SessionState): void {
  state = next;
  subscribers.forEach((listener) => listener());
}

export function startSession(response: TokenResponse): void {
  publish({
    ready: true,
    session: {
      token: response.access_token,
      user: response.user,
      expiresAt: Date.now() + response.expires_in * 1000,
    },
  });
}

export function clearSession(): void {
  publish({ ready: true, session: null });
}

// Обновление токена выполняет одна вкладка за раз и один запрос за раз: параллельные обновления
// одной cookie выглядели бы для сервера как повторное предъявление украденного токена.
async function withRefreshLock<T>(task: () => Promise<T>): Promise<T> {
  if (typeof navigator !== "undefined" && "locks" in navigator) {
    return await navigator.locks.request("kut-session-refresh", task);
  }
  return task();
}

let refreshing: Promise<Session | null> | null = null;

export function refreshSession(): Promise<Session | null> {
  refreshing ??= withRefreshLock(async () => {
    try {
      startSession(await authApi.refresh());
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        clearSession();
      } else if (!state.ready) {
        clearSession(); // сервер недоступен при первой загрузке: показываем форму входа
      }
    }
    return state.session;
  }).finally(() => {
    refreshing = null;
  });
  return refreshing;
}

export async function endSession(): Promise<void> {
  const token = state.session?.token;
  try {
    await authApi.logout(token);
  } finally {
    clearSession();
  }
}

// Запрос с токеном: при 401 один раз обновляет сессию и повторяет запрос.
export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const send = (token: string | undefined) =>
    fetch(path, {
      ...init,
      headers: { ...init.headers, ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      credentials: "same-origin",
      cache: "no-store",
    });

  let response = await send(state.session?.token);
  if (response.status === 401) {
    const renewed = await refreshSession();
    if (renewed) response = await send(renewed.token);
  }
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as T;
}
