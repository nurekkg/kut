// Срок и принадлежность записей очереди отправки (решение PO). Очередь лежит на устройстве вместе с
// фото и геометками, поэтому чужие и залежавшиеся записи удаляются сами: запись чужого пользователя
// стирается при входе другого человека, запись старше недели - при следующем запуске приложения.
// Чистая логика без браузерных API: проверяется юнит-тестами.
export const OUTBOX_TTL_MS = 7 * 24 * 60 * 60 * 1000;

type Owned = { id: string; userId: string; createdAt: number };

// Что стереть: записи другого пользователя и записи старше срока хранения.
export function selectRemovable<T extends Owned>(entries: T[], userId: string, now: number): T[] {
  return entries.filter((entry) => entry.userId !== userId || now - entry.createdAt > OUTBOX_TTL_MS);
}
