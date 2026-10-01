import hashlib
import json
from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi import FastAPI
from httpx import AsyncClient, Response
from sqlalchemy import Engine, text

from app.core.context import use_context
from app.core.db import get_sessionmaker
from app.core.redis import get_redis
from app.modules.auth import admin
from app.modules.auth.errors import NotFound, PermissionDenied
from app.modules.auth.principal import Principal, principal_store
from tests.auth_support import (
    AUTH,
    NEW_PASSWORD,
    PASSWORD,
    AuthWorld,
    bearer,
    client_from,
    login,
)

pytestmark = pytest.mark.usefixtures("clean_redis")

REFRESH_COOKIE = "kut_refresh"


def sid_of(token: str) -> UUID:
    return UUID(jwt.decode(token, options={"verify_signature": False})["sid"])


def user_of(token: str) -> UUID:
    return UUID(jwt.decode(token, options={"verify_signature": False})["sub"])


async def start(client: AsyncClient, who: str) -> tuple[str, str]:
    """(access, refresh) нового сеанса; refresh берётся из Set-Cookie ответа."""
    response = await login(client, who)
    assert response.status_code == 200, response.text
    return response.json()["access_token"], response.cookies[REFRESH_COOKIE]


async def refresh_with(client: AsyncClient, token: str) -> Response:
    return await client.post(f"{AUTH}/refresh", headers={"Cookie": f"{REFRESH_COOKIE}={token}"})


async def me(client: AsyncClient, access: str) -> int:
    return (await client.get(f"{AUTH}/me", headers=bearer(access))).status_code


def events(engine: Engine, action: str, row_id: UUID) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT actor_id, actor_kind, ip, meta FROM audit_log"
                " WHERE kind = 'event' AND action = :action AND row_id = :row_id ORDER BY id"
            ),
            {"action": action, "row_id": row_id},
        )
        return [dict(row._mapping) for row in rows]


async def actor_principal(user_id: UUID) -> Principal:
    record = await principal_store.reload(user_id)
    assert record is not None
    return Principal.from_record(record, sid=uuid4(), amr=("pwd", "otp"), otp_at=None)


async def as_admin(user_id: UUID, action: Callable[[Any], Any]) -> Any:
    principal = await actor_principal(user_id)
    with use_context(principal.to_context(None)):
        async with get_sessionmaker()() as session:
            return await action(session)


async def test_refresh_rotates_the_token_and_keeps_the_session_and_its_lifetime(
    api: AsyncClient,
) -> None:
    access, first = await start(api, "foreman")

    response = await refresh_with(api, first)
    second = response.cookies[REFRESH_COOKIE]
    body = response.json()

    assert response.status_code == 200 and second != first
    assert body["access_token"] != access and body["status"] == "ok"
    assert sid_of(body["access_token"]) == sid_of(access)
    assert "max-age=2592000" in response.headers["set-cookie"].lower()
    assert await me(api, access) == 200  # прежний access живёт до своего срока
    assert (await refresh_with(api, second)).status_code == 200


async def test_reusing_a_rotated_refresh_token_revokes_the_whole_family(
    api: AsyncClient, worker_sync_engine: Engine
) -> None:
    access, stolen = await start(api, "foreman")
    legit = await refresh_with(api, stolen)  # законная ротация: токен stolen теперь «использован»
    rotated = legit.json()
    successor = legit.cookies[REFRESH_COOKIE]

    replay = await refresh_with(api, stolen)  # повтор старого токена: признак кражи

    assert replay.status_code == 401
    assert "max-age=0" in replay.headers["set-cookie"].lower()
    # Всё, что выдано в этой семье, мертво, в том числе токены законного владельца.
    assert await me(api, rotated["access_token"]) == 401
    assert await me(api, access) == 401
    assert (await refresh_with(api, successor)).status_code == 401
    (event,) = events(worker_sync_engine, "refresh_reuse", sid_of(access))
    assert event["actor_id"] == user_of(access)


