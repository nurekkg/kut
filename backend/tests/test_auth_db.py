"""S1b на уровне БД: колонковые права, роль kut_auth, SECURITY DEFINER-функции, политики."""

from collections.abc import Callable
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.context import use_context
from app.core.db import get_sessionmaker
from app.modules.identity.models import User
from tests.auth_support import AuthWorld
from tests.db_support import (
    INSUFFICIENT_PRIVILEGE,
    call,
    context,
    expect_error,
    fresh_hash,
    owner_rows,
)

USER_READABLE = {
    "id", "created_at", "updated_at", "created_by", "updated_by", "deleted_at", "phone", "email",
    "full_name", "role_id", "counterparty_id", "is_active", "role_ver", "totp_confirmed_at",
    "password_changed_at", "notify_whatsapp",
}  # fmt: skip
USER_SECRET = {"password_hash", "totp_secret_enc", "totp_key_id"}
USER_WRITABLE = {"phone", "email", "full_name", "updated_at", "updated_by", "notify_whatsapp"}

# Кому разрешён EXECUTE: независимый от миграции перечень (ADR-001 §2, S1b).
EXECUTE_GRANTS: dict[str, set[str]] = {
    "audit_event": {"kut_app"},
    "auth_login_lookup": {"kut_app"},
    "auth_password_hash": {"kut_app"},
    "auth_context": {"kut_app"},
    "refresh_issue": {"kut_app"},
    "refresh_rotate": {"kut_app"},
    "refresh_logout": {"kut_app"},
    "refresh_revoke_user": set(),
    "totp_get": {"kut_app"},
    "totp_set_pending": {"kut_app"},
    "totp_confirm": {"kut_app"},
    "recovery_code_use": {"kut_app"},
    "change_password": {"kut_app"},
    "require_settings_admin": set(),
    "assign_role": {"kut_app"},
    "set_user_active": {"kut_app"},
    "purge_expired_auth_data": {"kut_worker"},
    "admin_create_user": {"kut_app"},
    "admin_issue_invite": {"kut_app"},
    "invite_consume": {"kut_app"},
    "admin_reset_totp": {"kut_app"},
    "admin_auth_events": {"kut_app"},
}


# --- роль kut_auth ---


async def test_kut_auth_is_a_non_login_bypassrls_role_without_other_privileges(
    owner_engine: AsyncEngine,
) -> None:
    (row,) = await owner_rows(
        owner_engine,
        "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls"
        " FROM pg_roles WHERE rolname = 'kut_auth'",
    )

    assert tuple(row) == (False, False, False, False, False, True)


async def test_no_application_login_can_become_kut_auth_and_the_owner_only_via_set_role(
    owner_engine: AsyncEngine,
) -> None:
    members = await owner_rows(
        owner_engine,
        "SELECT pg_get_userbyid(member) AS member, inherit_option, set_option FROM pg_auth_members"
        " WHERE roleid = 'kut_auth'::regrole",
    )

    assert [tuple(row) for row in members] == [("kut_owner", False, True)]
    for login in ("kut_app", "kut_portal", "kut_worker"):
        (row,) = await owner_rows(
            owner_engine, "SELECT pg_has_role(:login, 'kut_auth', 'MEMBER')", login=login
        )
        assert row[0] is False, login


@pytest.mark.parametrize("table", ["refresh_token", "recovery_code", "user_invite"])
async def test_session_tables_are_closed_to_every_login_and_to_the_owner_under_force_rls(
    owner_engine: AsyncEngine, owner_sync_engine: Engine, new_user: Callable[..., Any], table: str
) -> None:
    for login in ("kut_app", "kut_portal", "kut_worker"):
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            (row,) = await owner_rows(
                owner_engine,
                "SELECT has_table_privilege(:login, :table, :privilege)",
                login=login, table=f"public.{table}", privilege=privilege,
            )  # fmt: skip
            assert row[0] is False, (login, table, privilege)
    (flags,) = await owner_rows(
        owner_engine,
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = CAST(:t AS regclass)",
        t=f"public.{table}",
    )
    assert tuple(flags) == (True, True)

    user_id, _ = new_user("foreman")
    insert = {
        "refresh_token": (
            "INSERT INTO refresh_token (user_id, family_id, token_hash, amr, expires_at)"
            " VALUES (:u, gen_random_uuid(), :h, '{pwd}', now() + interval '1 day')"
        ),
        "recovery_code": ("INSERT INTO recovery_code (user_id, code_hash) VALUES (:u, :h)"),
        "user_invite": (
            "INSERT INTO user_invite (user_id, token_hash, expires_at)"
            " VALUES (:u, :h, now() + interval '1 day')"
        ),
    }[table]
    with owner_sync_engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE kut_auth"))
        connection.execute(text(insert), {"u": user_id, "h": fresh_hash()})
    (seen_by_owner,) = await owner_rows(owner_engine, f"SELECT count(*) FROM {table}")  # noqa: S608
    assert seen_by_owner[0] == 0  # FORCE RLS: у kut_owner политик нет, строк он не видит


# --- колонковые права kut_app ---


