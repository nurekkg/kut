from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from tests.auth_support import AuthWorld, client_from, login, token_for
from tests.db_support import INSUFFICIENT_PRIVILEGE, context, expect_error
from tests.db_support import call as sql_call
from tests.settings_support import call, create_user, new_active_user, unique

pytestmark = pytest.mark.usefixtures("clean_redis", "settings_cleanup")

NO_FILTERS = "SELECT * FROM app.admin_auth_events(10, NULL, NULL, NULL, NULL, NULL, NULL)"


async def test_the_login_journal_is_for_the_settings_admin_only(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    for who in ("finance", "techdir", "foreman", "customer"):
        token = await token_for(api, who)
        assert (await call(api, "GET", "/events", token)).status_code == 403, who
    # И напрямую в БД: функция проверяет право по контексту, а не по параметру.
    for who, internal in (("foreman", True), ("finance", True), ("sub_a", False)):
        await expect_error(
            context(auth_world.users[who], internal=internal), NO_FILTERS, INSUFFICIENT_PRIVILEGE
        )
    await expect_error(None, NO_FILTERS, INSUFFICIENT_PRIVILEGE)


async def test_the_journal_lists_who_when_what_and_from_which_address(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    token = await token_for(api, "owner")
    created = await create_user(api, token, auth_world, "foreman")
    login_name = unique("ghost-")
    await login(api, login_name, "wrong-password-1")

    page = (await call(api, "GET", "/events", token, params={"limit": 200})).json()
    items = page["items"]
    made = next(
        e for e in items if e["action"] == "user_created" and e["subject_id"] == created["id"]
    )
    failed = next(e for e in items if e["action"] == "login_failed")
    owner_login = next(e for e in items if e["action"] == "login_success")

    assert (
        made["actor_id"] == str(auth_world.users["owner"])
        and made["actor_name"] == "Владелец (dev)"
    )
    assert made["subject_name"] == created["full_name"] and made["actor_kind"] == "user"
    assert (
        failed["actor_id"] is None
        and failed["actor_kind"] == "system"
        and failed["ip"] == "127.0.0.1"
    )
    assert owner_login["ip"] == "127.0.0.1" and owner_login["meta"]["amr"] == ["pwd", "otp"]
    assert all("db_user" not in e["meta"] for e in items)  # служебное имя логина БД наружу не идёт
    assert [e["id"] for e in items] == sorted((e["id"] for e in items), reverse=True)


async def test_the_journal_filters_by_action_actor_address_and_time(
    api: AsyncClient, app: FastAPI, auth_world: AuthWorld
) -> None:
    token = await token_for(api, "owner")
    user_id, email = await new_active_user(api, token, auth_world, "estimator")
    await login(api, email)
    async with client_from(app, "10.77.0.5") as elsewhere:
        await login(elsewhere, unique("ghost-"), "wrong-password-1")

    async def query(**params: Any) -> list[dict[str, Any]]:
        response = await call(api, "GET", "/events", token, params={"limit": 200, **params})
        assert response.status_code == 200, response.text
        items: list[dict[str, Any]] = response.json()["items"]
        return items

    now = datetime.now(UTC)
    by_action = await query(action="invite_used")
    by_actor = await query(actor_id=user_id)
    by_ip = await query(ip="10.77.0.5")
    future = await query(date_from=(now + timedelta(days=1)).isoformat())
    past = await query(date_to=(now - timedelta(days=1)).isoformat())
    window = await query(
        action="login_success",
        date_from=(now - timedelta(minutes=5)).isoformat(),
        date_to=(now + timedelta(minutes=5)).isoformat(),
    )

    assert by_action and {e["action"] for e in by_action} == {"invite_used"}
    assert user_id in {e["actor_id"] for e in by_action}
    assert by_actor and {e["actor_id"] for e in by_actor} == {user_id}
    assert {e["action"] for e in by_actor} >= {"invite_used", "login_success"}
    assert by_ip and {e["ip"] for e in by_ip} == {"10.77.0.5"}
    assert by_ip[0]["action"] == "login_failed"
    assert future == [] and past == []
    assert window and {e["action"] for e in window} == {"login_success"}


async def test_the_journal_pages_with_a_cursor_without_gaps_or_repeats(
    api: AsyncClient, app: FastAPI
) -> None:
    token = await token_for(api, "owner")
    async with client_from(app, "10.88.0.9") as elsewhere:
        for _ in range(5):
            await login(elsewhere, unique("ghost-"), "wrong-password-1")

    seen: list[int] = []
    cursor: int | None = None
    pages = 0
    while pages < 10:
        params: dict[str, object] = {"ip": "10.88.0.9", "limit": 2}
        if cursor is not None:
            params["before"] = cursor
        page = (await call(api, "GET", "/events", token, params=params)).json()
        seen += [item["id"] for item in page["items"]]
        pages += 1
        cursor = page["next_before"]
        if cursor is None:
            break

    assert len(seen) == 5 and len(set(seen)) == 5 and seen == sorted(seen, reverse=True)
    assert pages == 3  # 2 + 2 + 1


async def test_the_journal_limits_and_rejects_nonsense_parameters(api: AsyncClient) -> None:
    token = await token_for(api, "owner")

    too_big = await call(api, "GET", "/events", token, params={"limit": 1000})
    zero = await call(api, "GET", "/events", token, params={"limit": 0})
    bad_actor = await call(api, "GET", "/events", token, params={"actor_id": "not-a-uuid"})
    bad_date = await call(api, "GET", "/events", token, params={"date_from": "yesterday"})
    no_ip_match = await call(api, "GET", "/events", token, params={"ip": "' OR 1=1 --"})

    assert (too_big.status_code, zero.status_code, bad_actor.status_code, bad_date.status_code) == (
        422,
    ) * 4
    assert no_ip_match.status_code == 200 and no_ip_match.json()["items"] == []


async def test_administrative_actions_leave_events_in_the_journal(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    token = await token_for(api, "owner")
    user_id, _email = await new_active_user(api, token, auth_world, "estimator")
    await call(
        api,
        "PUT",
        f"/users/{user_id}/role",
        token,
        json={"role_id": str(auth_world.roles["lawyer"])},
    )
    await call(api, "PUT", f"/users/{user_id}/active", token, json={"active": False})
    await call(api, "POST", f"/users/{user_id}/reset-mfa", token)

    items = (await call(api, "GET", "/events", token, params={"limit": 200})).json()["items"]
    mine = {e["action"] for e in items if e["subject_id"] == user_id}

    assert {
        "user_created",
        "invite_issued",
        "invite_used",
        "role_assigned",
        "user_deactivated",
        "mfa_reset",
    } <= mine
    assert all(UUID(e["subject_id"]) for e in items if e["subject_id"])


async def test_a_direct_sql_caller_with_the_app_login_sees_no_events_without_the_function(
    auth_world: AuthWorld,
) -> None:
    """Событий входа нет в журнале для kut_app: их читает только функция администратора."""
    seen = await sql_call(
        context(auth_world.users["owner"], all_users=True),
        "SELECT count(*) FROM audit_log WHERE kind = 'event'",
    )

    assert seen[0][0] == 0
