"""S1b: поведение SECURITY DEFINER-функций, журнал событий, политики user и idempotency_key."""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.context import RequestContext
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
    owner_rows,
)

NewUser = Callable[..., tuple[UUID, str]]
ARGON_SAMPLE = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdHNhbHQ$aGFzaGhhc2g"


def admin_context(world: AuthWorld) -> RequestContext:
    return context(world.users["owner"], all_users=True)


# --- назначение роли и активность ---


async def test_assign_role_demands_settings_admin_from_the_request_context(
    auth_world: AuthWorld, new_user: NewUser, worker_sync_engine: Engine
) -> None:
    target, _ = new_user("foreman")
    sql = "SELECT * FROM app.assign_role(:u, :r)"
    params = {"u": target, "r": auth_world.roles["estimator"]}
    demoted_admin, _ = new_user("owner")
    with worker_sync_engine.begin() as connection:
        connection.execute(
            text('UPDATE "user" SET is_active = false WHERE id = :i'), {"i": demoted_admin}
        )

    for who in ("finance", "techdir", "foreman", "accountant", "lawyer"):
        await expect_error(context(auth_world.users[who]), sql, INSUFFICIENT_PRIVILEGE, **params)
    await expect_error(
        None, sql, INSUFFICIENT_PRIVILEGE, **params
    )  # системный контекст: актора нет
    await expect_error(context(uuid4()), sql, INSUFFICIENT_PRIVILEGE, **params)  # такого нет
    await expect_error(context(demoted_admin), sql, INSUFFICIENT_PRIVILEGE, **params)  # неактивен
    await expect_error(
        context(auth_world.users["sub_a"], internal=False), sql, INSUFFICIENT_PRIVILEGE, **params
    )


async def test_assign_role_bumps_role_ver_revokes_sessions_and_journals_the_actor(
    auth_world: AuthWorld, new_user: NewUser, worker_engine: AsyncEngine
) -> None:
    target, _ = new_user("foreman")
    first, token_one = await issue_session(target)
    second, _token_two = await issue_session(target)
    admin = admin_context(auth_world)

    (changed,) = await call(
        admin, "SELECT * FROM app.assign_role(:u, :r)", u=target, r=auth_world.roles["estimator"]
    )
    (again,) = await call(
        admin, "SELECT * FROM app.assign_role(:u, :r)", u=target, r=auth_world.roles["lawyer"]
    )
    (state,) = await owner_rows(
        worker_engine, 'SELECT role_id, role_ver FROM "user" WHERE id = :u', u=target
    )
    rotation, _ = await rotate(token_one)

    assert changed.o_role_ver == 2 and set(changed.o_families) == {first, second}
    assert again.o_role_ver == 3 and again.o_families == []  # сеансов уже нет
    assert tuple(state) == (auth_world.roles["lawyer"], 3)
    assert rotation.o_status == "invalid"  # токен отозван вместе с семьёй
    journal = await events(worker_engine, "role_assigned", target)
    assert [event.actor_id for event in journal] == [auth_world.users["owner"]] * 2
    assert [event.meta["role_ver"] for event in journal] == [2, 3]
    assert journal[0].meta["old_role_id"] == str(auth_world.roles["foreman"])
    assert journal[0].meta["new_role_id"] == str(auth_world.roles["estimator"])
    assert journal[0].meta["db_user"] == "kut_app" and journal[0].actor_kind == "user"


async def test_assign_role_refuses_self_change_and_unknown_targets(
    auth_world: AuthWorld, new_user: NewUser
) -> None:
    target, _ = new_user("foreman")
    admin = admin_context(auth_world)
    sql = "SELECT * FROM app.assign_role(:u, :r)"
    owner, foreman_role = auth_world.users["owner"], auth_world.roles["foreman"]

    await expect_error(admin, sql, INSUFFICIENT_PRIVILEGE, u=owner, r=foreman_role)
    await expect_error(admin, sql, NO_DATA_FOUND, u=uuid4(), r=foreman_role)
    await expect_error(admin, sql, NO_DATA_FOUND, u=target, r=uuid4())


