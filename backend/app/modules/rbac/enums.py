from enum import StrEnum
from typing import Final


class Module(StrEnum):
    OBJECTS = "objects"
    GPR = "gpr"
    WORK_ACTUAL = "work_actual"
    DOCUMENTS = "documents"
    MATERIALS = "materials"
    PROCUREMENT = "procurement"
    SUBCONTRACT = "subcontract"
    FINANCE = "finance"
    REPORTS = "reports"
    SETTINGS = "settings"
    WORKS = "works"
    EQUIPMENT = "equipment"


class Action(StrEnum):
    READ = "read"
    WRITE = "write"
    APPROVE = "approve"
    DELETE = "delete"
    EXPORT = "export"
    ADMIN = "admin"


class RoleScope(StrEnum):
    GROUP = "group"
    PROJECT = "project"
    COUNTERPARTY = "counterparty"


# Особые права сверх шести глаголов ТЗ (ADR-001 §3, решение PO по вопросу 7).
QUALIFIED_PERMISSIONS: Final[tuple[tuple[Module, Action, str], ...]] = (
    (Module.WORK_ACTUAL, Action.WRITE, "after_approval"),
    (Module.MATERIALS, Action.APPROVE, "limit_override"),
    # Техника (ADR-014 часть 2 §5, D14): восемь квалифицированных прав.
    (Module.EQUIPMENT, Action.WRITE, "request"),
    (Module.EQUIPMENT, Action.APPROVE, "assign"),
    (Module.EQUIPMENT, Action.WRITE, "shift"),
    (Module.EQUIPMENT, Action.APPROVE, "shift"),
    (Module.EQUIPMENT, Action.WRITE, "after_approval"),
    (Module.EQUIPMENT, Action.APPROVE, "rate"),
    (Module.EQUIPMENT, Action.READ, "rates"),
    (Module.EQUIPMENT, Action.WRITE, "maintenance"),
    # Документы техники (PO 2026-10-17, R2): видимость и загрузка видов equipment_*, кроме денежных.
    (Module.EQUIPMENT, Action.READ, "documents"),
    (Module.EQUIPMENT, Action.WRITE, "documents"),
    # Документы (ADR-015 часть 2 раздел 6): финансовые виды и архив.
    (Module.DOCUMENTS, Action.READ, "financial"),
    (Module.DOCUMENTS, Action.WRITE, "archive"),
    # Позиции версии (ADR-015 часть 4): вносят сметчик, снабженец, техдиректор.
    (Module.DOCUMENTS, Action.WRITE, "lines"),
    # Шаблоны маршрута согласования (S6a-2): владелец и техдиректор.
    (Module.DOCUMENTS, Action.WRITE, "routes"),
)


def expected_catalog() -> set[tuple[str, str, str | None]]:
    catalog: set[tuple[str, str, str | None]] = {
        (module.value, action.value, None) for module in Module for action in Action
    }
    catalog.update(
        (module.value, action.value, qualifier)
        for module, action, qualifier in QUALIFIED_PERMISSIONS
    )
    return catalog
