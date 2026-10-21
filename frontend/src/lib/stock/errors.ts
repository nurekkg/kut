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
  "warehouse.name_taken": "warehouseNameTaken",
  "forbidden": "forbidden",
  "not_found": "notFound",
  "validation": "validation",
};

export function stockErrorKey(code: string): string | null {
  return KEYS[code] ?? null;
}

// Имена ключей текстов, которые обязаны быть в ru.json: проверяет unit-тест (защита от опечек).
export const STOCK_ERROR_KEY_NAMES: readonly string[] = [...new Set(Object.values(KEYS))];
