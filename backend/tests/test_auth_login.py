from collections.abc import Callable
from http.cookies import SimpleCookie
from typing import Any
from uuid import UUID

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from httpx import AsyncClient, Response
from sqlalchemy import Engine, text

from app.core.config import get_settings
from app.modules.auth.keys import get_jwt_keys
from tests.auth_support import (
    AUTH,
    PASSWORD,
    AuthWorld,
    bearer,
    client_from,
    login,
    sign_in,
    token_for,
)

pytestmark = pytest.mark.usefixtures("clean_redis")

REFRESH_COOKIE = "kut_refresh"


def refresh_cookie(response: Response) -> SimpleCookie:
    cookie: SimpleCookie = SimpleCookie()
    for header in response.headers.get_list("set-cookie"):
        cookie.load(header)
    return cookie


def comparable(response: Response) -> tuple[int, Any, dict[str, str]]:
    volatile = {"date", "x-request-id", "content-length"}
    headers = {k: v for k, v in response.headers.items() if k not in volatile}
    return response.status_code, response.json(), headers


async def test_foreman_logs_in_with_the_phone_and_gets_a_token_pair(api: AsyncClient) -> None:
    response = await api.post(
        f"{AUTH}/login", json={"identifier": "+996 555 100 001", "password": PASSWORD}
    )

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "ok" and body["token_type"] == "bearer"
    assert body["expires_in"] == 600
    assert body["user"]["role"] == "foreman" and body["user"]["amr"] == ["pwd"]
    assert response.headers["cache-control"] == "no-store"
    assert REFRESH_COOKIE in response.cookies


async def test_login_by_email_is_case_insensitive(api: AsyncClient) -> None:
    response = await api.post(
        f"{AUTH}/login", json={"identifier": "  Estimator@KUT.test ", "password": PASSWORD}
    )

    assert response.status_code == 200
    assert response.json()["user"]["role"] == "estimator"


async def test_the_refresh_cookie_is_httponly_secure_strict_and_scoped_to_the_auth_path(
    api: AsyncClient,
) -> None:
    morsel = refresh_cookie(await login(api, "foreman"))[REFRESH_COOKIE]

    assert morsel["httponly"] is True
    assert morsel["secure"] is True
    assert morsel["samesite"].lower() == "strict"
    assert morsel["path"] == "/api/v1/auth"
    assert "domain" not in morsel or not morsel["domain"]


@pytest.mark.parametrize(
    ("who", "days"), [("foreman", 30), ("master", 30), ("estimator", 14), ("lawyer", 14)]
)
async def test_the_refresh_lifetime_is_30_days_for_field_roles_and_14_for_the_rest(
    api: AsyncClient, who: str, days: int
) -> None:
    morsel = refresh_cookie(await login(api, who))[REFRESH_COOKIE]

    assert int(morsel["max-age"]) == days * 86400


async def test_a_wrong_password_an_unknown_login_and_an_inactive_user_look_identical(
    api: AsyncClient,
    new_user: Callable[..., tuple[UUID, str]],
    worker_sync_engine: Engine,
) -> None:
    inactive_id, inactive_email = new_user("foreman")
    with worker_sync_engine.begin() as connection:
        connection.execute(
            text('UPDATE "user" SET is_active = false WHERE id = :id'), {"id": inactive_id}
        )

    wrong_password = await login(api, "foreman", "not-the-password")
    unknown_login = await login(api, "nobody-at-all", PASSWORD)
    inactive = await login(api, inactive_email, PASSWORD)

    assert wrong_password.status_code == 401
    assert comparable(wrong_password) == comparable(unknown_login) == comparable(inactive)
    assert wrong_password.json() == {"detail": "invalid_credentials"}
    assert REFRESH_COOKIE not in wrong_password.cookies


async def test_the_fifth_failure_blocks_the_login_even_for_the_right_password(
    app: FastAPI, api: AsyncClient
) -> None:
    for number in range(5):  # с разных адресов: блокировку даёт именно логин, а не адрес
        async with client_from(app, f"10.0.1.{number}") as other:
            assert (await login(other, "master", "wrong-password-1")).status_code == 401

    async with client_from(app, "10.0.1.99") as fresh:
        blocked = await login(fresh, "master", PASSWORD)

    assert blocked.status_code == 429
    assert blocked.json() == {"detail": "too_many_attempts"}
    assert 1 <= int(blocked.headers["retry-after"]) <= 900
    assert REFRESH_COOKIE not in blocked.cookies
    assert (await login(api, "foreman", PASSWORD)).status_code == 200  # другой логин не затронут


async def test_the_block_looks_the_same_for_a_login_that_does_not_exist(app: FastAPI) -> None:
    for number in range(5):
        for name in ("master", "ghost-user"):
            async with client_from(app, f"10.0.2.{number}") as other:
                await login(other, name, "wrong-password-1")

    async with client_from(app, "10.0.2.99") as fresh_a, client_from(app, "10.0.2.98") as fresh_b:
        real = await login(fresh_a, "master", PASSWORD)
        ghost = await login(fresh_b, "ghost-user", PASSWORD)

    assert real.status_code == ghost.status_code == 429
    assert real.json() == ghost.json()
    assert set(real.headers) == set(ghost.headers)


