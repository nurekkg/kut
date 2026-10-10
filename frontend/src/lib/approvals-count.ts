// Лимит выборки на один объект: больше за раз прораб не разберёт, сервер отдаёт не больше.
export const APPROVAL_LIMIT = 200;

// Счётчик «На согласовании» и строка «всего» в очереди считают одно: сумму очередей по всем видимым
// объектам. Выборка по каждому объекту ограничена лимитом, поэтому, если хоть один объект упёрся в
// него, точное число неизвестно, и показываем «N+». Модуль без импортов: его читают и браузерный код,
// и юнит-тесты, которые не знают о псевдонимах `@/`.
export function approvalTotal(
  perProject: readonly number[],
  limit: number = APPROVAL_LIMIT,
): { total: number; capped: boolean } {
  return {
    total: perProject.reduce((sum, count) => sum + count, 0),
    capped: perProject.some((count) => count >= limit),
  };
}