async def test_set_user_active_follows_the_same_rules_and_ends_the_sessions(
    auth_world: AuthWorld, new_user: NewUser, worker_engine: AsyncEngine
) -> None:
    target, _ = new_user("estimator")
    family, _token = await issue_session(target)
    sql = "SELECT * FROM app.set_user_active(:u, :a)"
    admin = admin_context(auth_world)

    await expect_error(
        context(auth_world.users["finance"]), sql, INSUFFICIENT_PRIVILEGE, u=target, a=False
    )
    await expect_error(admin, sql, INSUFFICIENT_PRIVILEGE, u=auth_world.users["owner"], a=False)
    await expect_error(admin, sql, NO_DATA_FOUND, u=uuid4(), a=False)
    (off,) = await call(admin, sql, u=target, a=False)
    (on,) = await call(admin, sql, u=target, a=True)
    (state,) = await owner_rows(
        worker_engine, 'SELECT is_active, role_ver FROM "user" WHERE id = :u', u=target
    )

    assert off.o_role_ver == 2 and off.o_families == [family]
    assert on.o_role_ver == 3 and on.o_families == []
    assert tuple(state) == (True, 3)
    assert len(await events(worker_engine, "user_deactivated", target)) == 1
    assert len(await events(worker_engine, "user_activated", target)) == 1


# --- refresh-токены ---


async def rotate(old: bytes, grace: str = "0 seconds") -> tuple[Any, bytes]:
    new = fresh_hash()
    (row,) = await call(
        None,
        "SELECT * FROM app.refresh_rotate(:o, :n, CAST(:g AS interval))",
        o=old, n=new, g=grace,
    )  # fmt: skip
    return row, new


async def test_rotation_issues_a_successor_in_the_same_family_with_the_same_lifetime(
    new_user: NewUser,
) -> None:
    user, _ = new_user("foreman")
    family, token = await issue_session(user, ttl="30 days")

    row, _successor = await rotate(token)

    assert (row.o_status, row.o_user_id, row.o_family_id) == ("ok", user, family)
    left = row.o_expires_at - datetime.now(UTC)
    assert timedelta(days=29, hours=23) < left <= timedelta(days=30)


async def test_reuse_revokes_the_family_and_leaves_a_security_event(
    new_user: NewUser, worker_engine: AsyncEngine
) -> None:
    user, _ = new_user("foreman")
    family, token = await issue_session(user)
    other_family, other_token = await issue_session(user)
    ok, successor = await rotate(token)

    reused, _ = await rotate(token)
    successor_after, _ = await rotate(successor)
    untouched, _ = await rotate(other_token)

    assert ok.o_status == "ok" and reused.o_status == "reused" and reused.o_family_id == family
    assert successor_after.o_status == "invalid"  # семья отозвана целиком
    assert untouched.o_status == "ok" and untouched.o_family_id == other_family
    (event,) = await events(worker_engine, "refresh_reuse", family)
    assert event.actor_id == user and event.table_name == "refresh_token"


async def test_a_used_token_inside_the_grace_window_does_not_revoke_the_family(
    new_user: NewUser, worker_engine: AsyncEngine
) -> None:
    user, _ = new_user("foreman")
    family, token = await issue_session(user)
    ok, successor = await rotate(token, grace="1 hour")

    replay, _ = await rotate(token, grace="1 hour")
    next_step, _ = await rotate(successor, grace="1 hour")  # законная цепочка продолжается

    assert ok.o_status == "ok" and replay.o_status == "grace" and replay.o_family_id == family
    assert next_step.o_status == "ok"
    assert await events(worker_engine, "refresh_reuse", family) == []
    assert len(await events(worker_engine, "refresh_grace", family)) == 1


async def test_a_used_token_outside_the_grace_window_revokes_the_family(
    new_user: NewUser, worker_engine: AsyncEngine
) -> None:
    user, _ = new_user("foreman")
    family, token = await issue_session(user)
    _, successor = await rotate(token, grace="0 seconds")

    late, _ = await rotate(token, grace="0 seconds")
    successor_after, _ = await rotate(successor, grace="1 hour")

    assert late.o_status == "reused" and late.o_family_id == family
    assert successor_after.o_status == "invalid"
    assert len(await events(worker_engine, "refresh_reuse", family)) == 1


