import { expect, test } from "@playwright/test";

import { certificateKey, draftName } from "../../frontend/src/lib/stock/draft-store";

// Сертификат - на партию: две партии одного материала с разными номерами не делят сертификат (ревью S7b, H1).
test("сертификат: одна партия - один сертификат, разные партии одного материала - разные сертификаты", () => {
  expect(certificateKey("m1", "П-1")).not.toBe(certificateKey("m1", "П-2"));
  expect(certificateKey("m1", " П-1 ")).toBe(certificateKey("m1", "П-1"));
  expect(certificateKey("m1", "П-1")).not.toBe(certificateKey("m2", "П-1"));
});

// Черновик привязан к пользователю: на общем телефоне чужой акт или ВН не продолжается (ревью S7b, M5).
test("черновик: имя привязано к объекту и пользователю", () => {
  expect(draftName("receipt", "p1", "u1")).not.toBe(draftName("receipt", "p1", "u2"));
  expect(draftName("receipt", "p1", "u1")).not.toBe(draftName("issue", "p1", "u1"));
});
