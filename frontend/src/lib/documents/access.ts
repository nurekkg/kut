// Права экрана повторяют матрицу (backend/seeds/rbac_matrix.yaml). Это только подсказка интерфейсу:
// решает API и RLS, а скрытая кнопка не заменяет проверку на сервере.
export function canCreate(permissions: readonly string[]): boolean {
  return permissions.includes("documents:write");
}

export function canUploadVersion(permissions: readonly string[]): boolean {
  return permissions.includes("documents:write");
}

export function canArchive(permissions: readonly string[]): boolean {
  return permissions.includes("documents:write:archive");
}

export function canExport(permissions: readonly string[]): boolean {
  return permissions.includes("documents:export");
}

export function canReadFinancial(permissions: readonly string[]): boolean {
  return permissions.includes("documents:read:financial");
}

// Файл финансового вида несёт суммы и условия: внутреннему читателю ссылку даёт только documents:read:financial
// (DoD ADR-015, часть 3, раздел 12). Внешним ссылку выдаёт сервер по их видимости (Q4): заказчик видит свои
// утверждённые документы с суммами и файлами.
export function canDownload(
  doc: { financial: boolean },
  permissions: readonly string[],
  internal = true,
): boolean {
  return !internal || !doc.financial || canReadFinancial(permissions);
}

// Контрагенты берутся из справочника техники: его видят те же роли, что и форму договора.
export function canUseCounterparties(permissions: readonly string[]): boolean {
  return permissions.includes("equipment:read:rates") || permissions.includes("equipment:write");
}

// Колонка суммы нужна, только если сервер отдал хоть одну сумму: без неё колонки в DOM нет.
export function canSeeAmountColumn(rows: readonly { amount: string | null }[]): boolean {
  return rows.some((row) => row.amount !== null);
}

// Внутренним суммы показываем только с documents:read:financial (прораб и мастер их не видят);
// внешние роли видят то, что отдал сервер по их видимости (ADR-015, часть 2, раздел 7).
export function canShowAmounts(permissions: readonly string[], internal: boolean): boolean {
  return !internal || canReadFinancial(permissions);
}
