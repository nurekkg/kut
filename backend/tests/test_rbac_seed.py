import re
from copy import deepcopy

import pytest
import yaml
from pydantic import ValidationError
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.modules.objects.models import ProjectStatus
from app.modules.org.models import CounterpartyType
from app.modules.rbac.enums import (
    QUALIFIED_PERMISSIONS,
    Module,
    RoleScope,
    expected_catalog,
)
from app.modules.rbac.seed import SEED_FILE, RbacSpec, load_spec, sync_rbac

# Таблица ТЗ §7.3 слово в слово: второй, независимый от YAML источник для сверки.
TZ_MATRIX = """
| Объекты | R/W | R | R/W | R/W (свой) | R (свой) | R | R | R | R | R | R (свой) | R (свой) | — |
| ГПР | R | R | R/W | R/W | R | R/W | — | — | — | — | R | R (свой) | — |
| Факт работ | R | — | R | R/W | R/W | R | — | — | — | — | R | R (свой) | — |
| Документы | R | R | R/W | R/W | R | R/W | R/W | R | R/W | R/W | R | R (свой) | R (свой) |
| Материалы | R | R | R | R/W | R/W | R | R/W | R/W | R | — | — | — | — |
| Закупки | R | R | R | R/W | R/W | — | R/W | R | R | — | — | — | R (свой) |
| Субподряд | R | R | R/W | R/W | — | R/W | — | — | R | R | — | R (свой) | — |
| Финансы | R | R/W | R | R | — | — | R | — | R/W | R | — | — | R (свой) |
| Отчёты | R | R | R | R | R | R | R | R | R | R | R | R (свой) | R (свой) |
"""
TZ_ROLES = [
    "owner",
    "finance_director",
    "technical_director",
    "foreman",
    "master",
    "estimator",
    "procurement",
    "storekeeper",
    "accountant",
    "lawyer",
    "customer",
    "subcontractor",
    "supplier",
]
TZ_MODULES = {
    "Объекты": "objects",
    "ГПР": "gpr",
    "Факт работ": "work_actual",
    "Документы": "documents",
    "Материалы": "materials",
    "Закупки": "procurement",
    "Субподряд": "subcontract",
    "Финансы": "finance",
    "Отчёты": "reports",
}


def tz_grants() -> set[tuple[str, str, str, None]]:
    grants: set[tuple[str, str, str, None]] = set()
    for line in TZ_MATRIX.strip().splitlines():
        title, *cells = (cell.strip() for cell in line.strip().strip("|").split("|"))
        assert len(cells) == len(TZ_ROLES), title
        for role, cell in zip(TZ_ROLES, cells, strict=True):
            actions = {"—": (), "R": ("read",), "R/W": ("read", "write")}[
                cell.removesuffix(" (свой)")
            ]
            grants.update((role, TZ_MODULES[title], action, None) for action in actions)
    return grants


EXTRA_GRANTS = {
    ("technical_director", "work_actual", "write", "after_approval"),
    ("technical_director", "materials", "approve", "limit_override"),
    ("owner", "settings", "read", None),
    ("owner", "settings", "write", None),
    ("owner", "settings", "admin", None),
    # Справочник видов работ (S2): пишут владелец, техдир и ПТО, читают все внутренние роли.
    *{
        (role, "works", "read", None)
        for role in (
            "owner",
            "finance_director",
            "technical_director",
            "foreman",
            "master",
            "estimator",
            "procurement",
            "storekeeper",
            "accountant",
            "lawyer",
        )
    },
    ("owner", "works", "write", None),
    ("technical_director", "works", "write", None),
    ("estimator", "works", "write", None),
    # Факт работ (S3a, решения PO): подтверждают прораб и техдиректор; факт техдиректора создаётся
    # сразу согласованным, поэтому ему нужна и запись (в §7.3 у него «R»).
    ("foreman", "work_actual", "approve", None),
    ("technical_director", "work_actual", "write", None),
    ("technical_director", "work_actual", "approve", None),
}


# Модуль «Техника» (ADR-014 часть 2 §5) - отдельный источник, записанный из строк ADR, а не из YAML.
ADR_EQUIPMENT_ROLES = ("equipment_head", "mechanic", "machinist")
ADR_EQUIPMENT_GRANTS = {
    *{
        (role, "equipment", "read", None)
        for role in (
            "owner",
            "finance_director",
            "technical_director",
            "equipment_head",
            "estimator",
            "foreman",
            "master",
            "accountant",
            "mechanic",
            "machinist",
        )
    },
    ("technical_director", "equipment", "write", None),
    ("equipment_head", "equipment", "write", None),
    # Начальник отдела видит объекты (решение PO, SE-2a): реестр и назначения по объекту. Бюджет,
    # заказчик и адрес остаются закрыты: их даёт finance:read, а у начальника его нет.
    ("equipment_head", "objects", "read", None),
    ("foreman", "equipment", "write", "request"),
    ("technical_director", "equipment", "approve", "assign"),
    ("equipment_head", "equipment", "approve", "assign"),
    ("foreman", "equipment", "write", "shift"),
    ("master", "equipment", "write", "shift"),
    ("machinist", "equipment", "write", "shift"),
    ("technical_director", "equipment", "approve", "shift"),
    ("foreman", "equipment", "approve", "shift"),
    ("technical_director", "equipment", "write", "after_approval"),
    ("finance_director", "equipment", "approve", "rate"),
    *{
        (role, "equipment", "read", "rates")
        for role in (
            "owner",
            "finance_director",
            "technical_director",
            "equipment_head",
            "accountant",
            "lawyer",
        )
    },
    *{
        (role, "equipment", "write", "maintenance")
        for role in ("technical_director", "equipment_head", "mechanic")
    },
}