async def test_the_grace_window_does_not_save_an_inactive_user(
    new_user: NewUser, worker_sync_engine: Engine
) -> None:
    user, _ = new_user("foreman")
    _family, token = await issue_session(user)
    await rotate(token, grace="1 hour")
    with worker_sync_engine.begin() as connection:
        connection.execute(text('UPDATE "user" SET is_active = false WHERE id = :i'), {"i": user})

    replay, _ = await rotate(token, grace="1 hour")

    assert replay.o_status == "inactive"


async def test_unknown_and_expired_tokens_are_invalid_and_not_a_reuse(
    new_user: NewUser, worker_engine: AsyncEngine
) -> None:
    user, _ = new_user("foreman")
    family, expired = await issue_session(user, ttl="-1 minute")

    unknown, _ = await rotate(fresh_hash())
    stale, _ = await rotate(expired)

    assert (unknown.o_status, unknown.o_user_id, unknown.o_family_id) == ("invalid", None, None)
    assert stale.o_status == "invalid" and stale.o_family_id == family
    assert await events(worker_engine, "refresh_reuse", family) == []


async def test_an_inactive_user_cannot_rotate_and_loses_the_family(
    new_user: NewUser, worker_sync_engine: Engine
) -> None:
    user, _ = new_user("foreman")
    _family, token = await issue_session(user)
    with worker_sync_engine.begin() as connection:
        connection.execute(text('UPDATE "user" SET is_active = false WHERE id = :i'), {"i": user})

    row, _ = await rotate(token)
    again, _ = await rotate(token)

    assert row.o_status == "inactive" and again.o_status == "invalid"


async def test_logout_revokes_the_family_and_journals_once(
    new_user: NewUser, worker_engine: AsyncEngine
) -> None:
    user, _ = new_user("foreman")
    family, token = await issue_session(user)

    (first,) = await call(None, "SELECT * FROM app.refresh_logout(:h)", h=token)
    (second,) = await call(None, "SELECT * FROM app.refresh_logout(:h)", h=token)
    missing = await call(None, "SELECT * FROM app.refresh_logout(:h)", h=fresh_hash())
    row, _ = await rotate(token)

    assert (first.o_user_id, first.o_family_id) == (user, family)
    assert (second.o_user_id, second.o_family_id) == (user, family)
    assert missing == [] and row.o_status == "invalid"
    assert len(await events(worker_engine, "logout", family)) == 1


# --- пароль и TOTP ---


async def test_change_password_works_only_from_the_owners_own_context(
    auth_world: AuthWorld,
    new_user: NewUser,
    worker_engine: AsyncEngine,
    owner_engine: AsyncEngine,
) -> None:
    user, _ = new_user("foreman")
    keep, _keep_token = await issue_session(user)
    gone_a, _ = await issue_session(user)
    gone_b, _ = await issue_session(user)
    sql = "SELECT app.change_password(:u, :hash, :keep)"

    for stranger in (None, context(auth_world.users["owner"], all_users=True), context(uuid4())):
        await expect_error(
            stranger, sql, INSUFFICIENT_PRIVILEGE, u=user, hash=ARGON_SAMPLE, keep=keep
        )
    (row,) = await call(context(user), sql, u=user, hash=ARGON_SAMPLE, keep=keep)
    (state,) = await auth_rows(
        owner_engine, 'SELECT password_hash, password_changed_at FROM "user" WHERE id = :u', u=user
    )

    assert set(row[0]) == {gone_a, gone_b}
    assert state.password_hash == ARGON_SAMPLE and state.password_changed_at is not None
    (event,) = await events(worker_engine, "password_changed", user)
    assert event.actor_id == user and event.meta["revoked_sessions"] == 2


