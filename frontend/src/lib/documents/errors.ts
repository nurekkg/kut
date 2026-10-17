// Код ответа или ошибка загрузки -> ключ текста в Documents.errors. Чистая функция: её проверяют unit-тесты.
// Коды API приходят с префиксом "document." (document.macros_forbidden) или без него (storage_unavailable);
// префикс снимаем. Коды equipment.* переводятся тем же списком, потому что карточка техники
// показывает их рядом с документами.
const KEYS = {
  forbidden: "forbidden",
  not_found: "notFound",
  validation: "validation",
  storage_unavailable: "storageUnavailable",
  macros_forbidden: "macrosForbidden",
  active_content: "activeContent",
  too_large: "tooLarge",
  type_mismatch: "typeMismatch",
  type_not_allowed: "typeNotAllowed",
  pdf_unreadable: "pdfUnreadable",
  checksum_missing: "checksumMissing",
  not_uploaded: "notUploaded",
  key_invalid: "keyInvalid",
  archived: "archived",
  export_too_large: "exportTooLarge",
  file_already_confirmed: "fileAlreadyConfirmed",
  kind_invalid: "kindInvalid",
  scope_invalid: "scopeInvalid",
  counterparty_required: "counterpartyRequired",
  contract_required: "contractRequired",
  link_required: "linkRequired",
  legal_entity_code_missing: "legalEntityCodeMissing",
  insecure_context: "insecureContext",
  empty: "empty",
  version_not_draft: "versionNotDraft",
  amount_invalid: "amountInvalid",
  amount_not_financial: "amountNotFinancial",
  amount_exists: "amountExists",
  amount_delete_forbidden: "amountDeleteForbidden",
  "equipment.document_kind_mismatch": "equipmentDocumentKindMismatch",
  "equipment.document_unit_mismatch": "equipmentDocumentUnitMismatch",
} as const;

export type DocumentErrorKey = (typeof KEYS)[keyof typeof KEYS] | "unknown" | "network";

const PREFIX = "document.";

export function documentErrorKey(code: string): DocumentErrorKey {
  const bare = code.startsWith(PREFIX) ? code.slice(PREFIX.length) : code;
  return Object.hasOwn(KEYS, bare) ? KEYS[bare as keyof typeof KEYS] : "unknown";
}