async def db_grants(engine: AsyncEngine) -> set[tuple[str, str, str, str | None]]:
    async with engine.connect() as connection:
        rows = await connection.execute(
            text(
                "SELECT r.code, p.module, p.action, p.qualifier FROM role_permission rp"
                " JOIN role r ON r.id = rp.role_id JOIN permission p ON p.id = rp.permission_id"
                " WHERE rp.deleted_at IS NULL"
            )
        )
        return {tuple(row) for row in rows}  # type: ignore[misc]


async def test_the_database_holds_exactly_the_tz_matrix_plus_the_documented_extras(
    owner_engine: AsyncEngine,
) -> None:
    assert await db_grants(owner_engine) == (
        tz_grants() | EXTRA_GRANTS | ADR_EQUIPMENT_GRANTS | ADR_DOCUMENT_GRANTS
    )


async def test_there_are_thirteen_internal_and_three_external_roles_with_the_agreed_scopes(
    owner_engine: AsyncEngine,
) -> None:
    async with owner_engine.connect() as connection:
        rows = {
            row.code: (row.scope, row.requires_2fa, row.is_system)
            for row in await connection.execute(
                text(
                    "SELECT code, scope, requires_2fa, is_system FROM role WHERE deleted_at IS NULL"
                )
            )
        }
    assert set(rows) == set(TZ_ROLES) | set(ADR_EQUIPMENT_ROLES)
    internal = {code for code, (scope, _f, _s) in rows.items() if scope != "counterparty"}
    external = {code for code, (scope, _f, _s) in rows.items() if scope == "counterparty"}
    assert (len(internal), external) == (13, {"customer", "subcontractor", "supplier"})
    assert {code for code, (scope, _f, _s) in rows.items() if scope == "group"} == {
        "owner",
        "finance_director",
        "technical_director",  # решение PO после S1a
        "equipment_head",  # ADR-014 (Q8)
        "mechanic",  # ADR-014, допущение rev2: парк общий
    }
    assert all(is_system for _scope, _tfa, is_system in rows.values())


async def test_two_factor_is_required_for_owner_finance_director_and_accountant_only(
    owner_engine: AsyncEngine,
) -> None:
    async with owner_engine.connect() as connection:
        codes = set(await connection.scalars(text("SELECT code FROM role WHERE requires_2fa")))
    assert codes == {"owner", "finance_director", "accountant"}


# ADR-015 часть 2 раздел 6 и решения PO Q2, Q3: право на финансовые виды и архив документов.
ADR_DOCUMENT_GRANTS = {
    ("owner", "documents", "read", "financial"),
    ("finance_director", "documents", "read", "financial"),
    ("technical_director", "documents", "read", "financial"),
    ("accountant", "documents", "read", "financial"),
    ("lawyer", "documents", "read", "financial"),
    ("estimator", "documents", "read", "financial"),
    ("owner", "documents", "write", "archive"),
    ("owner", "documents", "export", None),
    ("finance_director", "documents", "export", None),
    ("accountant", "documents", "export", None),
    ("lawyer", "documents", "export", None),
}


async def test_qualified_rights_are_exactly_the_documented_ones(
    owner_engine: AsyncEngine,
) -> None:
    qualified = {grant for grant in await db_grants(owner_engine) if grant[3] is not None}
    assert qualified == {
        ("technical_director", "work_actual", "write", "after_approval"),
        ("technical_director", "materials", "approve", "limit_override"),
    } | {grant for grant in ADR_EQUIPMENT_GRANTS if grant[3] is not None} | {
        grant for grant in ADR_DOCUMENT_GRANTS if grant[3] is not None
    }


async def test_external_roles_are_read_only(owner_engine: AsyncEngine) -> None:
    external = {"customer", "subcontractor", "supplier"}
    writes = [
        grant
        for grant in await db_grants(owner_engine)
        if grant[0] in external and grant[2] != "read"
    ]
    assert writes == []


# --- каталог прав и enum -----------------------------------------------------------------------