async def test_after_reuse_the_other_session_of_the_user_still_works(api: AsyncClient) -> None:
    _, stolen = await start(api, "foreman")
    other_access, other_refresh = await start(api, "foreman")
    await refresh_with(api, stolen)
    await refresh_with(api, stolen)

    assert await me(api, other_access) == 200
    assert (await refresh_with(api, other_refresh)).status_code == 200


async def test_refresh_without_a_cookie_or_with_garbage_answers_401_and_clears_the_cookie(
    api: AsyncClient,
) -> None:
    for response in (
        await api.post(f"{AUTH}/refresh"),
        await refresh_with(api, "garbage-token-value"),
    ):
        assert response.status_code == 401
        assert response.json() == {"detail": "invalid_token"}
        assert response.headers["cache-control"] == "no-store"
        assert "max-age=0" in response.headers["set-cookie"].lower()


async def test_an_expired_refresh_token_is_rejected(
    api: AsyncClient, owner_sync_engine: Engine
) -> None:
    access, refresh = await start(api, "estimator")
    with owner_sync_engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE kut_auth"))
        connection.execute(
            text(
                "UPDATE refresh_token SET expires_at = now() - interval '1 minute'"
                " WHERE family_id = :family"
            ),
            {"family": sid_of(access)},
        )

    assert (await refresh_with(api, refresh)).status_code == 401


async def test_the_refresh_token_is_stored_only_as_a_hash(
    api: AsyncClient, owner_sync_engine: Engine
) -> None:
    access, refresh = await start(api, "estimator")

    with owner_sync_engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE kut_auth"))
        stored = connection.execute(
            text("SELECT token_hash FROM refresh_token WHERE family_id = :f"),
            {"f": sid_of(access)},
        ).scalar_one()

    assert bytes(stored) == hashlib.sha256(refresh.encode()).digest()


async def test_logout_revokes_the_session_everywhere_and_is_idempotent(
    api: AsyncClient, worker_sync_engine: Engine
) -> None:
    access, refresh = await start(api, "foreman")
    other_access, _ = await start(api, "foreman")

    out = await api.post(
        f"{AUTH}/logout", headers={**bearer(access), "Cookie": f"{REFRESH_COOKIE}={refresh}"}
    )

    assert out.status_code == 204 and out.content == b""
    assert "max-age=0" in out.headers["set-cookie"].lower()
    assert await me(api, access) == 401  # denylist по sid, не ждём истечения access
    assert (await refresh_with(api, refresh)).status_code == 401
    assert await me(api, other_access) == 200  # другие сеансы пользователя живы
    again = await api.post(
        f"{AUTH}/logout", headers={**bearer(access), "Cookie": f"{REFRESH_COOKIE}={refresh}"}
    )
    assert again.status_code == 204
    assert len(events(worker_sync_engine, "logout", sid_of(access))) == 1


async def test_logout_works_with_an_expired_access_token_and_still_kills_the_refresh_token(
    api: AsyncClient,
) -> None:
    _, refresh = await start(api, "estimator")

    out = await api.post(
        f"{AUTH}/logout",
        headers={**bearer("expired.or.garbage"), "Cookie": f"{REFRESH_COOKIE}={refresh}"},
    )

    assert out.status_code == 204
    assert (await refresh_with(api, refresh)).status_code == 401


async def test_the_denylist_entry_lives_as_long_as_the_access_token(api: AsyncClient) -> None:
    access, refresh = await start(api, "estimator")
    await api.post(
        f"{AUTH}/logout", headers={**bearer(access), "Cookie": f"{REFRESH_COOKIE}={refresh}"}
    )

    ttl = await get_redis().ttl(f"deny:sid:{sid_of(access)}")

    assert 600 <= ttl <= 630


