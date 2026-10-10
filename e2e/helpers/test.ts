import { test as base } from "@playwright/test";

import { waitForStack } from "./health";

export { expect } from "@playwright/test";

// Каждый сценарий начинается, только когда стек отвечает (см. waitForStack).
export const test = base.extend<{ stack: void }>({
  stack: [
    async ({ request }, use) => {
      await waitForStack(request);
      await use();
    },
    { auto: true },
  ],
});
