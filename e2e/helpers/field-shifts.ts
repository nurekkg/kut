import { expect, type APIRequestContext, type Browser, type Page } from "@playwright/test";

import { apiLogin, get, jwtSubject, post, type Session } from "./api";
import { newUnit, projectNamed, typeByCode, type Project, type UnitRow } from "./equipment";
import { EAST, PHOTO, today } from "./env";
import { desktopContext, signIn, shot } from "./ui";

// Сценарии техники (SE-2b): листы машиниста, подтверждение на объекте, журнал. Данные заводят сами
// сценарии через API; на UI остаются действия людей: ввод смены, подтверждение, правка, журнал.

export const SECOND = "ЖК «Восток-2» (dev)";
export const OWN_TYPE_CODE = "EXC";

export type ShiftRow = {
  id: string;
  status: "draft" | "submitted" | "confirmed" | "rejected";
  hours: string;
  reject_reason: string | null;
  equipment_unit_id: string;
  project_id: string;
  work_date: string;
  operator_id: string;
};

export type FuelRow = { id: string; shift_id: string | null; liters: string | null };

export type Prepared = {
  head: Session;
  unitEast: UnitRow;
  unitSecond: UnitRow;
  east: Project;
  second: Project;
  operatorId: string;
};

// Техника, назначения на сегодня и оператор машиниста. Единицы свежие: номера уникальны.
export async function prepareDay(request: APIRequestContext, count: 1 | 2 = 2): Promise<Prepared> {
  const head = await apiLogin(request, "equipment_head");
  const machinist = await apiLogin(request, "machinist");
  const east = await projectNamed(request, head, EAST);
  const second = await projectNamed(request, head, SECOND);
  const type = await typeByCode(request, head, OWN_TYPE_CODE);
  const unitEast = await newUnit(request, head, east, type);
  const unitSecond = count === 2 ? await newUnit(request, head, second, type) : unitEast;
  const operatorId = await operatorIdOf(request, machinist);
  await assignToday(request, head, unitEast, east, operatorId);
  if (count === 2) await assignToday(request, head, unitSecond, second, operatorId);
  return { head, unitEast, unitSecond, east, second, operatorId };
}

export async function operatorIdOf(request: APIRequestContext, session: Session): Promise<string> {
  const rows = await get<{ id: string; user_id: string | null }[]>(
    request,
    session,
    "/api/v1/equipment/operators",
  );
  const me = jwtSubject(session.token);
  const found = rows.find((row) => row.user_id === me);
  expect(found, "у учётки машиниста нет оператора").toBeTruthy();
  return (found as { id: string }).id;
}

export async function assignToday(
  request: APIRequestContext,
  head: Session,
  unit: UnitRow,
  project: Project,
  operatorId: string,
  hours = "8",
): Promise<void> {
  await post(request, head, "/api/v1/equipment/assignments", {
    project_id: project.id,
    equipment_unit_id: unit.id,
    date_from: today(),
    date_to: today(),
    planned_hours_per_day: hours,
    operator_id: operatorId,
  });
}

// Лист через API: от имени того, кто его ведёт (машинист, прораб).
export async function createShift(
  request: APIRequestContext,
  session: Session,
  body: {
    equipment_unit_id: string;
    project_id: string;
    operator_id: string;
    hours: string;
    work_date?: string;
    trips?: number;
    tonnes?: string;
  },
): Promise<ShiftRow> {
  return post<ShiftRow>(request, session, "/api/v1/equipment/shifts", {
    work_date: today(),
    ...body,
  });
}

export async function submitShift(request: APIRequestContext, session: Session, id: string): Promise<ShiftRow> {
  return post<ShiftRow>(request, session, `/api/v1/equipment/shifts/${id}/submit`, undefined);
}

export async function shiftById(request: APIRequestContext, session: Session, id: string): Promise<ShiftRow> {
  return get<ShiftRow>(request, session, `/api/v1/equipment/shifts/${id}`);
}

export async function fuelOf(request: APIRequestContext, session: Session, shiftId: string): Promise<FuelRow[]> {
  return get<FuelRow[]>(request, session, `/api/v1/equipment/shifts/${shiftId}/fuel`);
}

// Вход на телефоне машиниста: сразу на экран «Сегодня».
export async function openMachinistPhone(page: Page): Promise<void> {
  await signIn(page, "machinist");
  await page.goto("/m/equipment");
  await expect(page.getByTestId("tab-today")).toBeVisible({ timeout: 40_000 });
}

// Карточка назначения по номеру единицы на экране «Сегодня».
export function todayCard(page: Page, unit: UnitRow) {
  return page.getByTestId("today-list").locator("li").filter({ hasText: unit.registry_number });
}

// Ввод смены на телефоне: часы, при необходимости - заправка с чеком; отправка через очередь.
export async function enterShift(
  page: Page,
  unit: UnitRow,
  options: { hours: string; liters?: string; amount?: string; photo?: boolean },
): Promise<void> {
  await todayCard(page, unit).getByTestId("today-enter").click();
  await expect(page.getByTestId("shift-form")).toBeVisible({ timeout: 20_000 });
  await page.getByTestId("field-hours").fill(options.hours);
  if (options.liters) await page.getByTestId("field-liters").fill(options.liters);
  if (options.amount) await page.getByTestId("field-amount").fill(options.amount);
  if (options.photo) {
    await page.getByTestId("receipt-input").setInputFiles(PHOTO);
    await expect(page.getByText("Фото чека прикреплено")).toBeVisible({ timeout: 30_000 });
  }
  await page.getByTestId("form-submit").click();
}

// Вход на объектный экран подтверждения: сотрудник с правом на листы видит «Технику» на /approvals.
export async function openApprovals(page: Page, user: string): Promise<void> {
  await signIn(page, user);
  await page.goto("/approvals");
  await expect(page.getByTestId("equipment-approvals")).toBeVisible({ timeout: 40_000 });
}

export function shiftRow(page: Page, unit: UnitRow) {
  return page.getByTestId("shift-row").filter({ hasText: unit.registry_number });
}

export async function screenshot(page: Page, name: string): Promise<void> {
  await shot(page, name);
}

// Страница для одной роли: свой контекст браузера, чтобы сеанс прошлой роли не мешал входу.
export async function newPageFor(browser: Browser): Promise<Page> {
  const context = await desktopContext(browser);
  return context.newPage();
}