async def test_a_role_change_invalidates_every_token_of_the_user_at_once(
    api: AsyncClient,
    auth_world: AuthWorld,
    new_user: Callable[..., tuple[UUID, str]],
    worker_sync_engine: Engine,
) -> None:
    user_id, email = new_user("foreman")
    access, refresh = await start(api, email)
    assert await me(api, access) == 200

    role_ver = await as_admin(
        auth_world.users["owner"],
        lambda s: admin.assign_role(s, user_id=user_id, role_id=auth_world.roles["storekeeper"]),
    )

    assert role_ver == 2
    assert await me(api, access) == 401  # сразу, не через минуту кэша
    assert (await refresh_with(api, refresh)).status_code == 401
    fresh = (await login(api, email)).json()
    assert fresh["user"]["role"] == "storekeeper"
    (event,) = events(worker_sync_engine, "role_assigned", user_id)
    assert event["actor_id"] == auth_world.users["owner"] and event["actor_kind"] == "user"
    assert event["meta"]["role_ver"] == 2
    assert event["meta"]["new_role_id"] == str(auth_world.roles["storekeeper"])


async def test_only_a_settings_admin_can_change_roles_and_not_their_own(
    api: AsyncClient,
    auth_world: AuthWorld,
    new_user: Callable[..., tuple[UUID, str]],
    worker_sync_engine: Engine,
) -> None:
    user_id, _ = new_user("foreman")
    role = auth_world.roles["estimator"]

    for actor in ("finance", "techdir", "foreman", "accountant"):
        with pytest.raises(PermissionDenied):
            await as_admin(
                auth_world.users[actor],
                lambda s: admin.assign_role(s, user_id=user_id, role_id=role),
            )
    with pytest.raises(PermissionDenied):
        await as_admin(
            auth_world.users["owner"],
            lambda s: admin.assign_role(
                s, user_id=auth_world.users["owner"], role_id=auth_world.roles["foreman"]
            ),
        )
    with pytest.raises(NotFound):
        await as_admin(
            auth_world.users["owner"], lambda s: admin.assign_role(s, user_id=uuid4(), role_id=role)
        )
    with pytest.raises(NotFound):
        await as_admin(
            auth_world.users["owner"],
            lambda s: admin.assign_role(s, user_id=user_id, role_id=uuid4()),
        )

    with worker_sync_engine.connect() as connection:
        current = connection.execute(
            text('SELECT role_id, role_ver FROM "user" WHERE id = :id'), {"id": user_id}
        ).one()
    assert current.role_id == auth_world.roles["foreman"] and current.role_ver == 1


async def test_deactivating_a_user_ends_their_sessions_and_blocks_new_ones(
    api: AsyncClient,
    auth_world: AuthWorld,
    new_user: Callable[..., tuple[UUID, str]],
    worker_sync_engine: Engine,
) -> None:
    user_id, email = new_user("estimator")
    access, refresh = await start(api, email)

    await as_admin(
        auth_world.users["owner"], lambda s: admin.set_user_active(s, user_id=user_id, active=False)
    )

    assert await me(api, access) == 401
    assert (await refresh_with(api, refresh)).status_code == 401
    assert (await login(api, email)).status_code == 401
    assert len(events(worker_sync_engine, "user_deactivated", user_id)) == 1

    await as_admin(
        auth_world.users["owner"], lambda s: admin.set_user_active(s, user_id=user_id, active=True)
    )
    assert await me(api, access) == 401  # старые токены не оживают
    assert (await login(api, email)).status_code == 200


