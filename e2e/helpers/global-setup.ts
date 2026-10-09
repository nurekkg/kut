import { request, type FullConfig } from "@playwright/test";

import { waitForStack } from "./health";

// Один раз перед набором: стек должен быть поднят (web, api, база, Redis, S3).
export default async function globalSetup(config: FullConfig): Promise<void> {
  const baseURL = config.projects[0].use.baseURL;
  const context = await request.newContext({ baseURL });
  await waitForStack(context);
  await context.dispose();
}