async def test_the_permission_catalog_matches_the_enums_in_code(owner_engine: AsyncEngine) -> None:
    async with owner_engine.connect() as connection:
        rows = await connection.execute(
            text("SELECT module, action, qualifier FROM permission WHERE deleted_at IS NULL")
        )
        catalog = {tuple(row) for row in rows}
    assert catalog == expected_catalog()
    assert {module for module, _a, _q in catalog} == {module.value for module in Module}
    assert {q for *_m, q in catalog if q} == {q for *_rest, q in QUALIFIED_PERMISSIONS}


async def test_check_constraints_list_exactly_the_values_of_their_enums(
    owner_engine: AsyncEngine,
) -> None:
    async with owner_engine.connect() as connection:
        definitions = {
            row.conname: row.definition
            for row in await connection.execute(
                text(
                    "SELECT conname, pg_get_constraintdef(oid) AS definition FROM pg_constraint"
                    " WHERE conname IN ('ck_counterparty_types_valid', 'ck_role_scope_valid',"
                    " 'ck_project_status_valid')"
                )
            )
        }

    def values(name: str) -> set[str]:
        return set(re.findall(r"'([a-z_]+)'", definitions[name]))

    assert values("ck_counterparty_types_valid") == {kind.value for kind in CounterpartyType}
    assert values("ck_role_scope_valid") == {scope.value for scope in RoleScope}
    assert values("ck_project_status_valid") == {status.value for status in ProjectStatus}


# --- идемпотентность загрузчика ----------------------------------------------------------------


async def count_audit(worker_engine: AsyncEngine) -> int:
    async with worker_engine.connect() as connection:
        return int(
            await connection.scalar(
                text(
                    "SELECT count(*) FROM audit_log"
                    " WHERE table_name IN ('role', 'permission', 'role_permission')"
                )
            )
            or 0
        )


async def test_a_second_load_changes_nothing_and_writes_no_journal_rows(
    owner_sync_engine: Engine, owner_engine: AsyncEngine, worker_engine: AsyncEngine
) -> None:
    before_grants = await db_grants(owner_engine)
    before_audit = await count_audit(worker_engine)

    with owner_sync_engine.begin() as connection:
        report = sync_rbac(connection, load_spec())

    assert not report.changed, report
    assert await db_grants(owner_engine) == before_grants
    assert await count_audit(worker_engine) == before_audit
    assert before_audit > 0, "the first load must have been journaled"


def test_a_removed_grant_is_soft_deleted_and_restored_by_the_next_load(
    owner_sync_engine: Engine,
) -> None:
    spec = load_spec()
    matrix = {module: dict(row) for module, row in spec.matrix.items()}
    matrix[Module.OBJECTS]["foreman"] = "R"
    trimmed = spec.model_copy(update={"matrix": matrix})

    with owner_sync_engine.connect() as connection:
        transaction = connection.begin()
        removed = sync_rbac(connection, trimmed)
        restored = sync_rbac(connection, spec)
        left_as_history = connection.scalar(
            text(
                "SELECT count(*) FROM role_permission rp"
                " JOIN permission p ON p.id = rp.permission_id"
                " JOIN role r ON r.id = rp.role_id"
                " WHERE r.code = 'foreman' AND p.module = 'objects'"
                " AND p.action = 'write' AND rp.deleted_at IS NOT NULL"
            )
        )
        transaction.rollback()

    assert (removed.grants_removed, removed.grants_added) == (1, 0)
    assert (restored.grants_removed, restored.grants_added) == (0, 1)
    assert left_as_history == 1


# --- проверка YAML -----------------------------------------------------------------------------


def raw_spec() -> dict[str, object]:
    with SEED_FILE.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)  # type: ignore[no-any-return]


def test_the_shipped_yaml_is_valid() -> None:
    spec = RbacSpec.model_validate(raw_spec())
    assert len(spec.roles) == 16
    # Модуль «Техника» (ADR-014) вне ТЗ §7.3: его строка есть, ТЗ-модули - те же.
    assert set(spec.matrix) == {Module(value) for value in TZ_MODULES.values()} | {Module.EQUIPMENT}


@pytest.mark.parametrize(
    "mutation",
    [
        lambda raw: raw["matrix"].update({"nonsense": {}}),
        lambda raw: raw["matrix"]["objects"].pop("foreman"),
        lambda raw: raw["matrix"]["objects"].update({"foreman": "W"}),
        lambda raw: raw["matrix"]["objects"].update({"ghost": "R"}),
        lambda raw: raw["extra"].append({"role": "owner", "permission": "finance:fly"}),
        lambda raw: raw["extra"].append({"role": "nobody", "permission": "finance:read"}),
        lambda raw: raw["roles"].append(deepcopy(raw["roles"][0])),
        lambda raw: raw["roles"][0].update({"scope": "galaxy"}),
        lambda raw: raw.update({"version": 2}),
    ],
)
def test_a_broken_yaml_is_rejected(mutation: object) -> None:
    raw = raw_spec()
    mutation(raw)  # type: ignore[operator]
    with pytest.raises(ValidationError):
        RbacSpec.model_validate(raw)
