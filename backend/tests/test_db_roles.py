import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.context import use_context
from app.core.db import get_engine, get_portal_engine, get_sessionmaker
from tests.support import World, internal

LOGINS = ("kut_owner", "kut_app", "kut_portal", "kut_worker")
FLAGS = (
    "SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls"
    " FROM pg_roles WHERE rolname = current_user"
)


async def _role_flags(engine: AsyncEngine) -> tuple[str, bool, bool, bool, bool, bool]:
    async with engine.connect() as connection:
        row = (await connection.execute(text(FLAGS))).one()
    return tuple(row)  # type: ignore[return-value]


async def _tables(owner_engine: AsyncEngine) -> list[str]:
    async with owner_engine.connect() as connection:
        rows = await connection.execute(
            text(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')"
                " AND NOT c.relispartition AND c.relname <> 'alembic_version' ORDER BY 1"
            )
        )
        return [row.relname for row in rows]


async def _has(owner_engine: AsyncEngine, login: str, table: str, privilege: str) -> bool:
    async with owner_engine.connect() as connection:
        return bool(
            (
                await connection.execute(
                    text("SELECT has_table_privilege(:login, :table, :privilege)"),
                    {"login": login, "table": f'public."{table}"', "privilege": privilege},
                )
            ).scalar_one()
        )


async def test_no_login_is_a_superuser_and_only_the_worker_bypasses_rls(
    test_database: None, owner_engine: AsyncEngine, worker_engine: AsyncEngine
) -> None:
    engines = {
        "kut_owner": owner_engine,
        "kut_app": get_engine(),
        "kut_portal": get_portal_engine(),
        "kut_worker": worker_engine,
    }
    for login, engine in engines.items():
        name, superuser, createdb, createrole, replication, bypassrls = await _role_flags(engine)
        assert name == login
        assert (superuser, createdb, createrole, replication) == (False, False, False, False), login
        assert bypassrls is (login == "kut_worker"), login


async def test_logins_are_not_members_of_each_other_and_cannot_create_objects(
    owner_engine: AsyncEngine,
) -> None:
    async with owner_engine.connect() as connection:
        for login in LOGINS:
            for other in LOGINS:
                if login != other:
                    member = await connection.scalar(
                        text("SELECT pg_has_role(:a, :b, 'MEMBER')"), {"a": login, "b": other}
                    )
                    assert member is False, f"{login} is a member of {other}"
        for login in ("kut_app", "kut_portal", "kut_worker"):
            for schema in ("public", "app"):
                can_create = await connection.scalar(
                    text("SELECT has_schema_privilege(:login, :schema, 'CREATE')"),
                    {"login": login, "schema": schema},
                )
                assert can_create is False, f"{login} can create objects in {schema}"


async def test_the_portal_login_has_no_write_privileges_anywhere(owner_engine: AsyncEngine) -> None:
    for table in await _tables(owner_engine):
        for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            assert not await _has(owner_engine, "kut_portal", table, privilege), (table, privilege)


async def test_application_logins_never_get_delete_or_truncate(owner_engine: AsyncEngine) -> None:
    for table in await _tables(owner_engine):
        for login in ("kut_app", "kut_portal", "kut_worker"):
            for privilege in ("DELETE", "TRUNCATE"):
                assert not await _has(owner_engine, login, table, privilege), (login, table)


async def test_catalogs_and_the_journal_are_read_only_for_application_logins(
    owner_engine: AsyncEngine,
) -> None:
    for table in ("role", "permission", "role_permission", "audit_log"):
        for login in ("kut_app", "kut_portal", "kut_worker"):
            for privilege in ("INSERT", "UPDATE"):
                assert not await _has(owner_engine, login, table, privilege), (login, table)


async def test_audit_log_is_append_only_even_for_its_owner(owner_engine: AsyncEngine) -> None:
    for privilege in ("UPDATE", "DELETE", "TRUNCATE"):
        assert not await _has(owner_engine, "kut_owner", "audit_log", privilege)


