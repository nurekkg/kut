"""Функции администратора в БД: право берётся из контекста запроса, а не из параметров."""

from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.auth_support import AuthWorld
from tests.db_support import (
    INSUFFICIENT_PRIVILEGE,
    NO_DATA_FOUND,
    auth_rows,
    call,
    context,
    events,
    expect_error,
    fresh_hash,
    issue_session,
)

NewUser = Callable[..., tuple[UUID, str]]
CHECK_VIOLATION = "23514"
UNIQUE_VIOLATION = "23505"
ARGON_SAMPLE = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdHNhbHQ$aGFzaGhhc2g"


def admin(world: AuthWorld) -> Any:
    return context(world.users["owner"], all_users=True)


def create_sql() -> str:
    return "SELECT app.admin_create_user(:email, :phone, :name, :role, :cp)"


def person(world: AuthWorld, role: str = "foreman", **extra: Any) -> dict[str, Any]:
    return {
        "email": f"db-{uuid4().hex[:8]}@cfg.test",
        "phone": None,
        "name": "  Проверка  ",
        "role": world.roles[role],
        "cp": None,
        **extra,
    }


@pytest.fixture(autouse=True)
def _cleanup(settings_cleanup: None) -> None:
    return None


# --- кто может вызывать ---


@pytest.mark.parametrize("who", ["finance", "techdir", "foreman", "accountant", "lawyer"])
async def test_every_admin_function_refuses_a_caller_without_settings_admin(
    auth_world: AuthWorld, who: str
) -> None:
    caller = context(auth_world.users[who])
    target = auth_world.users["estimator"]
    calls = {
        "admin_create_user": (create_sql(), person(auth_world)),
        "admin_issue_invite": (
            "SELECT app.admin_issue_invite(:u, :h, interval '1 day')",
            {"u": target, "h": fresh_hash()},
        ),
        "admin_reset_totp": ("SELECT * FROM app.admin_reset_totp(:u)", {"u": target}),
        "admin_auth_events": (
            "SELECT * FROM app.admin_auth_events(10, NULL, NULL, NULL, NULL, NULL, NULL)",
            {},
        ),
    }

    for sql, params in calls.values():
        await expect_error(caller, sql, INSUFFICIENT_PRIVILEGE, **params)
        await expect_error(
            None, sql, INSUFFICIENT_PRIVILEGE, **params
        )  # без пользователя в контексте


# --- создание пользователя ---


async def test_creating_a_user_stores_a_clean_row_without_a_password_and_journals_it(
    auth_world: AuthWorld, owner_engine: AsyncEngine, worker_engine: AsyncEngine
) -> None:
    params = person(auth_world, email=f"DB-{uuid4().hex[:8]}@CFG.test", phone=" +996700000777 ")

    (created,) = await call(admin(auth_world), create_sql(), **params)
    (row,) = await auth_rows(
        owner_engine,
        "SELECT email, phone, full_name, password_hash, password_changed_at, is_active, role_ver,"
        ' created_by FROM "user" WHERE id = :u',
        u=created[0],
    )

    assert row.email == params["email"].lower() and row.phone == "+996700000777"
    assert row.full_name == "Проверка"  # пробелы по краям убраны
    assert row.password_hash is None and row.password_changed_at is None
    assert (row.is_active, row.role_ver, row.created_by) == (True, 1, auth_world.users["owner"])
    (event,) = await events(worker_engine, "user_created", created[0])
    assert event.actor_id == auth_world.users["owner"]


