import { expect, type APIRequestContext } from "@playwright/test";

// Перед сценарием ждём, пока стек отвечает: на слабой машине PostgreSQL иногда уходит в
// восстановление (recovery mode) на десяток секунд, и сценарий упал бы не по вине приложения.
export async function waitForStack(request: APIRequestContext, seconds = 120): Promise<void> {
  const deadline = Date.now() + seconds * 1000;
  let last = "";
  while (Date.now() < deadline) {
    try {
      const response = await request.get("/api/health/ready", { timeout: 10_000 });
      last = `${response.status()} ${await response.text()}`;
      if (response.ok()) return;
    } catch (error) {
      last = String(error);
    }
    await new Promise((resolve) => setTimeout(resolve, 3000));
  }
  expect(false, `стек не готов за ${seconds} с: ${last}`).toBe(true);
}
