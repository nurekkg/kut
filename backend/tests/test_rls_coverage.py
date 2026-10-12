import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.support import World

SCOPE_COLUMNS = {"project_id", "counterparty_id", "legal_entity_id"}

# Таблицы без RLS: причина обязательна. Остальные таблицы схемы public обязаны иметь
# ENABLE + FORCE ROW LEVEL SECURITY и хотя бы одну политику.
RLS_ALLOWLIST = {
    "role": "глобальный справочник ролей: нет scope-колонок и личных данных; пишет только сид",
    "permission": "глобальный справочник прав: нет scope-колонок и личных данных; пишет только сид",
    "role_permission": "связь справочников role и permission: нет scope-колонок; пишет только сид",
}

TABLES_SQL = text(
    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,"
    " (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) AS policies,"
    " ARRAY(SELECT a.attname FROM pg_attribute a"
    "       WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped) AS columns"
    " FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
    " WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND NOT c.relispartition"
    " AND c.relname <> 'alembic_version' ORDER BY c.relname"
)


async def _tables(owner_engine: AsyncEngine) -> list[tuple[str, bool, bool, int, list[str]]]:
    async with owner_engine.connect() as connection:
        return [tuple(row) for row in await connection.execute(TABLES_SQL)]  # type: ignore[misc]


async def test_every_table_forces_rls_unless_allowlisted_with_a_reason(
    owner_engine: AsyncEngine,
) -> None:
    problems = []
    for name, enabled, forced, policies, _columns in await _tables(owner_engine):
        if name in RLS_ALLOWLIST:
            continue
        if not (enabled and forced):
            problems.append(f"{name}: relrowsecurity={enabled} relforcerowsecurity={forced}")
        if policies == 0:
            problems.append(f"{name}: RLS without a single policy")
    assert not problems, "\n".join(problems)


async def test_the_allowlist_is_honest(owner_engine: AsyncEngine) -> None:
    tables = {
        name: (enabled, columns) for name, enabled, _f, _p, columns in await _tables(owner_engine)
    }
    assert set(RLS_ALLOWLIST) <= set(tables), "stale allowlist entry"
    for name, reason in RLS_ALLOWLIST.items():
        enabled, columns = tables[name]
        assert reason.strip()
        assert not SCOPE_COLUMNS & set(columns), f"{name} has a scope column: it needs RLS"
        assert not enabled, f"{name} has RLS now: drop it from the allowlist"


async def test_tables_with_scope_columns_are_never_allowlisted(owner_engine: AsyncEngine) -> None:
    for name, _enabled, _forced, _policies, columns in await _tables(owner_engine):
        if SCOPE_COLUMNS & set(columns):
            assert name not in RLS_ALLOWLIST, name


@pytest.mark.parametrize(
    "table",
    [
        "legal_entity",
        "counterparty",
        "user",
        "project",
        "user_project_access",
        "user_legal_entity_access",
        "project_node",
        "work",
        "project_finance",
        "schedule_item",
        "schedule_dependency",
        "work_actual",
        "work_photo",
        "equipment_type",
        "contract",
        "equipment_unit",
        "equipment_own_rate",
        "equipment_rate",
        "equipment_operator",
        "equipment_request",
        "equipment_assignment",
        "equipment_maintenance",
        "equipment_document",
        "equipment_import_batch",
    ],
)
async def test_force_rls_constrains_the_table_owner_too(
    owner_engine: AsyncEngine, world: World, table: str
) -> None:
    # kut_owner не имеет политик: если приложение подключится под ним, строк не будет вовсе.
    async with owner_engine.connect() as connection:
        count = await connection.scalar(text(f'SELECT count(*) FROM "{table}"'))  # noqa: S608
    assert count == 0


async def test_the_owner_cannot_write_to_rls_tables_either(
    owner_engine: AsyncEngine, world: World
) -> None:
    async with owner_engine.connect() as connection:
        with pytest.raises(ProgrammingError, match="row-level security"):
            await connection.execute(
                text("INSERT INTO counterparty (name, types) VALUES ('x', ARRAY['supplier'])")
            )