async def test_creating_a_user_checks_role_counterparty_contacts_and_uniqueness(
    auth_world: AuthWorld,
) -> None:
    sub = auth_world.counterparties["Субподрядчик А (dev)"]
    first = person(auth_world)
    await call(admin(auth_world), create_sql(), **first)

    ghost_role = {**person(auth_world), "role": uuid4()}
    await expect_error(admin(auth_world), create_sql(), NO_DATA_FOUND, **ghost_role)
    await expect_error(
        admin(auth_world), create_sql(), CHECK_VIOLATION, **person(auth_world, "subcontractor")
    )
    await expect_error(
        admin(auth_world), create_sql(), CHECK_VIOLATION, **person(auth_world, "foreman", cp=sub)
    )
    await expect_error(
        admin(auth_world),
        create_sql(),
        CHECK_VIOLATION,
        **person(auth_world, "subcontractor", cp=uuid4()),
    )
    await expect_error(
        admin(auth_world),
        create_sql(),
        CHECK_VIOLATION,
        **person(auth_world, email=None, phone=None),
    )
    await expect_error(
        admin(auth_world), create_sql(), CHECK_VIOLATION, **person(auth_world, phone="12345")
    )
    await expect_error(
        admin(auth_world),
        create_sql(),
        UNIQUE_VIOLATION,
        **person(auth_world, email=first["email"]),
    )
    (good,) = await call(
        admin(auth_world), create_sql(), **person(auth_world, "subcontractor", cp=sub)
    )
    assert isinstance(good[0], UUID)


# --- ссылка активации ---


INVITE = "SELECT app.admin_issue_invite(:u, :h, CAST(:ttl AS interval))"
CONSUME = "SELECT * FROM app.invite_consume(:h, :p)"


async def new_user_id(world: AuthWorld) -> UUID:
    (created,) = await call(admin(world), create_sql(), **person(world))
    user_id = created[0]
    assert isinstance(user_id, UUID)
    return user_id


async def test_an_invite_sets_the_password_once_ends_sessions_and_is_journaled(
    auth_world: AuthWorld, owner_engine: AsyncEngine, worker_engine: AsyncEngine
) -> None:
    user_id = await new_user_id(auth_world)
    family, _token = await issue_session(user_id)
    digest = fresh_hash()
    await call(admin(auth_world), INVITE, u=user_id, h=digest, ttl="1 day")

    (consumed,) = await call(None, CONSUME, h=digest, p=ARGON_SAMPLE)
    again = await call(None, CONSUME, h=digest, p="$argon2id$other")
    (state,) = await auth_rows(
        owner_engine,
        'SELECT password_hash, password_changed_at FROM "user" WHERE id = :u',
        u=user_id,
    )

    assert consumed.o_user_id == user_id and consumed.o_families == [family]
    assert again == []  # одноразовая
    assert state.password_hash == ARGON_SAMPLE and state.password_changed_at is not None
    (used,) = await events(worker_engine, "invite_used", user_id)
    assert used.actor_id == user_id and used.meta["revoked_sessions"] == 1
    assert len(await events(worker_engine, "invite_issued", user_id)) == 1