async def test_nobody_but_the_owner_can_touch_audit_partitions_directly(
    owner_engine: AsyncEngine,
) -> None:
    async with owner_engine.connect() as connection:
        partitions = [
            row.relname
            for row in await connection.execute(
                text(
                    "SELECT c.relname FROM pg_class c JOIN pg_inherits i ON i.inhrelid = c.oid"
                    " WHERE i.inhparent = 'public.audit_log'::regclass"
                )
            )
        ]
    assert len(partitions) >= 25  # default + текущий месяц ± окно вперёд
    for partition in partitions:
        for login in ("kut_app", "kut_portal", "kut_worker"):
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                assert not await _has(owner_engine, login, partition, privilege), (
                    login,
                    partition,
                )
        for privilege in ("UPDATE", "DELETE", "TRUNCATE"):
            assert not await _has(owner_engine, "kut_owner", partition, privilege), partition


async def test_the_password_hash_is_not_readable_by_the_app_and_portal_logins(
    owner_engine: AsyncEngine,
) -> None:
    """Пароль читают только функции app.* (kut_auth): ни kut_app, ни kut_portal, ни kut_worker."""
    async with owner_engine.connect() as connection:
        results = {
            login: await connection.scalar(
                text(
                    "SELECT has_column_privilege(:login, 'public.\"user\"',"
                    " 'password_hash', 'SELECT')"
                ),
                {"login": login},
            )
            for login in ("kut_app", "kut_portal", "kut_worker")
        }
    assert results == {"kut_app": False, "kut_portal": False, "kut_worker": False}


BASE_COLUMNS = {"id", "created_at", "updated_at", "deleted_at"}
PORTAL_COLUMNS = {
    # Документы (ADR-015 часть 2 раздел 7.5): без номера, автора, ключа файла и комментариев.
    "document": BASE_COLUMNS
    | {"kind", "title", "project_id", "counterparty_id", "financial", "current_version_id"},
    "document_version": BASE_COLUMNS
    | {"document_id", "version_no", "status", "content_type", "size_bytes"},
    "document_amount": BASE_COLUMNS | {"document_id", "amount"},
    "document_version_amount": BASE_COLUMNS | {"version_id", "document_id", "amount"},
    # S6a-1: позиции версии - порталу только у утверждённых версий (политика); цена - как сумма версии.
    "document_line": BASE_COLUMNS
    | {
        "document_id",
        "version_id",
        "position_no",
        "name",
        "unit",
        "quantity",
        "work_id",
        "project_node_id",
    },
    "document_line_price": BASE_COLUMNS | {"line_id", "document_id", "version_id", "unit_price"},
    "user": BASE_COLUMNS
    | {"phone", "email", "full_name", "role_id", "counterparty_id", "is_active", "role_ver"},
    "project": BASE_COLUMNS | {"legal_entity_id", "name", "status"},
    "counterparty": BASE_COLUMNS | {"name", "tax_id", "types"},
    "user_project_access": BASE_COLUMNS | {"user_id", "project_id", "role_id"},
    # S2: без ответственных (foreman/master), юрлица и служебных created_by/updated_by.
    "project_node": BASE_COLUMNS
    | {
        "project_id",
        "parent_id",
        "type",
        "name",
        "code",
        "sort_order",
        "path",
        "planned_start",
        "planned_end",
        "polygon",
    },
    # S3a: ГПР и подтверждённый факт своего объекта - без ответственного, автора, геометок и
    # служебных created_by/updated_by; справочник работ - только колонки названия и единицы (строки
    # политика открывает ровно для видов работ из ГПР своих объектов).
    "schedule_item": BASE_COLUMNS
    | {"project_id", "node_id", "work_id", "plan_start", "plan_end", "plan_volume", "weight"},
    "schedule_dependency": BASE_COLUMNS | {"project_id", "predecessor_id", "successor_id"},
    "work_actual": BASE_COLUMNS
    | {
        "project_id",
        "node_id",
        "work_id",
        "schedule_item_id",
        "volume",
        "work_date",
        "status",
        "approved_at",
    },
    "work": BASE_COLUMNS | {"code", "name", "unit", "category"},
}


