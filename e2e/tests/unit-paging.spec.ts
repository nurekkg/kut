import { expect, test } from "@playwright/test";

import { collectPages } from "../../frontend/src/lib/paging";

// Чистая логика постраничного чтения, без браузера. Сервер отдаёт снимки не больше 500 за запрос:
// без догрузки у самых новых записей очереди согласования снимки пропадали молча.
function server(total: number, pageSize: number) {
  const offsets: number[] = [];
  const all = Array.from({ length: total }, (_, index) => index);
  return {
    offsets,
    fetchPage: async (offset: number) => {
      offsets.push(offset);
      return all.slice(offset, offset + pageSize);
    },
  };
}

test("собирает все страницы по порядку и останавливается на короткой", async () => {
  const fake = server(1203, 500);
  const items = await collectPages(fake.fetchPage, 500);
  expect(items).toHaveLength(1203);
  expect(items.every((value, index) => value === index)).toBe(true);
  expect(fake.offsets).toEqual([0, 500, 1000]);
});

test("число записей кратно странице: лишний запрос получает пустой ответ и на этом конец", async () => {
  const fake = server(1000, 500);
  const items = await collectPages(fake.fetchPage, 500);
  expect(items).toHaveLength(1000);
  expect(fake.offsets).toEqual([0, 500, 1000]);
});

test("короткий список читается одним запросом", async () => {
  const fake = server(3, 500);
  expect(await collectPages(fake.fetchPage, 500)).toHaveLength(3);
  expect(fake.offsets).toEqual([0]);
});

test("пустой список: один запрос, пустой результат", async () => {
  const fake = server(0, 500);
  expect(await collectPages(fake.fetchPage, 500)).toEqual([]);
  expect(fake.offsets).toEqual([0]);
});

test("предохранитель: бесконечный сервер не зацикливает чтение", async () => {
  let calls = 0;
  const items = await collectPages(
    async () => {
      calls += 1;
      return [1, 2, 3];
    },
    3,
    5,
  );
  expect(calls).toBe(5);
  expect(items).toHaveLength(15);
});

test("ошибка страницы не глотается: чтение падает целиком, а не отдаёт обрезанный список", async () => {
  await expect(
    collectPages(async (offset) => {
      if (offset > 0) throw new Error("network");
      return [1, 2];
    }, 2),
  ).rejects.toThrow("network");
});
