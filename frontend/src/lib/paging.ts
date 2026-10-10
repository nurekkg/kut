// Постраничное чтение списка: пока страница полная, берётся следующая. Сервер ограничивает ответ
// (снимки - 500 за запрос), и без догрузки хвост списка пропал бы без предупреждения. Ошибка любой
// страницы роняет чтение целиком: обрезанный список не выдаётся за полный.
export async function collectPages<T>(
  fetchPage: (offset: number) => Promise<T[]>,
  pageSize: number,
  maxPages = 40,
): Promise<T[]> {
  const all: T[] = [];
  for (let page = 0; page < maxPages; page += 1) {
    const items = await fetchPage(page * pageSize);
    all.push(...items);
    if (items.length < pageSize) break;
  }
  return all;
}
