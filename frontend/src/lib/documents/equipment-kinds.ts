// Вид строки срока техники -> вид документа (ADR-015 часть 3, раздел 10). Документ можно связать
// со строкой срока только того же вида; сервер проверяет это ещё раз (equipment.document_kind_mismatch).
const EQUIPMENT_DOCUMENT_KIND: Readonly<Record<string, string>> = {
  passport: "equipment_passport",
  technical_inspection: "equipment_technical_inspection",
  insurance: "equipment_insurance",
  permit: "equipment_permit",
  other: "equipment_other",
};

export function equipmentDocumentKind(kind: string): string | null {
  return Object.hasOwn(EQUIPMENT_DOCUMENT_KIND, kind) ? EQUIPMENT_DOCUMENT_KIND[kind] : null;
}

// Виды документов техники и денежные виды (R2, R3): их право - equipment:*, а не documents:*.
// Совпадает с backend/app/modules/documents/types.py (EQUIPMENT_KINDS, MONEY_EQUIPMENT_KINDS).
export const EQUIPMENT_DOCUMENT_KINDS: readonly string[] = [
  "equipment_passport",
  "equipment_technical_inspection",
  "equipment_insurance",
  "equipment_permit",
  "equipment_other",
];
export const MONEY_EQUIPMENT_DOCUMENT_KINDS: readonly string[] = ["equipment_insurance", "equipment_other"];
export const INSPECTION_DOCUMENT_KIND = "equipment_technical_inspection";

export function isEquipmentDocumentKind(kind: string): boolean {
  return EQUIPMENT_DOCUMENT_KINDS.includes(kind);
}

export function isMoneyEquipmentDocumentKind(kind: string): boolean {
  return MONEY_EQUIPMENT_DOCUMENT_KINDS.includes(kind);
}
