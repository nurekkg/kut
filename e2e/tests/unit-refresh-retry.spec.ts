import { expect, test } from "@playwright/test";

import { REFRESH_BACKOFF_MS, withRetry } from "../../frontend/src/lib/auth/refresh-retry";

// Сценарий ответов сервера на обновление сессии: число или "network" (обрыв сети) или "ok".
function scripted(steps: Array<number | "network" | "ok">) {
  const waits: number[] = [];
  let calls = 0;
  const run = async () => {
    const step = steps[calls];
    calls += 1;
    if (step === "ok") return "session";
    if (step === "network") throw new TypeError("Failed to fetch");
    throw Object.assign(new Error(`HTTP ${step}`), { status: step });
  };
  const wait = async (ms: number) => {
    waits.push(ms);
  };
  return { run, wait, waits, calls: () => calls };
}

test("два повтора с паузами 0,5 с и 1,5 с: 5xx дважды, затем успех сохраняет сессию", async () => {
  const server = scripted([503, 500, "ok"]);
  await expect(withRetry(server.run, server.wait)).resolves.toBe("session");
  expect(server.calls()).toBe(3);
  expect(server.waits).toEqual([500, 1500]);
});

test("обрыв сети при обновлении тоже повторяется", async () => {
  const server = scripted(["network", "ok"]);
  await expect(withRetry(server.run, server.wait)).resolves.toBe("session");
  expect(server.calls()).toBe(2);
  expect(server.waits).toEqual([500]);
});

test("после двух повторов ошибка уходит дальше: всего три попытки, не четыре", async () => {
  const server = scripted([502, 503, 504, "ok"]);
  await expect(withRetry(server.run, server.wait)).rejects.toMatchObject({ status: 504 });
  expect(server.calls()).toBe(3);
});

test("401 не повторяется: истёкший токен сразу ведёт к выходу", async () => {
  const server = scripted([401, "ok"]);
  await expect(withRetry(server.run, server.wait)).rejects.toMatchObject({ status: 401 });
  expect(server.calls()).toBe(1);
  expect(server.waits).toEqual([]);
});

test("403 и 404 не повторяются", async () => {
  for (const status of [403, 404]) {
    const server = scripted([status, "ok"]);
    await expect(withRetry(server.run, server.wait)).rejects.toMatchObject({ status });
    expect(server.calls()).toBe(1);
  }
});

test("число повторов зафиксировано: две паузы", () => {
  expect([...REFRESH_BACKOFF_MS]).toEqual([500, 1500]);
});
