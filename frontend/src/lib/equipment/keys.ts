// Ключи запросов модуля «Техника». Любая запись сбрасывает весь префикс: связанные экраны
// (реестр, карточка, календарь, заявки, ТО) читают одни и те же назначения и статусы.
export const EQUIPMENT_KEY = ["equipment"] as const;

export const typesKey = [...EQUIPMENT_KEY, "types"] as const;
export const unitsKey = (scope: Record<string, string | undefined>) =>
  [...EQUIPMENT_KEY, "units", scope] as const;
export const unitKey = (unitId: string) => [...EQUIPMENT_KEY, "unit", unitId] as const;
export const assignmentsKey = (scope: Record<string, string | undefined>) =>
  [...EQUIPMENT_KEY, "assignments", scope] as const;
export const requestsKey = (scope: Record<string, string | undefined>) =>
  [...EQUIPMENT_KEY, "requests", scope] as const;
export const maintenanceKey = (status: string) => [...EQUIPMENT_KEY, "maintenance", status] as const;
export const calendarKey = (dateFrom: string, dateTo: string, typeId: string | undefined) =>
  [...EQUIPMENT_KEY, "calendar", dateFrom, dateTo, typeId ?? "all"] as const;
export const contractsKey = [...EQUIPMENT_KEY, "contracts"] as const;
export const ratesKey = (contractId: string) => [...EQUIPMENT_KEY, "rates", contractId] as const;
export const ownRatesKey = (legalEntityId: string, typeId: string) =>
  [...EQUIPMENT_KEY, "own-rates", legalEntityId, typeId] as const;
export const operatorsKey = [...EQUIPMENT_KEY, "operators"] as const;
export const counterpartiesKey = [...EQUIPMENT_KEY, "counterparties"] as const;
export const batchesKey = [...EQUIPMENT_KEY, "batches"] as const;
