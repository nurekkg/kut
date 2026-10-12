"use client";

import { useTranslations } from "next-intl";

import { ApiError } from "@/lib/auth/api";

// Код ответа API -> ключ текста. Ключи с точкой next-intl читает как путь, поэтому код переводим
// в имя ключа явно: неизвестный код получает общий текст, а не сырой код.
const KEYS = {
  "equipment.day_hours_exceeded": "dayHoursExceeded",
  "equipment.maintenance_overlap": "maintenanceOverlap",
  "equipment.document_expired": "documentExpired",
  "equipment.contract_inactive": "contractInactive",
  "equipment.contract_not_active": "contractNotActive",
  "equipment.contract_kind_invalid": "contractKindInvalid",
  "equipment.contract_legal_entity_mismatch": "contractLegalEntityMismatch",
  "equipment.contract_frozen": "contractFrozen",
  "equipment.rate_already_approved": "rateAlreadyApproved",
  "equipment.approver_is_proposer": "approverIsProposer",
  "equipment.request_not_assignable": "requestNotAssignable",
  "equipment.request_invalid": "requestInvalid",
  "equipment.type_mismatch": "typeMismatch",
  "equipment.project_required": "projectRequired",
  "equipment.node_invalid": "nodeInvalid",
  "equipment.schedule_item_invalid": "scheduleItemInvalid",
  "equipment.type_invalid": "typeInvalid",
  "equipment.contract_invalid": "contractInvalid",
  "equipment.operator_invalid": "operatorInvalid",
  "equipment.dates_out_of_range": "datesOutOfRange",
  "equipment.dates_invalid": "datesInvalid",
  "equipment.hours_invalid": "hoursInvalid",
  "equipment.reason_required": "reasonRequired",
  "equipment.transition_forbidden": "transitionForbidden",
  "equipment.calendar_window_invalid": "calendarWindowInvalid",
  "equipment.unit_key_taken": "unitKeyTaken",
  "equipment.contract_number_taken": "contractNumberTaken",
  "equipment.rate_version_exists": "rateVersionExists",
  "equipment.operator_user_taken": "operatorUserTaken",
  "equipment.document_version_conflict": "documentVersionConflict",
  "equipment.import_format": "importFormat",
  "equipment.import_macros_forbidden": "importMacrosForbidden",
  "equipment.import_encoding": "importEncoding",
  "equipment.import_too_large": "importTooLarge",
  forbidden: "forbidden",
  not_found: "notFound",
  validation: "validation",
} as const;

type ErrorKey = (typeof KEYS)[keyof typeof KEYS] | "unknown" | "network";

export function equipmentErrorKey(code: string): ErrorKey {
  const key = (KEYS as Record<string, (typeof KEYS)[keyof typeof KEYS] | undefined>)[code];
  return key ?? "unknown";
}

export function useEquipmentErrorMessage(): (error: unknown) => string {
  const t = useTranslations("Equipment.errors");
  return (error) => {
    if (!(error instanceof ApiError)) return t("network");
    return t(equipmentErrorKey(error.code));
  };
}