async def user_columns(owner_engine: AsyncEngine) -> list[str]:
    rows = await owner_rows(
        owner_engine,
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_schema = 'public' AND table_name = 'user' ORDER BY ordinal_position",
    )
    return [row[0] for row in rows]


async def test_every_user_column_is_classified_for_the_app_login(owner_engine: AsyncEngine) -> None:
    """Новая колонка в user без решения о правах не пройдёт: добавьте её в один из списков."""
    assert set(await user_columns(owner_engine)) == USER_READABLE | USER_SECRET


async def test_the_app_login_reads_all_user_columns_except_the_secrets(
    owner_engine: AsyncEngine,
) -> None:
    for column in await user_columns(owner_engine):
        (row,) = await owner_rows(
            owner_engine,
            "SELECT has_column_privilege('kut_app', 'public.\"user\"', :c, 'SELECT')",
            c=column,
        )
        assert row[0] is (column in USER_READABLE), column


async def test_the_app_login_updates_only_the_profile_columns_and_never_inserts(
    owner_engine: AsyncEngine,
) -> None:
    for column in await user_columns(owner_engine):
        (row,) = await owner_rows(
            owner_engine,
            "SELECT has_column_privilege('kut_app', 'public.\"user\"', :c, 'UPDATE'),"
            " has_column_privilege('kut_app', 'public.\"user\"', :c, 'INSERT'),"
            " has_column_privilege('kut_app', 'public.\"user\"', :c, 'REFERENCES')",
            c=column,
        )
        assert tuple(row) == (column in USER_WRITABLE, False, False), column
    (table_level,) = await owner_rows(
        owner_engine,
        "SELECT has_table_privilege('kut_app', 'public.\"user\"', 'INSERT'),"
        " has_table_privilege('kut_app', 'public.\"user\"', 'DELETE')",
    )
    assert tuple(table_level) == (False, False)


@pytest.mark.parametrize(
    "statement",
    [
        'SELECT password_hash FROM "user"',
        'SELECT totp_secret_enc FROM "user"',
        'SELECT totp_key_id FROM "user"',
        'SELECT * FROM "user"',
        'SELECT password_hash IS NULL FROM "user" WHERE id = gen_random_uuid()',
        'UPDATE "user" SET role_id = role_id WHERE false',
        'UPDATE "user" SET is_active = true WHERE false',
        'UPDATE "user" SET role_ver = 99 WHERE false',
        "UPDATE \"user\" SET password_hash = 'x' WHERE false",
        'UPDATE "user" SET totp_confirmed_at = now() WHERE false',
        'UPDATE "user" SET counterparty_id = NULL WHERE false',
        'UPDATE "user" SET deleted_at = now() WHERE false',
        'INSERT INTO "user" (full_name, email, role_id)'
        " SELECT 'x', 'x@y.z', id FROM role LIMIT 1",
        "SELECT * FROM refresh_token",
        "SELECT * FROM recovery_code",
    ],
)
async def test_the_app_login_is_refused_secrets_role_changes_and_user_creation(
    statement: str,
) -> None:
    await expect_error(None, statement, INSUFFICIENT_PRIVILEGE)


async def test_the_app_login_can_still_update_its_profile_columns_and_load_users_via_the_orm(
    auth_world: AuthWorld,
) -> None:
    owner = auth_world.users["owner"]
    await call(
        context(owner, all_users=True),
        'UPDATE "user" SET full_name = full_name WHERE id = :id',
        id=owner,
    )
    with use_context(context(owner, all_users=True)):
        async with get_sessionmaker()() as session:
            loaded = (await session.scalars(select(User).where(User.id == owner))).one()

    assert loaded.full_name


# --- права на функции ---


async def function_rows(owner_engine: AsyncEngine) -> dict[str, Any]:
    rows = await owner_rows(
        owner_engine,
        "SELECT p.proname, p.prosecdef, pg_get_userbyid(p.proowner) AS owner, p.proconfig,"
        " has_function_privilege('kut_app', p.oid, 'EXECUTE') AS app,"
        " has_function_privilege('kut_portal', p.oid, 'EXECUTE') AS portal,"
        " has_function_privilege('kut_worker', p.oid, 'EXECUTE') AS worker"
        " FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
        " WHERE n.nspname = 'app' AND p.proname = ANY(:names)",
        names=list(EXECUTE_GRANTS),
    )
    return {row.proname: row for row in rows}


async def test_each_definer_function_is_owned_by_kut_auth_and_executable_only_by_its_logins(
    owner_engine: AsyncEngine,
) -> None:
    found = await function_rows(owner_engine)

    assert set(found) == set(EXECUTE_GRANTS)
    for name, allowed in EXECUTE_GRANTS.items():
        row = found[name]
        granted = {
            login
            for login, flag in (
                ("kut_app", row.app),
                ("kut_portal", row.portal),
                ("kut_worker", row.worker),
            )
            if flag
        }
        assert row.prosecdef is True and row.owner == "kut_auth", name
        assert granted == allowed, (name, granted)


