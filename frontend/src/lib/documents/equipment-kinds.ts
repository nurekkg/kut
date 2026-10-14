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
