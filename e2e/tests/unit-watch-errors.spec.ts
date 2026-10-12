import { expect, test, type Page } from "@playwright/test";

import { watchErrors } from "../helpers/ui";

// Заглушка страницы: запоминает обработчики, по которым watchErrors получает ошибки браузера.
function fakePage() {
  const handlers = new Map<string, (value: unknown) => void>();
  const page = {
    on: (event: string, handler: (value: unknown) => void) => handlers.set(event, handler),
  } as unknown as Page;
  const emit = (event: string, value: unknown) => handlers.get(event)?.(value);
  return { page, emit };
}

function consoleError(text: string, url: string) {
  return { type: () => "error", text: () => text, location: () => ({ url, lineNumber: 0, columnNumber: 0 }) };
}

const CONFLICT = "Failed to load resource: the server responded with a status of 409 (Conflict)";
const ASSIGNMENTS = "http://localhost:3100/api/v1/equipment/assignments";

test("неожиданная ошибка консоли попадает в список и валит сценарий с проверкой пустого списка", () => {
  const { page, emit } = fakePage();
  const errors = watchErrors(page);
  emit("console", consoleError("Failed to load resource: the server responded with a status of 500", "http://localhost:3100/api/v1/projects"));
  expect(errors).toHaveLength(1);
  expect(errors[0]).toContain("/api/v1/projects");
});

test("ошибка страницы попадает в список", () => {
  const { page, emit } = fakePage();
  const errors = watchErrors(page);
  emit("pageerror", new Error("Hydration failed"));
  expect(errors).toEqual(["pageerror: Hydration failed"]);
});

test("409 на назначение - ошибка, если сценарий его не ждёт", () => {
  const { page, emit } = fakePage();
  const errors = watchErrors(page);
  emit("console", consoleError(CONFLICT, `${ASSIGNMENTS}/check`));
  expect(errors).toHaveLength(1);
});

test("409 на назначение не ошибка только в сценарии, который его ждёт", () => {
  const { page, emit } = fakePage();
  const errors = watchErrors(page, { allowAssignmentRefusals: true });
  emit("console", consoleError(CONFLICT, ASSIGNMENTS));
  emit("console", consoleError(CONFLICT, `${ASSIGNMENTS}/check`));
  expect(errors).toEqual([]);
});

test("409 с другого адреса остаётся ошибкой даже в сценарии техники", () => {
  const { page, emit } = fakePage();
  const errors = watchErrors(page, { allowAssignmentRefusals: true });
  emit("console", consoleError(CONFLICT, "http://localhost:3100/api/v1/projects"));
  expect(errors).toHaveLength(1);
});

test("401 при обновлении сеанса на входе - не ошибка", () => {
  const { page, emit } = fakePage();
  const errors = watchErrors(page);
  emit("console", consoleError("Failed to load resource: the server responded with a status of 401 (Unauthorized)", "http://localhost:3100/api/v1/auth/refresh"));
  expect(errors).toEqual([]);
});