async def test_every_security_definer_function_in_the_app_schema_fixes_its_search_path(
    owner_engine: AsyncEngine,
) -> None:
    rows = await owner_rows(
        owner_engine,
        "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
        " WHERE n.nspname = 'app' AND p.prosecdef"
        " AND NOT COALESCE(p.proconfig::text[] @> ARRAY['search_path=pg_catalog'], false)",
    )

    assert [row[0] for row in rows] == []


# --- чтение учётных данных ---


async def test_login_lookup_finds_by_email_or_phone_and_ignores_deleted_users(
    auth_world: AuthWorld, new_user: Callable[..., Any], worker_sync_engine: Engine
) -> None:
    foreman = auth_world.users["foreman"]

    by_email = await call(None, "SELECT * FROM app.auth_login_lookup(:l)", l="foreman@kut.test")
    by_phone = await call(None, "SELECT * FROM app.auth_login_lookup(:l)", l="+996555100001")
    unknown = await call(None, "SELECT * FROM app.auth_login_lookup(:l)", l="nobody@kut.test")
    gone_id, gone_email = new_user("foreman")
    with worker_sync_engine.begin() as connection:
        connection.execute(
            text('UPDATE "user" SET deleted_at = now() WHERE id = :i'), {"i": gone_id}
        )
    deleted = await call(None, "SELECT * FROM app.auth_login_lookup(:l)", l=gone_email)

    assert by_email[0].o_user_id == by_phone[0].o_user_id == foreman
    assert by_email[0].o_password_hash.startswith("$argon2id$")
    assert (by_email[0].o_requires_2fa, by_email[0].o_totp_confirmed) == (False, False)
    assert (by_email[0].o_role_scope, by_email[0].o_role_code) == ("project", "foreman")
    assert unknown == [] and deleted == []
    finance = await call(None, "SELECT * FROM app.auth_login_lookup(:l)", l="finance@kut.test")
    assert (finance[0].o_requires_2fa, finance[0].o_totp_confirmed) == (True, True)


async def test_auth_context_expands_scope_into_projects_and_permissions(
    auth_world: AuthWorld,
) -> None:
    async def load(who: str) -> Any:
        (row,) = await call(None, "SELECT * FROM app.auth_context(:u)", u=auth_world.users[who])
        return row

    owner, foreman, sub_a = await load("owner"), await load("foreman"), await load("sub_a")

    assert set(auth_world.projects.values()) <= set(owner.o_project_ids)
    assert owner.o_role_scope == "group" and "settings:admin" in owner.o_permissions
    assert foreman.o_project_ids == [auth_world.projects["east"]]
    assert (
        "objects:write" in foreman.o_permissions and "settings:admin" not in foreman.o_permissions
    )
    assert sub_a.o_role_scope == "counterparty" and sub_a.o_project_ids == [
        auth_world.projects["east"]
    ]
    assert sub_a.o_counterparty_id == auth_world.counterparties["Субподрядчик А (dev)"]
    assert all(
        not item.endswith(":write") for item in sub_a.o_permissions
    )  # внешние: только чтение
    assert await call(None, "SELECT * FROM app.auth_context(:u)", u=uuid4()) == []


# --- kut_worker: секреты только на запись ---


async def test_the_worker_login_reads_no_secret_columns_but_can_still_write_them(
    owner_engine: AsyncEngine,
) -> None:
    """Сид и фоновые задачи пишут хэш и секрет TOTP, но читать их воркеру не нужно."""
    for column in await user_columns(owner_engine):
        (row,) = await owner_rows(
            owner_engine,
            "SELECT has_column_privilege('kut_worker', 'public.\"user\"', :c, 'SELECT'),"
            " has_column_privilege('kut_worker', 'public.\"user\"', :c, 'UPDATE')",
            c=column,
        )
        assert row[0] is (column in USER_READABLE), column
        assert row[1] is True, column  # запись не отзывалась
    (table_level,) = await owner_rows(
        owner_engine, "SELECT has_table_privilege('kut_worker', 'public.\"user\"', 'SELECT')"
    )
    assert table_level[0] is False  # табличного SELECT нет: новая колонка по умолчанию закрыта


@pytest.mark.parametrize(
    "statement",
    [
        'SELECT password_hash FROM "user"',
        'SELECT totp_secret_enc FROM "user"',
        'SELECT totp_key_id FROM "user"',
        'SELECT * FROM "user"',
        'SELECT count(*) FROM "user" WHERE password_hash IS NOT NULL',
    ],
)
async def test_the_worker_is_refused_the_secrets_in_sql(
    worker_engine: AsyncEngine, statement: str
) -> None:
    async with worker_engine.connect() as connection:
        with pytest.raises(DBAPIError) as info:
            await connection.execute(text(statement))
    assert getattr(info.value.orig, "sqlstate", None) == INSUFFICIENT_PRIVILEGE


async def test_the_worker_still_reads_the_non_secret_user_columns(
    worker_engine: AsyncEngine,
) -> None:
    rows = await owner_rows(
        worker_engine, 'SELECT id, email, role_id, is_active FROM "user" LIMIT 1'
    )

    assert len(rows) == 1
