import { expect, test } from "@playwright/test";

import { formatQuantity } from "../../frontend/src/lib/documents/format";

// Количества на экранах склада и документов: без лишних нулей, запятая, до четырёх знаков.
test("количество: без лишних нулей, запятая, до четырёх знаков", () => {
  expect(formatQuantity("40.0000")).toBe("40");
  expect(formatQuantity("40.5000")).toBe("40,5");
  expect(formatQuantity("0.1250")).toBe("0,125");
  expect(formatQuantity("12.34567")).toBe("12,3457");
});
