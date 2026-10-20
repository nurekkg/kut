import asyncio
from typing import Any
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import Engine, text

from app.core.config import get_settings
from app.modules.auth import devseed
from tests.auth_support import (
    AUTH,
    PASSWORD,
    AuthWorld,
    bearer,
    login,
    token_for,
    totp_code,
)
from tests.settings_support import (
    CFG_DOMAIN,
    activate,
    call,
    create_user,
    new_active_user,
    post_new,
    unique,
)

pytestmark = pytest.mark.usefixtures("clean_redis", "settings_cleanup")


async def me(api: AsyncClient, token: str) -> int:
    return (await api.get(f"{AUTH}/me", headers=bearer(token))).status_code


async def projects_of(api: AsyncClient, token: str) -> set[str]:
    response = await api.get("/api/v1/projects", headers=bearer(token))
    assert response.status_code == 200, response.text
    return {item["id"] for item in response.json()}


# --- создание и вход ---


async def test_a_created_user_has_no_password_and_cannot_log_in_until_activation(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    token = await token_for(api, "owner")

    created = await create_user(api, token, auth_world, "foreman", phone="+996 700 123-456")
    before = await login(api, created["email"])
    listed = (await call(api, "GET", f"/users/{created['id']}", token)).json()

    assert created["role"] == "foreman" and created["is_active"] is True
    assert created["pending_activation"] is True and created["mfa_enabled"] is False
    assert created["phone"] == "+996700123456" and created["project_ids"] == []
    assert before.status_code == 401 and before.json() == {"detail": "invalid_credentials"}
    assert listed == created


async def test_the_invite_link_sets_the_password_once_and_the_user_can_log_in(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    token = await token_for(api, "owner")
    created = await create_user(api, token, auth_world, "estimator")

    link = await activate(api, token, created["id"])
    again = await api.post(f"{AUTH}/activate", json={"token": link, "password": "another-pass-9"})
    signed_in = await login(api, created["email"])
    old_password = await login(api, created["email"], "another-pass-9")
    listed = (await call(api, "GET", f"/users/{created['id']}", token)).json()

    assert again.status_code == 400 and again.json() == {"detail": "invalid_invite"}
    assert signed_in.status_code == 200 and signed_in.json()["user"]["role"] == "estimator"
    assert old_password.status_code == 401
    assert listed["pending_activation"] is False


async def test_a_new_invite_cancels_the_previous_one_and_resets_a_known_password(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    token = await token_for(api, "owner")
    user_id, email = await new_active_user(api, token, auth_world, "estimator")
    session = (await login(api, email)).json()
    first = (await call(api, "POST", f"/users/{user_id}/invite", token)).json()
    second = (await call(api, "POST", f"/users/{user_id}/invite", token)).json()
    first_token, second_token = (item["path"].split("token=")[1] for item in (first, second))

    old = await api.post(
        f"{AUTH}/activate", json={"token": first_token, "password": "reset-pass-1234"}
    )
    fresh = await api.post(
        f"{AUTH}/activate", json={"token": second_token, "password": "reset-pass-1234"}
    )

    assert old.status_code == 400 and fresh.status_code == 204
    assert (await login(api, email, PASSWORD)).status_code == 401
    assert (await login(api, email, "reset-pass-1234")).status_code == 200
    assert await me(api, session["access_token"]) == 401  # прежние сеансы закрыты сбросом


async def test_an_expired_or_unknown_invite_is_refused_without_saying_why(
    api: AsyncClient, auth_world: AuthWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = await token_for(api, "owner")
    created = await create_user(api, token, auth_world, "estimator")
    monkeypatch.setenv("INVITE_TTL_HOURS", "0")
    get_settings.cache_clear()
    try:
        link = (await call(api, "POST", f"/users/{created['id']}/invite", token)).json()["path"]
    finally:
        monkeypatch.delenv("INVITE_TTL_HOURS")
        get_settings.cache_clear()

    expired = await api.post(
        f"{AUTH}/activate", json={"token": link.split("token=")[1], "password": PASSWORD}
    )
    unknown = await api.post(f"{AUTH}/activate", json={"token": "x" * 43, "password": PASSWORD})
    short = await api.post(f"{AUTH}/activate", json={"token": "x" * 43, "password": "short"})

    assert expired.status_code == unknown.status_code == 400
    assert expired.json() == unknown.json() == {"detail": "invalid_invite"}
    assert short.status_code == 422


async def test_guessing_invite_tokens_is_rate_limited(api: AsyncClient) -> None:
    codes = []
    for number in range(6):
        response = await api.post(
            f"{AUTH}/activate", json={"token": f"{number}" * 43, "password": PASSWORD}
        )
        codes.append(response.status_code)

    assert codes == [400, 400, 400, 400, 400, 429]


async def test_an_invite_is_not_issued_for_an_unknown_or_inactive_user(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    token = await token_for(api, "owner")
    created = await create_user(api, token, auth_world, "estimator")
    await call(api, "PUT", f"/users/{created['id']}/active", token, json={"active": False})

    inactive = await call(api, "POST", f"/users/{created['id']}/invite", token)
    unknown = await call(api, "POST", f"/users/{uuid4()}/invite", token)

    assert inactive.status_code == unknown.status_code == 404


async def test_the_invite_response_is_not_cacheable_and_the_token_is_not_stored_in_clear(
    api: AsyncClient, auth_world: AuthWorld, owner_sync_engine: Engine
) -> None:
    token = await token_for(api, "owner")
    created = await create_user(api, token, auth_world, "estimator")

    response = await call(api, "POST", f"/users/{created['id']}/invite", token)
    link = response.json()["path"].split("token=")[1]
    with owner_sync_engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE kut_auth"))
        stored = connection.execute(
            text("SELECT token_hash FROM user_invite WHERE user_id = :u"), {"u": created["id"]}
        ).scalar_one()

    assert response.headers["cache-control"] == "no-store"
    assert link.encode() not in bytes(stored) and len(bytes(stored)) == 32


@pytest.mark.parametrize(
    ("override", "status", "detail"),
    [
        ({"email": "not-an-email"}, 422, None),
        ({"phone": "12ab"}, 422, None),
        ({"email": None, "phone": None}, 422, None),
        ({"full_name": ""}, 422, None),
        ({"role_id": str(uuid4())}, 404, "not_found"),
    ],
)
async def test_user_creation_validates_the_input(
    api: AsyncClient,
    auth_world: AuthWorld,
    override: dict[str, Any],
    status: int,
    detail: str | None,
) -> None:
    token = await token_for(api, "owner")
    body = {
        "full_name": "Сотрудник (cfg)",
        "email": f"{unique('cfg-')}@{CFG_DOMAIN}",
        "role_id": str(auth_world.roles["foreman"]),
        **override,
    }

    response = await post_new(api, "/users", token, body)

    assert response.status_code == status
    if detail:
        assert response.json() == {"detail": detail}


async def test_an_email_or_phone_that_is_taken_gives_409_with_the_field(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    token = await token_for(api, "owner")
    first = await create_user(api, token, auth_world, "foreman", phone="+996700555111")
    role = str(auth_world.roles["foreman"])

    same_email = await post_new(
        api,
        "/users",
        token,
        {"full_name": "Копия", "email": first["email"].upper(), "role_id": role},
    )
    same_phone = await post_new(
        api, "/users", token, {"full_name": "Копия", "phone": "+996 700 555 111", "role_id": role}
    )

    assert (same_email.status_code, same_email.json()) == (409, {"detail": "email_taken"})
    assert (same_phone.status_code, same_phone.json()) == (409, {"detail": "phone_taken"})


async def test_an_external_role_needs_a_counterparty_and_an_internal_one_must_not_have_it(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    token = await token_for(api, "owner")
    sub = str(auth_world.counterparties[devseed.SUB_A])

    def spec(role: str, **extra: str) -> dict[str, str]:
        return {
            "full_name": "Контрагент (cfg)",
            "email": f"{unique('cfg-')}@{CFG_DOMAIN}",
            "role_id": str(auth_world.roles[role]),
            **extra,
        }

    external_alone = await post_new(api, "/users", token, spec("subcontractor"))
    internal_with = await post_new(api, "/users", token, spec("foreman", counterparty_id=sub))
    ghost = await post_new(
        api, "/users", token, spec("subcontractor", counterparty_id=str(uuid4()))
    )
    good = await post_new(api, "/users", token, spec("subcontractor", counterparty_id=sub))

    assert external_alone.status_code == internal_with.status_code == ghost.status_code == 422
    assert external_alone.json() == {"detail": "invalid_counterparty"}
    assert good.status_code == 201 and good.json()["counterparty_name"] == devseed.SUB_A


async def test_user_creation_is_idempotent_and_a_failed_attempt_does_not_burn_the_key(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    token, key = await token_for(api, "owner"), uuid4()
    body = {
        "full_name": "Сотрудник (cfg)",
        "email": f"{unique('cfg-')}@{CFG_DOMAIN}",
        "role_id": str(auth_world.roles["foreman"]),
    }
    bad = {**body, "role_id": str(uuid4())}

    failed = await call(api, "POST", "/users", token, json=bad, key=key)
    first = await call(api, "POST", "/users", token, json=body, key=key)
    again = await call(api, "POST", "/users", token, json=body, key=key)
    people = (await call(api, "GET", "/users", token, params={"q": body["email"]})).json()

    assert failed.status_code == 404  # откат вместе с ключом
    assert first.status_code == again.status_code == 201 and first.json() == again.json()
    assert again.headers["idempotent-replayed"] == "true" and len(people) == 1


async def test_parallel_user_creation_under_one_key_makes_one_user(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    token, key = await token_for(api, "owner"), uuid4()
    body = {
        "full_name": "Сотрудник (cfg)",
        "email": f"{unique('cfg-')}@{CFG_DOMAIN}",
        "role_id": str(auth_world.roles["foreman"]),
    }

    responses = await asyncio.gather(
        *(call(api, "POST", "/users", token, json=body, key=key) for _ in range(4))
    )
    people = (await call(api, "GET", "/users", token, params={"q": body["email"]})).json()

    assert [response.status_code for response in responses] == [201] * 4
    assert len({response.json()["id"] for response in responses}) == 1 and len(people) == 1


# --- список и фильтры ---


async def test_the_user_list_filters_by_role_activity_project_and_text(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    token = await token_for(api, "owner")
    east = auth_world.projects["east"]
    off = await create_user(api, token, auth_world, "estimator", full_name="Выключенный (cfg)")
    await call(api, "PUT", f"/users/{off['id']}/active", token, json={"active": False})

    foremen = (await call(api, "GET", "/users", token, params={"role": "foreman"})).json()
    inactive = (await call(api, "GET", "/users", token, params={"active": "false"})).json()
    on_east = (await call(api, "GET", "/users", token, params={"project_id": str(east)})).json()
    found = (await call(api, "GET", "/users", token, params={"q": "ВЫКЛЮЧЕН"})).json()

    assert foremen and {u["role"] for u in foremen} == {"foreman"}
    assert off["id"] in {u["id"] for u in inactive} and all(not u["is_active"] for u in inactive)
    east_ids = {u["id"] for u in on_east}
    team = {auth_world.users[n] for n in ("foreman", "master", "estimator", "customer", "sub_a")}
    assert {str(item) for item in team} <= east_ids
    assert str(auth_world.users["procurement"]) not in east_ids  # объект «Запад»
    assert str(auth_world.users["owner"]) in east_ids  # группа видит всё
    assert [u["id"] for u in found] == [off["id"]]


async def test_the_user_list_shows_the_security_state_but_never_a_secret(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    token = await token_for(api, "owner")

    people = (await call(api, "GET", "/users", token, params={"limit": 500})).json()
    by_id = {u["id"]: u for u in people}

    finance = by_id[str(auth_world.users["finance"])]
    foreman = by_id[str(auth_world.users["foreman"])]
    assert finance["mfa_enabled"] is True and finance["requires_2fa"] is True
    assert foreman["mfa_enabled"] is False and foreman["pending_activation"] is False
    assert set(foreman) == {
        "id", "full_name", "email", "phone", "role_id", "role", "role_name", "scope", "is_active",
        "mfa_enabled", "requires_2fa", "pending_activation", "counterparty_id", "counterparty_name",
        "project_ids", "legal_entity_ids", "notify_whatsapp", "notify_whatsapp_consent_at",
    }  # fmt: skip


# --- роль, активность ---


async def test_a_role_change_ends_the_old_sessions_and_the_next_login_has_the_new_role(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    token = await token_for(api, "owner")
    user_id, email = await new_active_user(api, token, auth_world, "foreman")
    session = (await login(api, email)).json()

    changed = await call(
        api,
        "PUT",
        f"/users/{user_id}/role",
        token,
        json={"role_id": str(auth_world.roles["storekeeper"])},
    )

    assert changed.status_code == 200 and changed.json()["role"] == "storekeeper"
    assert await me(api, session["access_token"]) == 401  # старый токен отклонён
    assert (await login(api, email)).json()["user"]["role"] == "storekeeper"


async def test_a_role_cannot_move_a_user_between_internal_and_external_or_to_nowhere(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    token = await token_for(api, "owner")
    created = await create_user(api, token, auth_world, "foreman")

    crossing = await call(
        api,
        "PUT",
        f"/users/{created['id']}/role",
        token,
        json={"role_id": str(auth_world.roles["customer"])},
    )
    nowhere = await call(
        api, "PUT", f"/users/{created['id']}/role", token, json={"role_id": str(uuid4())}
    )
    unknown_user = await call(
        api,
        "PUT",
        f"/users/{uuid4()}/role",
        token,
        json={"role_id": str(auth_world.roles["foreman"])},
    )

    assert crossing.status_code == 422 and crossing.json() == {"detail": "role_scope_mismatch"}
    assert nowhere.status_code == 404 and unknown_user.status_code == 404


async def test_the_owner_cannot_change_their_own_role_activity_or_two_factor(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    token = await token_for(api, "owner")
    me_id = auth_world.users["owner"]

    role = await call(
        api,
        "PUT",
        f"/users/{me_id}/role",
        token,
        json={"role_id": str(auth_world.roles["foreman"])},
    )
    active = await call(api, "PUT", f"/users/{me_id}/active", token, json={"active": False})
    mfa = await call(api, "POST", f"/users/{me_id}/reset-mfa", token)

    assert (role.status_code, active.status_code, mfa.status_code) == (403, 403, 403)
    assert await me(api, token) == 200


async def test_deactivation_blocks_login_and_ends_sessions_and_reactivation_restores_login(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    token = await token_for(api, "owner")
    user_id, email = await new_active_user(api, token, auth_world, "estimator")
    session = (await login(api, email)).json()

    off = await call(api, "PUT", f"/users/{user_id}/active", token, json={"active": False})
    blocked = await login(api, email)
    on = await call(api, "PUT", f"/users/{user_id}/active", token, json={"active": True})

    assert off.json()["is_active"] is False and on.json()["is_active"] is True
    assert blocked.status_code == 401 and blocked.json() == {"detail": "invalid_credentials"}
    assert await me(api, session["access_token"]) == 401
    assert (await login(api, email)).status_code == 200


# --- доступ к объектам и юрлицам ---


async def test_granting_and_removing_object_access_changes_the_projects_list_at_once(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    token = await token_for(api, "owner")
    east, west = str(auth_world.projects["east"]), str(auth_world.projects["west"])
    user_id, email = await new_active_user(api, token, auth_world, "foreman")
    own = (await login(api, email)).json()["access_token"]
    path = f"/users/{user_id}/access"

    empty = await projects_of(api, own)
    await call(api, "PUT", path, token, json={"project_ids": [east, west]})
    both = await projects_of(api, own)  # тем же токеном, без ожидания кэша
    await call(api, "PUT", path, token, json={"project_ids": [west]})
    only_west = await projects_of(api, own)
    removed = await call(api, "PUT", path, token, json={"project_ids": []})
    none_left = await projects_of(api, own)

    assert (empty, both, only_west, none_left) == (set(), {east, west}, {west}, set())
    assert removed.json()["project_ids"] == []


async def test_access_is_validated_against_the_role_and_the_directory(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    token = await token_for(api, "owner")
    east = str(auth_world.projects["east"])
    entity = str(next(iter(auth_world.legal_entities.values())))
    sub = await create_user(
        api,
        token,
        auth_world,
        "subcontractor",
        counterparty_id=auth_world.counterparties[devseed.SUB_A],
    )
    foreman = await create_user(api, token, auth_world, "foreman")

    group = await call(
        api,
        "PUT",
        f"/users/{auth_world.users['finance']}/access",
        token,
        json={"project_ids": [east]},
    )
    external_entity = await call(
        api,
        "PUT",
        f"/users/{sub['id']}/access",
        token,
        json={"project_ids": [east], "legal_entity_ids": [entity]},
    )
    ghost_project = await call(
        api, "PUT", f"/users/{foreman['id']}/access", token, json={"project_ids": [str(uuid4())]}
    )
    ghost_entity = await call(
        api,
        "PUT",
        f"/users/{foreman['id']}/access",
        token,
        json={"project_ids": [], "legal_entity_ids": [str(uuid4())]},
    )
    external_ok = await call(
        api, "PUT", f"/users/{sub['id']}/access", token, json={"project_ids": [east]}
    )
    unknown_user = await call(
        api, "PUT", f"/users/{uuid4()}/access", token, json={"project_ids": []}
    )

    assert group.status_code == 422 and group.json() == {"detail": "group_role_has_all_access"}
    assert external_entity.json() == {"detail": "external_has_no_legal_entity_access"}
    assert ghost_project.json() == {"detail": "unknown_project"}
    assert ghost_entity.json() == {"detail": "unknown_legal_entity"}
    assert external_ok.status_code == 200 and external_ok.json()["project_ids"] == [east]
    assert unknown_user.status_code == 404


async def test_access_changes_are_journaled_by_the_data_triggers(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    token = await token_for(api, "owner")
    created = await create_user(api, token, auth_world, "foreman")
    east = str(auth_world.projects["east"])

    await call(api, "PUT", f"/users/{created['id']}/access", token, json={"project_ids": [east]})
    await call(api, "PUT", f"/users/{created['id']}/access", token, json={"project_ids": []})
    with worker_sync_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT action, actor_id FROM audit_log WHERE kind = 'data'"
                " AND table_name = 'user_project_access' AND row_id IN"
                " (SELECT id FROM user_project_access WHERE user_id = :u) ORDER BY id"
            ),
            {"u": created["id"]},
        ).all()

    assert [row.action for row in rows] == ["insert", "soft_delete"]
    assert {row.actor_id for row in rows} == {auth_world.users["owner"]}


# --- сброс 2FA ---


async def test_resetting_two_factor_ends_sessions_and_forces_a_fresh_enrollment(
    api: AsyncClient, auth_world: AuthWorld, owner_sync_engine: Engine
) -> None:
    token = await token_for(api, "owner")
    user_id, email = await new_active_user(api, token, auth_world, "accountant")
    first = (await login(api, email)).json()
    setup = await api.post(
        f"{AUTH}/mfa/setup", json={"mfa_token": first["mfa_token"], "password": PASSWORD}
    )
    secret = setup.json()["secret"]
    confirmed = await api.post(
        f"{AUTH}/mfa/confirm", json={"mfa_token": first["mfa_token"], "code": totp_code(secret)}
    )
    access = confirmed.json()["session"]["access_token"]
    enrolled = (await call(api, "GET", f"/users/{user_id}", token)).json()["mfa_enabled"]

    reset = await call(api, "POST", f"/users/{user_id}/reset-mfa", token)
    again = (await login(api, email)).json()
    with owner_sync_engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE kut_auth"))
        codes_left = connection.execute(
            text("SELECT count(*) FROM recovery_code WHERE user_id = :u"), {"u": user_id}
        ).scalar_one()

    assert enrolled is True and reset.status_code == 200 and reset.json()["mfa_enabled"] is False
    assert await me(api, access) == 401
    assert (
        again["status"] == "mfa_enrollment_required"
    )  # роль с обязательной 2FA настраивает заново
    assert codes_left == 0
    assert (await call(api, "POST", f"/users/{uuid4()}/reset-mfa", token)).status_code == 404
