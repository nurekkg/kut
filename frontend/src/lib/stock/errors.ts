// Код ошибки склада/справочника -> ключ текста в StockErrors (messages/ru.json). Чистая функция:
// её проверяют unit-тесты. Неизвестный код получает общий текст, а не сырой код из API.
const KEYS: Record<string, string> = {
  "stock.material_required": "materialRequired",
  "stock.receipt_consumed": "receiptConsumed",
  "stock.batch_required": "batchRequired",
  "stock.batch_not_applicable": "batchNotApplicable",
  "document.file_required": "fileRequired",
  "material.certificate_required": "certificateRequired",
  "stock.insufficient": "insufficient",
  "stock.version_not_draft": "versionNotDraft",
  "document.version_not_draft": "versionNotDraft",
  "document.lines_not_draft": "versionNotDraft",
  "approval.version_not_draft": "versionNotDraft",
  "stock.issue_incomplete": "issueIncomplete",
  "stock.already_reversed": "alreadyReversed",
  "stock.reversal_of_reversal": "reversalOfReversal",
  "stock.warehouse_project": "warehouseProject",
  "stock.warehouse_required": "warehouseRequired",
  "stock.kind_invalid": "kindInvalid",
  "stock.forbidden": "forbidden",
  "stock.not_found": "notFound",
  "material.unit_unknown": "unitUnknown",
  "material.import_invalid": "importInvalid",
  "material.import_header": "importHeader",
  "material.import_empty": "importEmpty",
  "material.import_too_large": "importTooLarge",
  "material.unit_is_base": "unitIsBase",
  "material.code_taken": "codeTaken",
  "material.unit_taken": "unitTaken",
  "material.batch_taken": "batchTaken",
  "material.price_taken": "priceTaken",
  "material.certificate_invalid": "certificateInvalid",
  "stock.append_only": "appendOnly",
  "stock.qty_invalid": "qtyInvalid",
  "warehouse.name_taken": "warehouseNameTaken",
  "forbidden": "forbidden",
  "not_found": "notFound",
  "validation": "validation",
  "inventory.not_counted": "notCounted",
  "inventory.line_incomplete": "inventoryLineIncomplete",
  "inventory.duplicate_line": "inventoryDuplicate",
  "inventory.batch_required": "batchRequired",
  "inventory.no_snapshot": "inventoryNoSnapshot",
  "inventory.negative": "inventoryNegative",
  "inventory.already_filled": "inventoryAlreadyFilled",
  "inventory.not_inventory": "inventoryNotInventory",
  "stock.adjustment_consumed": "adjustmentConsumed",
  "stock.adjustment_reverse": "adjustmentReverse",
  "stock.already_adjusted": "alreadyAdjusted",
  "document.line_quantity_invalid": "lineQuantityInvalid",
};

export function stockErrorKey(code: string): string | null {
  return KEYS[code] ?? null;
}

// Имена ключей текстов, которые обязаны быть в ru.json: проверяет unit-тест (защита от опечек).
export const STOCK_ERROR_KEY_NAMES: readonly string[] = [...new Set(Object.values(KEYS))];
