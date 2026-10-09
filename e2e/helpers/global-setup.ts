import { request, type FullConfig } from "@playwright/test";

import { waitForStack } from "./health";

const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

// Сценарии заводят объекты, ГПР, факты и фото и рассчитывают на демо-данные dev-сида. На чужом
// стенде им делать нечего: адрес не из петли принимается только по явному разрешению.
function assertLocalTarget(baseURL: string | undefined): void {
  const host = new URL(baseURL ?? "http://localhost").hostname;
  if (LOCAL_HOSTS.has(host) || process.env.E2E_ALLOW_REMOTE === "1") return;
  throw new Error(
    `E2E_BASE_URL указывает на ${host}: сценарии пишут данные и рассчитаны на dev-сид. ` +
      "Для стенда вне этой машины задайте E2E_ALLOW_REMOTE=1.",
  );
}

// Один раз перед набором: стек должен быть поднят (web, api, база, Redis, S3).
export default async function globalSetup(config: FullConfig): Promise<void> {
  const baseURL = config.projects[0].use.baseURL;
  assertLocalTarget(baseURL);
  const context = await request.newContext({ baseURL });
  await waitForStack(context);
  await context.dispose();
}
