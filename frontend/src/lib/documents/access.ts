import { INSPECTION_DOCUMENT_KIND, isEquipmentDocumentKind, isMoneyEquipmentDocumentKind } from "./equipment-kinds";

// Права экрана повторяют матрицу (backend/seeds/rbac_matrix.yaml). Это только подсказка интерфейсу:
// решает API и RLS, а скрытая кнопка не заменяет проверку на сервере.
export function canCreate(permissions: readonly string[]): boolean {
  return permissions.includes("documents:write");
}

export function canArchive(permissions: readonly string[]): boolean {
  return permissions.includes("documents:write:archive");
}

export function canExport(permissions: readonly string[]): boolean {
  return permissions.includes("documents:export");
}

// Виды техники пишет право техники (R2); технический осмотр - ещё и механик (equipment:write:maintenance).
export function canWriteKind(kind: string, permissions: readonly string[]): boolean {
  if (isEquipmentDocumentKind(kind)) {
    return (
      permissions.includes("equipment:write:documents") ||
      (kind === INSPECTION_DOCUMENT_KIND && permissions.includes("equipment:write:maintenance"))
    );
  }
  return canCreate(permissions);
}

// Сумма и файл финансового вида: денежные виды техники (страховка, прочее) - по тарифам (R3),
// остальные финансовые - по documents:read:financial.
export function canSeeMoney(kind: string, permissions: readonly string[]): boolean {
  return isMoneyEquipmentDocumentKind(kind)
    ? permissions.includes("equipment:read:rates")
    : permissions.includes("documents:read:financial");
}

// Файл финансового вида несёт суммы и условия: внутреннему читателю ссылку даёт право на деньги вида
// (DoD ADR-015, часть 3, раздел 12). Внешним ссылку выдаёт сервер по их видимости (Q4).
export function canDownload(
  doc: { kind: string; financial: boolean },
  permissions: readonly string[],
  internal = true,
): boolean {
  return !internal || !doc.financial || canSeeMoney(doc.kind, permissions);
}

// Сумма версии правится только в черновике (R1); архивный документ не меняется.
export function canEditVersionAmount(
  doc: { kind: string; financial: boolean; archived_at: string | null },
  version: { status: string },
  permissions: readonly string[],
  internal = true,
): boolean {
  return (
    internal &&
    doc.financial &&
    doc.archived_at === null &&
    version.status === "draft" &&
    canSeeMoney(doc.kind, permissions) &&
    canWriteKind(doc.kind, permissions)
  );
}

// Поле суммы в загрузке версии: внутренний с правом на деньги вида. Сервер проверяет то же и отвергает лишнее.
export function canEnterAmount(
  doc: { kind: string; financial: boolean },
  permissions: readonly string[],
  internal = true,
): boolean {
  return internal && doc.financial && canSeeMoney(doc.kind, permissions);
}

// Контрагенты для выбора: справочник отдаёт только внутренним с правом на документы (GET /documents/counterparties).
export function canUseCounterparties(permissions: readonly string[], internal = true): boolean {
  return internal && permissions.includes("documents:read");
}

// Колонка суммы нужна, только если сервер отдал хоть одну сумму: без неё колонки в DOM нет.
export function canSeeAmountColumn(rows: readonly { amount: string | null }[]): boolean {
  return rows.some((row) => row.amount !== null);
}

// Отметка «действует vN» нужна, когда действующая версия не последняя (R1, решение оркестратора).
export function effectiveDiffers(doc: {
  effective_version_no: number | null;
  latest_version_no: number | null;
}): boolean {
  return doc.effective_version_no !== null && doc.effective_version_no !== doc.latest_version_no;
}