async def test_totp_secret_lifecycle_and_single_use_recovery_codes(
    new_user: NewUser, worker_engine: AsyncEngine
) -> None:
    user, _ = new_user("accountant")
    me = context(user)
    blob = b"\x01" * 40
    codes = [fresh_hash() for _ in range(3)]
    use = "SELECT app.recovery_code_use(:c)"
    confirm = "SELECT app.totp_confirm(CAST(:c AS bytea[]))"

    pending = await call(me, "SELECT app.totp_set_pending(:b, 'k1')", b=blob)
    (stored,) = await call(me, "SELECT * FROM app.totp_get()")
    early = await call(me, use, c=codes[0])
    confirmed = await call(me, confirm, c=codes)
    twice = await call(me, confirm, c=codes)
    after_confirm = await call(me, "SELECT app.totp_set_pending(:b, 'k2')", b=blob)
    first = await call(me, use, c=codes[0])
    spent = await call(me, use, c=codes[0])
    unknown = await call(me, use, c=fresh_hash())
    elsewhere = await call(context(uuid4()), use, c=codes[1])  # чужой контекст: кодов нет

    assert pending[0][0] is True and bytes(stored.o_secret_enc) == blob and stored.o_key_id == "k1"
    assert stored.o_confirmed is False and early[0][0] is False
    assert (confirmed[0][0], twice[0][0], after_confirm[0][0]) == (True, False, False)
    assert (first[0][0], spent[0][0], unknown[0][0], elsewhere[0][0]) == (True, False, False, False)
    (used,) = await events(worker_engine, "recovery_code_used", user)
    assert used.meta["left"] == 2
    assert len(await events(worker_engine, "mfa_enabled", user)) == 1


TOTP_CALLS = {
    "totp_get": ("SELECT * FROM app.totp_get()", {}),
    "totp_set_pending": ("SELECT app.totp_set_pending(:b, 'k')", {"b": b"\x02" * 8}),
    "totp_confirm": ("SELECT app.totp_confirm(CAST(:c AS bytea[]))", {"c": [b"\x03" * 32]}),
    "recovery_code_use": ("SELECT app.recovery_code_use(:c)", {"c": b"\x04" * 32}),
    "auth_password_hash": ("SELECT app.auth_password_hash()", {}),
}


@pytest.mark.parametrize("name", list(TOTP_CALLS))
async def test_credential_functions_refuse_a_call_without_a_user_in_the_context(
    name: str,
) -> None:
    sql, params = TOTP_CALLS[name]

    await expect_error(None, sql, INSUFFICIENT_PRIVILEGE, **params)
    await expect_error(context(None), sql, INSUFFICIENT_PRIVILEGE, **params)


async def test_credential_functions_only_ever_touch_the_user_of_the_context(
    new_user: NewUser, owner_engine: AsyncEngine
) -> None:
    alice, _ = new_user("accountant")
    bob, _ = new_user("accountant")
    secret = b"\x07" * 30
    await call(context(alice), "SELECT app.totp_set_pending(:b, 'ka')", b=secret)

    bobs_view = await call(context(bob), "SELECT * FROM app.totp_get()")
    bob_writes = await call(context(bob), "SELECT app.totp_set_pending(:b, 'kb')", b=b"\x08" * 30)
    alices_view = await call(context(alice), "SELECT * FROM app.totp_get()")
    stored = await auth_rows(
        owner_engine,
        'SELECT id, totp_key_id FROM "user" WHERE id = ANY(:ids)',
        ids=[alice, bob],
    )

    assert bobs_view[0].o_secret_enc is None and bobs_view[0].o_key_id is None
    assert bob_writes[0][0] is True and bytes(alices_view[0].o_secret_enc) == secret
    assert {row.id: row.totp_key_id for row in stored} == {alice: "ka", bob: "kb"}


async def test_the_old_credential_signatures_with_a_user_parameter_are_gone(
    owner_engine: AsyncEngine,
) -> None:
    rows = await owner_rows(
        owner_engine,
        "SELECT p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')' AS sig"
        " FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'app'"
        " AND p.proname = ANY(:names)",
        names=[*TOTP_CALLS, "refresh_rotate"],
    )

    signatures = {row.sig for row in rows}
    assert not any(
        "uuid" in signature.split("(")[1] for signature in signatures if "(" in signature
    ), signatures
    assert "refresh_rotate(p_old bytea, p_new bytea, p_grace interval)" in signatures


# --- журнал ---


