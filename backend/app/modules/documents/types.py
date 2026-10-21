"""Виды документов (ADR-015 часть 1, раздел 2; решения PO по §14).

Единственный источник правил видов в коде: CHECK-ограничения миграций повторяют эти списки
литералами (миграции заморожены), тест сверяет их с кодом.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class Scope(StrEnum):
    """Где лежит документ: объект (project_id обязателен), юрлицо (project_id пуст) или любое."""

    OBJECT = "object"
    LEGAL_ENTITY = "legal_entity"
    EITHER = "either"


class Link(StrEnum):
    """Обязательные связи вида (ADR часть 1 раздел 2): узел, вид работы, факт, техника."""

    PROJECT_NODE = "project_node"
    WORK = "work"
    WORK_ACTUAL = "work_actual"
    EQUIPMENT_UNIT = "equipment_unit"


@dataclass(frozen=True, slots=True)
class KindSpec:
    prefix: str
    scope: Scope
    financial: bool
    counterparty: bool = False
    contract: bool = False
    links: frozenset[Link] = frozenset()
    # Позиции версии (ADR-015 часть 4): виды, где версия состоит из строк работ или материалов.
    lines: bool = False


OBJECT: Final = Scope.OBJECT
LEGAL: Final = Scope.LEGAL_ENTITY
EITHER: Final = Scope.EITHER

KINDS: Final[dict[str, KindSpec]] = {
    "contract_customer": KindSpec("ДЗ", OBJECT, True, counterparty=True),
    "contract_subcontract": KindSpec("ДС", OBJECT, True, counterparty=True),
    "contract_supply": KindSpec("ДП", LEGAL, True, counterparty=True),
    "contract_rental": KindSpec("ДА", LEGAL, True, counterparty=True, contract=True),
    "addendum": KindSpec("ДОП", LEGAL, True, contract=True),
    "estimate_local": KindSpec("СМЛ", OBJECT, True, lines=True),
    "estimate_summary": KindSpec("СМС", OBJECT, True, lines=True),
    "ks2": KindSpec("КС2", OBJECT, True, counterparty=True, lines=True),
    "ks3": KindSpec("КС3", OBJECT, True, counterparty=True, lines=True),
    "ks6": KindSpec("КС6", OBJECT, True, counterparty=True, lines=True),
    "hidden_works_act": KindSpec(
        "АСР", OBJECT, False, links=frozenset({Link.PROJECT_NODE, Link.WORK})
    ),
    "ird": KindSpec("ИД", OBJECT, False, links=frozenset({Link.PROJECT_NODE, Link.WORK})),
    "waybill_torg12": KindSpec("ТОРГ12", EITHER, True, counterparty=True, lines=True),
    "waybill_m15": KindSpec("М15", EITHER, True, counterparty=True, lines=True),
    "waybill_internal": KindSpec("ВН", EITHER, False, lines=True),
    "invoice_esf": KindSpec("ЭСФ", LEGAL, True, counterparty=True),
    "m7_act": KindSpec("М7", EITHER, False, lines=True),
    "inventory_act": KindSpec("ИНВ", OBJECT, False, lines=True),
    "material_request": KindSpec(
        "ЗМ", OBJECT, False, links=frozenset({Link.PROJECT_NODE}), lines=True
    ),
    "commissioning_act": KindSpec("АВЭ", OBJECT, False),
    # Документы техники (ADR-015 часть 3, раздел 10, вариант B): вид по строке equipment_document.
    "equipment_passport": KindSpec("ПСП", LEGAL, False, links=frozenset({Link.EQUIPMENT_UNIT})),
    "equipment_technical_inspection": KindSpec(
        "ТОС", LEGAL, False, links=frozenset({Link.EQUIPMENT_UNIT})
    ),
    # Денежные виды (PO 2026-10-17, R3): файл и сумма - только по equipment:read:rates.
    "equipment_insurance": KindSpec("СТР", LEGAL, True, links=frozenset({Link.EQUIPMENT_UNIT})),
    "equipment_permit": KindSpec("РАЗ", LEGAL, False, links=frozenset({Link.EQUIPMENT_UNIT})),
    "equipment_other": KindSpec("ПРЧ", LEGAL, True, links=frozenset({Link.EQUIPMENT_UNIT})),
}

FINANCIAL_KINDS: Final = frozenset(kind for kind, spec in KINDS.items() if spec.financial)
LINE_KINDS: Final = frozenset(kind for kind, spec in KINDS.items() if spec.lines)
EQUIPMENT_KINDS: Final = frozenset(kind for kind in KINDS if kind.startswith("equipment_"))
MONEY_EQUIPMENT_KINDS: Final = frozenset({"equipment_insurance", "equipment_other"})
INSPECTION_KIND: Final = "equipment_technical_inspection"
OBJECT_KINDS: Final = frozenset(kind for kind, spec in KINDS.items() if spec.scope is OBJECT)
LEGAL_KINDS: Final = frozenset(kind for kind, spec in KINDS.items() if spec.scope is LEGAL)
COUNTERPARTY_KINDS: Final = frozenset(kind for kind, spec in KINDS.items() if spec.counterparty)
CONTRACT_KINDS: Final = frozenset(kind for kind, spec in KINDS.items() if spec.contract)

# Внешние роли (ADR часть 2 раздел 7, решение PO Q4). Заказчик видит документы, где он контрагент,
# и только утверждённые версии; субподрядчик и поставщик - свои виды (статус тот же).
CUSTOMER_KINDS: Final = frozenset(
    {"contract_customer", "ks2", "ks3", "ks6", "hidden_works_act", "ird", "commissioning_act"}
)
SUBCONTRACTOR_KINDS: Final = frozenset(
    {"contract_subcontract", "ks2", "ks3", "ks6", "hidden_works_act", "ird"}
)
# Виды, которые вносит кладовщик (ADR-016 часть 5): М-7 и внутренняя накладная (ВН).
STOCK_KINDS: Final = frozenset({"m7_act", "waybill_internal"})
INVENTORY_KIND: Final = "inventory_act"
SUPPLIER_KINDS: Final = frozenset(
    {"contract_supply", "waybill_torg12", "waybill_m15", "invoice_esf", "material_request"}
)

# Допустимые типы файлов (ADR часть 2 раздел 8.1): PDF, XLSX, DOCX, изображения как у фото.
EXTENSIONS: Final[dict[str, str]] = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/heic": "heic",
    "image/webp": "webp",
}
MAX_FILE_BYTES: Final = 30 * 1024 * 1024

VERSION_STATUSES: Final = ("draft", "on_approval", "approved", "signed", "archived")
VISIBLE_TO_EXTERNAL: Final = ("approved", "signed")

# Подписи для выгрузки XLSX (экран берёт свои из messages/ru.json).
KIND_TITLES: Final[dict[str, str]] = {
    "contract_customer": "Договор с заказчиком",
    "contract_subcontract": "Договор субподряда",
    "contract_supply": "Договор поставки",
    "contract_rental": "Договор аренды техники",
    "addendum": "Дополнительное соглашение",
    "estimate_local": "Локальная смета",
    "estimate_summary": "Сводная смета",
    "ks2": "Акт КС-2",
    "ks3": "Справка КС-3",
    "ks6": "Журнал КС-6",
    "hidden_works_act": "Акт скрытых работ",
    "ird": "Исполнительная документация",
    "waybill_torg12": "Накладная ТОРГ-12",
    "waybill_m15": "Накладная М-15",
    "waybill_internal": "Внутреннее перемещение",
    "invoice_esf": "Счёт-фактура (ЭСФ)",
    "m7_act": "Акт М-7",
    "material_request": "Заявка на материалы",
    "inventory_act": "Акт инвентаризации",
    "commissioning_act": "Акт ввода в эксплуатацию",
    "equipment_passport": "Паспорт техники",
    "equipment_technical_inspection": "Техосмотр",
    "equipment_insurance": "Страхование техники",
    "equipment_permit": "Разрешение",
    "equipment_other": "Прочий документ техники",
}
STATUS_TITLES: Final[dict[str, str]] = {
    "draft": "Черновик",
    "on_approval": "На согласовании",
    "approved": "Утверждён",
    "signed": "Подписан",
    "archived": "В архиве",
}