async def test_changing_the_password_keeps_this_session_and_revokes_the_others(
    api: AsyncClient,
    app: FastAPI,
    new_user: Callable[..., tuple[UUID, str]],
    worker_sync_engine: Engine,
) -> None:
    user_id, email = new_user("estimator")
    keep_access, keep_refresh = await start(api, email)
    async with client_from(app, "10.0.9.1") as phone:
        other_access, other_refresh = await start(phone, email)

    changed = await api.post(
        f"{AUTH}/password",
        headers=bearer(keep_access),
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )

    assert changed.status_code == 204
    assert await me(api, keep_access) == 200
    assert (await refresh_with(api, keep_refresh)).status_code == 200
    assert await me(api, other_access) == 401
    assert (await refresh_with(api, other_refresh)).status_code == 401
    assert (await login(api, email, PASSWORD)).status_code == 401
    assert (await login(api, email, NEW_PASSWORD)).status_code == 200
    (event,) = events(worker_sync_engine, "password_changed", user_id)
    assert event["meta"]["revoked_sessions"] == 1
    with worker_sync_engine.connect() as connection:
        stored = connection.execute(
            text('SELECT password_hash FROM "user" WHERE id = :id'), {"id": user_id}
        ).scalar_one()
        journal = connection.execute(
            text("SELECT old, new FROM audit_log WHERE table_name = 'user' AND row_id = :id"),
            {"id": user_id},
        ).all()
    assert stored.startswith("$argon2id$")
    assert "password_hash" not in json.dumps([[row.old, row.new] for row in journal])
    assert NEW_PASSWORD not in json.dumps([[row.old, row.new] for row in journal])


async def test_password_change_validates_the_current_and_the_new_password(
    api: AsyncClient, new_user: Callable[..., tuple[UUID, str]]
) -> None:
    _, email = new_user("estimator")
    token = (await login(api, email)).json()["access_token"]

    def change(current: str, new: str) -> Any:
        return api.post(
            f"{AUTH}/password",
            headers=bearer(token),
            json={"current_password": current, "new_password": new},
        )

    wrong = await change("wrong-current-password", NEW_PASSWORD)
    same = await change(PASSWORD, PASSWORD)
    short = await change(PASSWORD, "short")
    anonymous = await api.post(
        f"{AUTH}/password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    )

    assert (wrong.status_code, wrong.json()) == (400, {"detail": "invalid_current_password"})
    assert (same.status_code, same.json()) == (422, {"detail": "password_unchanged"})
    assert short.status_code == 422
    assert anonymous.status_code == 401
    assert (await login(api, email, PASSWORD)).status_code == 200  # пароль не менялся


async def test_guessing_the_current_password_is_rate_limited(
    api: AsyncClient, new_user: Callable[..., tuple[UUID, str]]
) -> None:
    _, email = new_user("estimator")
    token = (await login(api, email)).json()["access_token"]

    codes = []
    for _ in range(6):
        response = await api.post(
            f"{AUTH}/password",
            headers=bearer(token),
            json={"current_password": "wrong-current-password", "new_password": NEW_PASSWORD},
        )
        codes.append(response.status_code)

    assert codes == [400, 400, 400, 400, 400, 429]


async def test_the_principal_cache_is_keyed_by_role_ver_and_expires(api: AsyncClient) -> None:
    access, _ = await start(api, "foreman")
    user = jwt.decode(access, options={"verify_signature": False})["sub"]

    assert await me(api, access) == 200
    redis = get_redis()

    role_ver = await redis.get(f"rv:{user}")
    assert role_ver == "1"
    assert 0 < await redis.ttl(f"ctx:{user}:{role_ver}") <= 60
    cached = json.loads(await redis.get(f"ctx:{user}:{role_ver}"))
    assert cached["role_code"] == "foreman" and "password_hash" not in cached


async def test_an_external_user_changes_the_password_through_the_app_login(
    api: AsyncClient, new_user: Callable[..., tuple[UUID, str]]
) -> None:
    """Внешние роли ходят в БД через kut_portal, но функции учётной записи есть только у kut_app."""
    _, email = new_user("customer")
    token = (await login(api, email)).json()["access_token"]

    changed = await api.post(
        f"{AUTH}/password",
        headers=bearer(token),
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )

    assert changed.status_code == 204
    assert (await login(api, email, PASSWORD)).status_code == 401
    assert (await login(api, email, NEW_PASSWORD)).status_code == 200