async def test_the_portal_login_reads_only_explicitly_listed_columns(
    owner_engine: AsyncEngine,
) -> None:
    """Колонки, добавленные позже, закрыты для портала, пока их не откроют намеренно."""
    async with owner_engine.connect() as connection:
        for table in await _tables(owner_engine):
            columns = [
                row.attname
                for row in await connection.execute(
                    text(
                        "SELECT a.attname FROM pg_attribute a"
                        " WHERE a.attrelid = CAST(:table AS regclass) AND a.attnum > 0"
                        " AND NOT a.attisdropped"
                    ),
                    {"table": f'public."{table}"'},
                )
            ]
            readable = set()
            for column in columns:
                allowed = await connection.scalar(
                    text(
                        "SELECT has_column_privilege('kut_portal', CAST(:table AS regclass),"
                        " :column, 'SELECT')"
                    ),
                    {"table": f'public."{table}"', "column": column},
                )
                if allowed:
                    readable.add(column)
            if table in PORTAL_COLUMNS:
                assert readable == PORTAL_COLUMNS[table], table
                assert not await _has(owner_engine, "kut_portal", table, "SELECT"), (
                    f"{table}: table-wide SELECT would expose columns added later"
                )
                assert not readable & {"created_by", "updated_by", "password_hash"}
            elif table in ("role", "permission", "role_permission"):
                assert readable == set(columns), table  # справочники: без чувствительных колонок
            else:
                assert readable == set(), f"{table} must be invisible to the portal login"


async def test_schema_functions_are_executable_only_by_the_logins_that_need_them(
    owner_engine: AsyncEngine,
) -> None:
    context_functions = [
        f"app.{name}()"
        for name in (
            "user_id",
            "is_internal",
            "project_ids",
            "legal_entity_ids",
            "counterparty_id",
            "request_id",
            "actor_kind",
        )
    ]
    internal_functions = [
        "app.audit_insert()",
        "app.audit_update()",
        "app.audit_delete()",
        "app.audit_log_immutable()",
    ]
    async with owner_engine.connect() as connection:

        async def can(login: str, function: str) -> bool:
            return bool(
                await connection.scalar(
                    text("SELECT has_function_privilege(:login, :fn, 'EXECUTE')"),
                    {"login": login, "fn": function},
                )
            )

        for login in ("kut_app", "kut_portal", "kut_worker"):
            for function in context_functions:
                assert await can(login, function), (login, function)
            for function in internal_functions:
                assert not await can(login, function), (login, function)
        assert await can("kut_worker", "app.ensure_audit_partitions(integer)")
        assert not await can("kut_app", "app.ensure_audit_partitions(integer)")
        assert not await can("kut_portal", "app.ensure_audit_partitions(integer)")


async def test_a_write_through_the_portal_pool_is_refused(world: World) -> None:
    async with get_portal_engine().connect() as connection:
        with pytest.raises(ProgrammingError, match="permission denied"):
            await connection.execute(
                text("INSERT INTO counterparty (name, types) VALUES ('x', ARRAY['supplier'])")
            )


async def test_the_portal_pool_cannot_select_the_password_hash(world: World) -> None:
    async with get_portal_engine().connect() as connection:
        with pytest.raises(ProgrammingError, match="permission denied"):
            await connection.execute(text('SELECT password_hash FROM "user"'))


async def test_the_application_login_cannot_become_another_role(test_database: None) -> None:
    async with get_engine().connect() as connection:
        for target in ("kut_owner", "kut_worker"):
            with pytest.raises(DBAPIError, match="permission denied"):
                await connection.execute(text(f"SET ROLE {target}"))
            await connection.rollback()


async def test_the_application_login_cannot_alter_policies_or_the_schema(
    test_database: None,
) -> None:
    async with get_engine().connect() as connection:
        for statement in (
            "ALTER TABLE project DISABLE ROW LEVEL SECURITY",
            "DROP POLICY project_select_app ON project",
            "CREATE TABLE public.sneaky (id int)",
            "DROP FUNCTION app.is_internal()",
        ):
            with pytest.raises(DBAPIError, match=r"must be owner|permission denied"):
                await connection.execute(text(statement))
            await connection.rollback()


async def test_app_writes_without_delete_privilege_cannot_hard_delete(world: World) -> None:
    with use_context(internal(world.owner_user, projects=[world.p1], legal_entities=[world.le1])):
        async with get_sessionmaker()() as session:
            with pytest.raises(ProgrammingError, match="permission denied"):
                await session.execute(text("DELETE FROM project WHERE id = :id"), {"id": world.p1})
