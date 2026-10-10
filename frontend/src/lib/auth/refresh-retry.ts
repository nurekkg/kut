// Повтор обновления сессии при сбое сервера: две повторные попытки с паузами 0,5 с и 1,5 с.
// Только после них сессия считается потерянной (показываем форму входа при первой загрузке).
// 401 и остальные 4xx повтором не лечатся: токен истёк или запрос неверен, ошибка уходит сразу.
export const REFRESH_BACKOFF_MS: readonly number[] = [500, 1500];

export function isTransientFailure(error: unknown): boolean {
  if (error instanceof TypeError) return true; // fetch не дошёл до сервера
  const status = (error as { status?: unknown } | null)?.status;
  return typeof status === "number" && status >= 500;
}

export async function withRetry<T>(
  run: () => Promise<T>,
  wait: (ms: number) => Promise<void> = (ms) => new Promise((done) => setTimeout(done, ms)),
): Promise<T> {
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await run();
    } catch (error) {
      if (!isTransientFailure(error) || attempt >= REFRESH_BACKOFF_MS.length) throw error;
      await wait(REFRESH_BACKOFF_MS[attempt]);
    }
  }
}