async def test_a_successful_login_resets_the_counter_of_that_login(
    app: FastAPI, api: AsyncClient
) -> None:
    async def attempt(number: int, password: str) -> int:
        async with client_from(app, f"10.0.3.{number}") as other:
            return (await login(other, "estimator", password)).status_code

    for number in range(4):
        assert await attempt(number, "wrong-password-1") == 401
    assert await attempt(10, PASSWORD) == 200

    for number in range(11, 15):
        assert await attempt(number, "wrong-password-1") == 401
    assert await attempt(20, PASSWORD) == 200


async def test_five_failures_from_one_address_block_every_login_from_it_and_only_from_it(
    app: FastAPI, api: AsyncClient
) -> None:
    for number in range(5):
        await login(api, f"someone-{number}", "wrong-password-1")

    blocked = await login(api, "lawyer", PASSWORD)
    async with client_from(app, "10.0.4.1") as elsewhere:
        allowed = await login(elsewhere, "lawyer", PASSWORD)

    assert blocked.status_code == 429
    assert allowed.status_code == 200


async def test_me_returns_the_principal_without_secrets(api: AsyncClient) -> None:
    token = await token_for(api, "foreman")

    response = await api.get(f"{AUTH}/me", headers=bearer(token))

    body = response.json()
    assert response.status_code == 200
    assert body["role"] == "foreman" and body["scope"] == "project" and body["is_internal"] is True
    assert "objects:write" in body["permissions"] and "finance:write" not in body["permissions"]
    assert len(body["project_ids"]) == 1
    assert set(body) == {
        "id", "full_name", "role", "scope", "is_internal", "counterparty_id", "permissions",
        "project_ids", "amr",
    }  # fmt: skip
    assert "password" not in response.text and "argon2" not in response.text


async def test_endpoints_without_a_valid_token_answer_401(api: AsyncClient) -> None:
    token = await token_for(api, "foreman")
    header, payload, signature = token.split(".")
    flipped = signature[:-2] + ("A" if signature[-2] != "A" else "B") + signature[-1]

    bad_tokens = {
        "none": None,
        "garbage": "not-a-jwt",
        "tampered-signature": f"{header}.{payload}.{flipped}",
        "tampered-payload": f"{header}.{payload[:-2]}xx.{signature}",
    }
    for name, value in bad_tokens.items():
        headers = bearer(value) if value else {}
        response = await api.get(f"{AUTH}/me", headers=headers)
        assert response.status_code == 401, name
        assert response.headers["www-authenticate"] == "Bearer", name


async def test_a_token_with_alg_none_or_a_foreign_key_is_rejected(api: AsyncClient) -> None:
    token = await token_for(api, "foreman")
    claims = jwt.decode(token, options={"verify_signature": False})
    kid = get_jwt_keys().active_kid
    unsigned = jwt.encode(claims, None, algorithm="none")
    foreign = jwt.encode(
        claims, Ed25519PrivateKey.generate(), algorithm="EdDSA", headers={"kid": kid}
    )
    unknown_kid = jwt.encode(
        claims, Ed25519PrivateKey.generate(), algorithm="EdDSA", headers={"kid": "no-such-kid"}
    )
    hmac_with_public = jwt.encode(claims, "secret-material-123456789012345678", algorithm="HS256")

    for forged in (unsigned, foreign, unknown_kid, hmac_with_public):
        response = await api.get(f"{AUTH}/me", headers=bearer(forged))
        assert response.status_code == 401


async def test_an_expired_access_token_is_rejected(
    api: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ACCESS_TTL_SECONDS", "-5")
    get_settings.cache_clear()
    try:
        token = await token_for(api, "foreman")
    finally:
        monkeypatch.delenv("ACCESS_TTL_SECONDS")
        get_settings.cache_clear()

    assert (await api.get(f"{AUTH}/me", headers=bearer(token))).status_code == 401


async def test_the_access_token_carries_no_permissions_only_identity_and_session(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    token = await token_for(api, "foreman")

    claims = jwt.decode(token, options={"verify_signature": False})
    header = jwt.get_unverified_header(token)

    assert header["alg"] == "EdDSA" and header["kid"] == get_jwt_keys().active_kid
    assert set(claims) == {
        "iss",
        "aud",
        "iat",
        "exp",
        "jti",
        "typ",
        "sub",
        "sid",
        "role_ver",
        "amr",
    }
    assert claims["sub"] == str(auth_world.users["foreman"]) and claims["typ"] == "access"
    assert claims["exp"] - claims["iat"] == 600


async def test_the_request_id_header_is_returned_and_a_valid_one_is_honoured(
    api: AsyncClient,
) -> None:
    given = "6f1c2f0e-5c7b-4a53-9a3e-8f1d6f0c1a11"

    generated = await api.get("/health")
    echoed = await api.get("/health", headers={"X-Request-ID": given})
    rejected = await api.get("/health", headers={"X-Request-ID": "not-a-uuid"})

    assert len(generated.headers["x-request-id"]) == 36
    assert echoed.headers["x-request-id"] == given
    assert rejected.headers["x-request-id"] != "not-a-uuid"


async def test_signing_in_twice_creates_independent_sessions(api: AsyncClient) -> None:
    first = await sign_in(api, "estimator")
    second = await sign_in(api, "estimator")

    assert first["access_token"] != second["access_token"]
    for body in (first, second):
        response = await api.get(f"{AUTH}/me", headers=bearer(body["access_token"]))
        assert response.status_code == 200