async def test_an_invite_is_refused_when_unknown_expired_replaced_or_the_user_is_off(
    auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    user_id = await new_user_id(auth_world)
    old, expired, fresh = fresh_hash(), fresh_hash(), fresh_hash()
    await call(admin(auth_world), INVITE, u=user_id, h=old, ttl="1 day")
    await call(admin(auth_world), INVITE, u=user_id, h=fresh, ttl="1 day")  # отменяет прежнюю
    other = await new_user_id(auth_world)
    await call(admin(auth_world), INVITE, u=other, h=expired, ttl="0 seconds")
    third = await new_user_id(auth_world)
    off = fresh_hash()
    await call(admin(auth_world), INVITE, u=third, h=off, ttl="1 day")
    with worker_sync_engine.begin() as connection:
        connection.execute(text('UPDATE "user" SET is_active = false WHERE id = :u'), {"u": third})

    results = {
        "unknown": await call(None, CONSUME, h=fresh_hash(), p=ARGON_SAMPLE),
        "replaced": await call(None, CONSUME, h=old, p=ARGON_SAMPLE),
        "expired": await call(None, CONSUME, h=expired, p=ARGON_SAMPLE),
        "inactive": await call(None, CONSUME, h=off, p=ARGON_SAMPLE),
    }
    still_valid = await call(None, CONSUME, h=fresh, p=ARGON_SAMPLE)

    assert all(rows == [] for rows in results.values()), results
    assert len(still_valid) == 1


async def test_an_invite_cannot_be_issued_for_an_unknown_or_inactive_user(
    auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    user_id = await new_user_id(auth_world)
    with worker_sync_engine.begin() as connection:
        connection.execute(
            text('UPDATE "user" SET is_active = false WHERE id = :u'), {"u": user_id}
        )

    await expect_error(
        admin(auth_world), INVITE, NO_DATA_FOUND, u=uuid4(), h=fresh_hash(), ttl="1 day"
    )
    await expect_error(
        admin(auth_world), INVITE, NO_DATA_FOUND, u=user_id, h=fresh_hash(), ttl="1 day"
    )


# --- сброс 2FA ---


async def test_resetting_two_factor_clears_the_secret_codes_and_sessions(
    auth_world: AuthWorld, new_user: NewUser, owner_engine: AsyncEngine, worker_engine: AsyncEngine
) -> None:
    user, _ = new_user("accountant")
    me = context(user)
    await call(me, "SELECT app.totp_set_pending(:b, 'k1')", b=b"\x01" * 30)
    await call(me, "SELECT app.totp_confirm(CAST(:c AS bytea[]))", c=[fresh_hash(), fresh_hash()])
    family, token = await issue_session(user)

    (reset,) = await call(admin(auth_world), "SELECT * FROM app.admin_reset_totp(:u)", u=user)
    (state,) = await auth_rows(
        owner_engine,
        'SELECT totp_secret_enc, totp_key_id, totp_confirmed_at, role_ver FROM "user"'
        " WHERE id = :u",
        u=user,
    )
    (codes,) = await auth_rows(
        owner_engine, "SELECT count(*) FROM recovery_code WHERE user_id = :u", u=user
    )
    rotation = await call(
        None, "SELECT * FROM app.refresh_rotate(:o, :n, interval '0')", o=token, n=fresh_hash()
    )

    assert reset.o_role_ver == 2 and reset.o_families == [family]
    assert tuple(state) == (None, None, None, 2) and codes[0] == 0
    assert rotation[0].o_status == "invalid"
    (event,) = await events(worker_engine, "mfa_reset", user)
    assert event.actor_id == auth_world.users["owner"]


async def test_two_factor_reset_refuses_oneself_and_unknown_users(auth_world: AuthWorld) -> None:
    sql = "SELECT * FROM app.admin_reset_totp(:u)"

    await expect_error(admin(auth_world), sql, INSUFFICIENT_PRIVILEGE, u=auth_world.users["owner"])
    await expect_error(admin(auth_world), sql, NO_DATA_FOUND, u=uuid4())


# --- журнал событий ---


EVENTS = "SELECT * FROM app.admin_auth_events(:lim, :before, :action, :actor, :ip, :a, :b)"


def args(**override: Any) -> dict[str, Any]:
    return {
        "lim": 50,
        "before": None,
        "action": None,
        "actor": None,
        "ip": None,
        "a": None,
        "b": None,
        **override,
    }


async def test_the_events_function_returns_only_events_without_the_connection_user(
    auth_world: AuthWorld, new_user: NewUser
) -> None:
    user, _ = new_user("foreman")
    await call(
        None,
        "SELECT app.audit_event('probe_event', 'user', :u, :u,"
        " CAST('192.0.2.7' AS inet), '{\"k\": 1}')",
        u=user,
    )

    rows = await call(admin(auth_world), EVENTS, **args(action="probe_event", ip="192.0.2.7"))
    data_rows = await call(
        admin(auth_world), EVENTS, **args(action="insert")
    )  # так зовутся изменения данных

    assert len(rows) >= 1 and rows[0].o_ip == "192.0.2.7"
    assert (
        rows[0].o_meta == {"k": 1} and rows[0].o_actor_id == user and rows[0].o_subject_id == user
    )
    assert data_rows == []  # строки kind = 'data' функция не отдаёт


async def test_the_events_function_caps_the_page_size_and_pages_by_id(
    auth_world: AuthWorld,
) -> None:
    for number in range(3):
        await call(
            None,
            "SELECT app.audit_event(:a, NULL, NULL, NULL, NULL, NULL)",
            a=f"cap-probe-{number}",
        )

    huge = await call(admin(auth_world), EVENTS, **args(lim=100000))
    one = await call(admin(auth_world), EVENTS, **args(lim=1))
    older = await call(admin(auth_world), EVENTS, **args(lim=1, before=one[0].o_id))

    assert len(huge) <= 200 and [r.o_id for r in huge] == sorted(
        (r.o_id for r in huge), reverse=True
    )
    assert len(one) == 1 and len(older) == 1 and older[0].o_id < one[0].o_id
