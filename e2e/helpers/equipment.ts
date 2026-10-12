import { createHmac, randomUUID } from "node:crypto";

import type { APIRequestContext, Page } from "@playwright/test";
import { expect } from "@playwright/test";

import { apiLogin, get, post, type Session } from "./api";
import { PASSWORD, today } from "./env";

export const EAST_NAME = "ЖК «Восток» (dev)";
export const WEST_NAME = "ЖК «Запад» (dev)";
export const TOTP_SECRET = process.env.DEV_SEED_TOTP_SECRET ?? "";

export type Project = { id: string; name: string; legal_entity_id: string };
export type TypeRow = { id: string; code: string; name: string };
export type UnitRow = { id: string; registry_number: string; legal_entity_id: string; equipment_type_id: string };

export function unique(prefix: string): string {
  return `${prefix}${randomUUID().slice(0, 8).toUpperCase()}`;
}

// Даты сценариев: далеко в будущем, чтобы не пересекаться с данными прошлых прогонов.
export function futureDay(offset: number): string {
  return today(offset);
}

export async function projectNamed(request: APIRequestContext, session: Session, name: string): Promise<Project> {
  const list = await get<Project[]>(request, session, "/api/v1/projects");
  const found = list.find((project) => project.name === name);
  expect(found, `объект ${name} не найден`).toBeTruthy();
  return found as Project;
}

export async function typeByCode(request: APIRequestContext, session: Session, code: string): Promise<TypeRow> {
  const list = await get<TypeRow[]>(request, session, "/api/v1/equipment/types");
  const found = list.find((item) => item.code === code);
  expect(found, `вид техники ${code} не найден`).toBeTruthy();
  return found as TypeRow;
}

// Новая единица с обязательными документами до 2040 года; страховка - по желанию сценария.
export async function newUnit(
  request: APIRequestContext,
  session: Session,
  project: Project,
  type: TypeRow,
  options: { insuranceUntil?: string; number?: string } = {},
): Promise<UnitRow> {
  const unit = await post<UnitRow>(request, session, "/api/v1/equipment/units", {
    legal_entity_id: project.legal_entity_id,
    equipment_type_id: type.id,
    ownership: "own",
    registry_number: options.number ?? unique("E2E"),
    contract_id: null,
  });
  for (const [kind, until] of [
    ["technical_inspection", "2040-12-31"],
    ["passport", "2040-12-31"],
    ["insurance", options.insuranceUntil ?? "2040-12-31"],
  ] as const) {
    await post(request, session, `/api/v1/equipment/units/${unit.id}/documents`, {
      kind,
      number: `D-${kind}`,
      valid_until: until,
    });
  }
  return unit;
}

// Вход с кодом TOTP: для владельца и финдиректора, как в интерфейсе (поле #totp-code).
export async function signInWithCode(page: Page, user: string, secret: string): Promise<void> {
  await page.goto("/login");
  await page.locator("#identifier").fill(`${user}@kut.test`);
  await page.locator("#password").fill(PASSWORD);
  await page.getByRole("button", { name: "Войти" }).click();
  await page.locator("#totp-code").waitFor({ timeout: 40_000 });
  await page.locator("#totp-code").fill(totp(secret));
  await page.getByRole("button", { name: "Подтвердить" }).click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"), { timeout: 40_000 });
}

// Одноразовый код RFC 6238 (SHA-1, 30 секунд, 6 цифр) по секрету в base32.
export function totp(secret: string, now = Date.now()): string {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(now / 1000 / 30)));
  const digest = createHmac("sha1", base32(secret)).update(counter).digest();
  const offset = digest[digest.length - 1] & 0x0f;
  const value =
    ((digest[offset] & 0x7f) << 24) |
    (digest[offset + 1] << 16) |
    (digest[offset + 2] << 8) |
    digest[offset + 3];
  return String(value % 1_000_000).padStart(6, "0");
}

function base32(input: string): Buffer {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = 0;
  let value = 0;
  const bytes: number[] = [];
  for (const char of input.replace(/=+$/, "").toUpperCase()) {
    const index = alphabet.indexOf(char);
    if (index < 0) continue;
    value = (value << 5) | index;
    bits += 5;
    if (bits >= 8) {
      bytes.push((value >>> (bits - 8)) & 0xff);
      bits -= 8;
    }
  }
  return Buffer.from(bytes);
}

export { apiLogin, get, post };
export type { Session };