async def test_audit_event_classifies_the_actor_and_records_ip_and_request(
    auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    request_id, row_id = uuid4(), uuid4()
    marker = f"probe-{uuid4().hex[:8]}"
    sql = (
        "SELECT app.audit_event(:a, 'user', :row, :actor, CAST(:ip AS inet), CAST(:meta AS jsonb))"
    )
    cases: dict[str, tuple[UUID | None, str | None]] = {
        "system": (None, None),
        "user": (auth_world.users["foreman"], None),
        "external": (auth_world.users["sub_a"], "203.0.113.7"),
    }
    for kind, (actor, ip) in cases.items():
        await call(
            context(actor, request_id=request_id), sql,
            a=f"{marker}-{kind}", row=row_id, actor=actor, ip=ip, meta=json.dumps({"k": kind}),
        )  # fmt: skip

    for kind, (actor, ip) in cases.items():
        (event,) = await events(worker_engine, f"{marker}-{kind}")
        assert event.actor_kind == kind and event.actor_id == actor
        assert event.request_id == request_id and event.row_id == row_id
        assert event.meta == {"k": kind, "db_user": "kut_app"}
        assert (str(event.ip) if event.ip else None) == ip


async def test_the_journal_never_contains_credentials_of_a_user_row(
    new_user: NewUser, worker_engine: AsyncEngine
) -> None:
    user, _ = new_user("accountant")
    marker_hash = "$argon2id$v=19$m=65536,t=3,p=4$bWFya2Vy$bWFya2VyaGFzaA"
    await call(context(user), "SELECT app.totp_set_pending(:b, 'k1')", b=b"\xaa" * 32)
    await call(context(user), "SELECT app.change_password(:u, :h, NULL)", u=user, h=marker_hash)

    rows = await owner_rows(
        worker_engine,
        "SELECT old, new FROM audit_log"
        " WHERE kind = 'data' AND table_name = 'user' AND row_id = :u",
        u=user,
    )
    dumped = json.dumps([[row.old, row.new] for row in rows], default=str)

    assert len(rows) >= 3  # создание, секрет TOTP, смена пароля
    for forbidden in ("password_hash", "totp_secret_enc", "argon2id", "marker"):
        assert forbidden not in dumped, forbidden
    assert "password_changed_at" in dumped and "totp_key_id" in dumped  # несекретные поля видны


# --- видимость пользователей ---


async def visible_users(who: RequestContext, *, portal: bool = False) -> set[UUID]:
    rows = await call(who, 'SELECT id FROM "user"', portal=portal)
    return {row[0] for row in rows}


async def test_user_policy_internal_roles_see_themselves_and_users_sharing_a_project(
    auth_world: AuthWorld,
) -> None:
    east, west = auth_world.projects["east"], auth_world.projects["west"]
    users = auth_world.users
    east_team = {
        users[name]
        for name in ("foreman", "master", "estimator", "storekeeper", "lawyer", "customer", "sub_a")
    }

    foreman = await visible_users(context(users["foreman"], projects={east}))
    both = await visible_users(context(users["lawyer"], projects={east, west}))
    loner = await visible_users(context(users["foreman"], projects=set()))

    assert foreman == east_team
    assert {users["procurement"], users["sub_b"]} <= both and users["owner"] not in both
    assert loner == {users["foreman"]}  # без объектов в контексте только сам


async def test_user_policy_everyone_only_with_the_all_users_flag(auth_world: AuthWorld) -> None:
    users = auth_world.users
    sub_a = auth_world.counterparties["Субподрядчик А (dev)"]

    admin_like = await visible_users(context(users["owner"], all_users=True))
    flag_on_external = await visible_users(
        context(users["sub_a"], internal=False, all_users=True, counterparty=sub_a)
    )
    flag_off = await visible_users(context(users["owner"], all_users=False))

    assert set(users.values()) <= admin_like
    assert flag_on_external == {users["sub_a"]}  # внешнему флаг не помогает
    assert flag_off == {users["owner"]}  # без флага и без объектов в контексте: только сам


async def test_user_policy_portal_pool_sees_only_the_caller(auth_world: AuthWorld) -> None:
    users = auth_world.users

    seen = await visible_users(
        context(users["customer"], internal=False, projects={auth_world.projects["east"]}),
        portal=True,
    )

    assert seen == {users["customer"]}


# --- ключи идемпотентности ---


async def test_idempotency_keys_are_private_and_frozen_once_completed(
    auth_world: AuthWorld,
) -> None:
    foreman, master = auth_world.users["foreman"], auth_world.users["master"]
    alice, bob = context(foreman), context(master)
    key, digest = uuid4(), b"h" * 32
    insert = (
        "INSERT INTO idempotency_key (user_id, key, request_hash, response_code, expires_at)"
        " VALUES (:u, :k, :h, 0, now() + interval '1 hour')"
    )
    complete = (
        "UPDATE idempotency_key SET response_code = 201, response_body = '{\"a\": 1}'"
        " WHERE key = :k"
    )
    await call(alice, insert, u=foreman, k=key, h=digest)

    mine = await call(alice, "SELECT key FROM idempotency_key WHERE key = :k", k=key)
    theirs = await call(bob, "SELECT key FROM idempotency_key WHERE key = :k", k=key)
    await expect_error(bob, insert, INSUFFICIENT_PRIVILEGE, u=foreman, k=uuid4(), h=digest)
    await call(bob, "UPDATE idempotency_key SET response_code = 500 WHERE key = :k", k=key)
    await call(alice, complete, k=key)
    await call(alice, "UPDATE idempotency_key SET response_code = 500 WHERE key = :k", k=key)
    (final,) = await call(
        alice, "SELECT response_code, response_body FROM idempotency_key WHERE key = :k", k=key
    )

    assert len(mine) == 1 and theirs == []
    assert tuple(final) == (201, {"a": 1})  # чужое обновление и повторное завершение не прошли
    for statement in (
        "UPDATE idempotency_key SET request_hash = 'x' WHERE false",
        "UPDATE idempotency_key SET user_id = user_id WHERE false",
        "DELETE FROM idempotency_key",
    ):
        await expect_error(alice, statement, INSUFFICIENT_PRIVILEGE)


async def test_idempotency_keys_are_unreachable_for_the_portal_and_worker_logins(
    owner_engine: AsyncEngine,
) -> None:
    for login in ("kut_portal", "kut_worker"):
        (row,) = await owner_rows(
            owner_engine,
            "SELECT has_table_privilege(:l, 'public.idempotency_key', 'SELECT'),"
            " has_table_privilege(:l, 'public.idempotency_key', 'INSERT')",
            l=login,
        )
        assert tuple(row) == (False, False), login


# --- очистка ---


async def test_purge_removes_only_expired_rows_and_only_the_worker_may_call_it(
    new_user: NewUser, owner_sync_engine: Engine, worker_engine: AsyncEngine
) -> None:
    user, _ = new_user("foreman")
    live, expired, revoked = fresh_hash(), fresh_hash(), fresh_hash()
    live_key, stale_key = uuid4(), uuid4()
    token_sql = text(
        "INSERT INTO refresh_token (user_id, family_id, token_hash, amr, expires_at, revoked_at)"
        " VALUES (:u, gen_random_uuid(), :h, '{pwd}', now() + CAST(:exp AS interval),"
        " CASE WHEN CAST(:rev AS text) IS NULL THEN NULL ELSE now() + CAST(:rev AS interval) END)"
    )
    key_sql = (
        "INSERT INTO idempotency_key (user_id, key, request_hash, response_code, expires_at)"
        " VALUES (:u, :k, :h, 201, now() + CAST(:exp AS interval))"
    )
    with owner_sync_engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE kut_auth"))
        for token, expires, revoked_at in (
            (live, "10 days", None),
            (expired, "-3 days", None),
            (revoked, "10 days", "-3 days"),
        ):
            connection.execute(
                token_sql, {"u": user, "h": token, "exp": expires, "rev": revoked_at}
            )

    for key, expires in ((live_key, "1 hour"), (stale_key, "-1 hour")):  # kut_auth не пишет ключи
        await call(context(user), key_sql, u=user, k=key, h=live, exp=expires)

    await expect_error(None, "SELECT app.purge_expired_auth_data()", INSUFFICIENT_PRIVILEGE)
    async with worker_engine.begin() as connection:  # begin(): результат нужно зафиксировать
        counts = (await connection.execute(text("SELECT app.purge_expired_auth_data()"))).one()

    assert counts[0]["refresh_tokens"] >= 2 and counts[0]["idempotency_keys"] >= 1
    with owner_sync_engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE kut_auth"))
        left_tokens = {
            bytes(item)
            for item in connection.scalars(
                text("SELECT token_hash FROM refresh_token WHERE user_id = :u"), {"u": user}
            )
        }
        left_keys = set(
            connection.scalars(
                text("SELECT key FROM idempotency_key WHERE user_id = :u"), {"u": user}
            )
        )
    assert left_tokens == {live} and left_keys == {live_key}
