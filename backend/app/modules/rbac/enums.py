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
