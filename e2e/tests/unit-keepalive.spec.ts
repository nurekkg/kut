import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, test } from "@playwright/test";

// uvicorn закрывает простаивающее keep-alive соединение через --timeout-keep-alive (по умолчанию 5 с).
// Прокси web (Next 16.3.7, httpxy) держит upstream-сокеты в пуле без своего таймаута простоя, поэтому
// порог задаёт uvicorn: при паузах дольше 5 с каждый запрос шёл по новому соединению. Значение 65 с
// держит соединение на паузах до минуты, запас над 60 с типичного простоя внешних балансировщиков.
const MIN_KEEP_ALIVE_S = 60;

function keepAliveSeconds(file: string): number | null {
  const source = readFileSync(path.resolve(__dirname, "../..", file), "utf8");
  const match = source.match(/"--timeout-keep-alive",\s*"(\d+)"/);
  return match ? Number(match[1]) : null;
}

test("docker-compose: api поднимает uvicorn с keep-alive не короче 60 с", () => {
  expect(keepAliveSeconds("docker-compose.yml")).toBeGreaterThanOrEqual(MIN_KEEP_ALIVE_S);
});

test("backend/Dockerfile: CMD с keep-alive не короче 60 с", () => {
  expect(keepAliveSeconds("backend/Dockerfile")).toBeGreaterThanOrEqual(MIN_KEEP_ALIVE_S);
});
