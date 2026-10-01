import hashlib
import re
from collections.abc import Callable
from typing import Any
from uuid import UUID

import jwt
import pytest
from fastapi import FastAPI
from httpx import AsyncClient, Response
from sqlalchemy import Engine, text

from app.core.config import get_settings
from app.modules.auth.crypto import SecretDecryptionError, decrypt_totp_secret
from tests.auth_support import (
    AUTH,
    PASSWORD,
    TOTP_SECRET,
    AuthWorld,
    bearer,
    client_from,
    login,
    sign_in,
    totp_code,
)

pytestmark = pytest.mark.usefixtures("clean_redis")

RECOVERY_CODE = re.compile(r"^[0-9a-f]{4}(-[0-9a-f]{4}){3}$")


async def verify(client: AsyncClient, mfa_token: str, **proof: str) -> Response:
    return await client.post(f"{AUTH}/mfa/verify", json={"mfa_token": mfa_token, **proof})


def audit_actions(engine: Engine, user_id: UUID) -> list[str]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT action FROM audit_log WHERE kind = 'event'"
                " AND (row_id = :id OR actor_id = :id) ORDER BY id"
            ),
            {"id": user_id},
        )
        return [row.action for row in rows]


async def enroll(client: AsyncClient, email: str) -> tuple[str, list[str], Response]:
    """Обязательная настройка 2FA при первом входе: (секрет, коды восстановления, ответ confirm)."""
    first = (await login(client, email)).json()
    assert first["status"] == "mfa_enrollment_required"
    token = first["mfa_token"]
    setup = await client.post(f"{AUTH}/mfa/setup", json={"mfa_token": token, "password": PASSWORD})
    secret = setup.json()["secret"]
    confirmed = await client.post(
        f"{AUTH}/mfa/confirm", json={"mfa_token": token, "code": totp_code(secret)}
    )
    assert confirmed.status_code == 200, confirmed.text
    return secret, confirmed.json()["recovery_codes"], confirmed


async def test_finance_director_gets_no_session_until_the_second_factor_is_proven(
    api: AsyncClient,
) -> None:
    first = await login(api, "finance")

    body = first.json()
    assert first.status_code == 200 and body["status"] == "mfa_required"
    assert "access_token" not in body and "kut_refresh" not in first.cookies
    assert 0 < body["expires_in"] <= 300

    second = await verify(api, body["mfa_token"], code=totp_code())

    data = second.json()
    claims = jwt.decode(data["access_token"], options={"verify_signature": False})
    assert second.status_code == 200 and data["user"]["role"] == "finance_director"
    assert data["user"]["amr"] == ["pwd", "otp"] and claims["amr"] == ["pwd", "otp"]
    assert claims["otp_at"] > 0 and "kut_refresh" in second.cookies


async def test_a_wrong_code_gives_nothing_and_leaves_a_trace(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    mfa_token = (await login(api, "finance")).json()["mfa_token"]

    response = await verify(api, mfa_token, code="000000")

    assert response.status_code == 401 and response.json() == {"detail": "invalid_code"}
    assert "kut_refresh" not in response.cookies
    assert "mfa_failed" in audit_actions(worker_sync_engine, auth_world.users["finance"])


async def test_a_code_that_was_already_used_cannot_be_replayed(api: AsyncClient) -> None:
    code = totp_code()
    first = await verify(api, (await login(api, "owner")).json()["mfa_token"], code=code)
    replay = await verify(api, (await login(api, "owner")).json()["mfa_token"], code=code)

    assert first.status_code == 200
    assert replay.status_code == 401 and replay.json() == {"detail": "invalid_code"}


async def test_codes_from_the_neighbouring_30_second_steps_are_accepted_once(
    api: AsyncClient,
) -> None:
    for offset in (0, 1):  # -1 не берём: на границе шага код «вчерашнего» окна мог бы устареть
        token = (await login(api, "finance")).json()["mfa_token"]
        assert (await verify(api, token, code=totp_code(step_offset=offset))).status_code == 200

    token = (await login(api, "finance")).json()["mfa_token"]
    assert (await verify(api, token, code=totp_code(step_offset=3))).status_code == 401


async def test_malformed_codes_are_rejected_before_any_secret_is_touched(api: AsyncClient) -> None:
    token = (await login(api, "finance")).json()["mfa_token"]

    for code in ("12345", "abcdef", "1234567", "12 34 5", "١٢٣٤٥٦"):  # до лимита в 5 попыток
        assert (await verify(api, token, code=code)).status_code == 401, code
    both = await verify(api, token, code="123456", recovery_code="aaaa-bbbb-cccc-dddd")
    neither = await api.post(f"{AUTH}/mfa/verify", json={"mfa_token": token})
    assert both.status_code == 422 and neither.status_code == 422


async def test_five_wrong_codes_block_even_the_right_one(api: AsyncClient) -> None:
    token = (await login(api, "finance")).json()["mfa_token"]
    for _ in range(5):
        assert (await verify(api, token, code="000000")).status_code == 401

    blocked = await verify(api, token, code=totp_code())

    assert blocked.status_code == 429 and "retry-after" in blocked.headers


async def test_mfa_tokens_are_bound_to_their_purpose(api: AsyncClient) -> None:
    verify_token = (await login(api, "finance")).json()["mfa_token"]
    access = (await sign_in(api, "owner"))["access_token"]

    as_bearer = await api.get(f"{AUTH}/me", headers=bearer(verify_token))
    access_as_mfa = await verify(api, access, code=totp_code(step_offset=1))
    verify_as_enroll = await api.post(
        f"{AUTH}/mfa/setup", json={"mfa_token": verify_token, "password": PASSWORD}
    )

    assert as_bearer.status_code == 401
    assert access_as_mfa.status_code == 401
    assert verify_as_enroll.status_code == 401


async def test_an_expired_mfa_token_is_rejected(
    api: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MFA_TOKEN_TTL_SECONDS", "-5")
    get_settings.cache_clear()
    try:
        token = (await login(api, "finance")).json()["mfa_token"]
    finally:
        monkeypatch.delenv("MFA_TOKEN_TTL_SECONDS")
        get_settings.cache_clear()

    assert (await verify(api, token, code=totp_code())).status_code == 401


async def test_an_unenrolled_accountant_cannot_get_a_session_without_setting_up_2fa(
    api: AsyncClient, new_user: Callable[..., tuple[UUID, str]]
) -> None:
    _, email = new_user("accountant")

    response = await login(api, email)
    body = response.json()

    assert response.status_code == 200 and body["status"] == "mfa_enrollment_required"
    assert "access_token" not in body and "kut_refresh" not in response.cookies
    assert (await api.get(f"{AUTH}/me", headers=bearer(body["mfa_token"]))).status_code == 401
    no_token = await api.post(f"{AUTH}/mfa/setup", json={"password": PASSWORD})
    assert no_token.status_code == 401
    enroll_as_verify = await verify(api, body["mfa_token"], code=totp_code())
    assert enroll_as_verify.status_code == 401


async def test_enrollment_issues_a_secret_ten_recovery_codes_and_the_first_session(
    api: AsyncClient,
    new_user: Callable[..., tuple[UUID, str]],
    worker_sync_engine: Engine,
    owner_sync_engine: Engine,
) -> None:
    user_id, email = new_user("accountant")
    token = (await login(api, email)).json()["mfa_token"]

    setup = await api.post(f"{AUTH}/mfa/setup", json={"mfa_token": token, "password": PASSWORD})
    secret = setup.json()["secret"]
    uri = setup.json()["otpauth_uri"]
    wrong = await api.post(f"{AUTH}/mfa/confirm", json={"mfa_token": token, "code": "000000"})
    confirmed = await api.post(
        f"{AUTH}/mfa/confirm", json={"mfa_token": token, "code": totp_code(secret)}
    )

    assert uri.startswith("otpauth://totp/") and "issuer=Kut" in uri and secret in uri
    assert wrong.status_code == 401
    body = confirmed.json()
    codes = body["recovery_codes"]
    assert len(codes) == 10 and len(set(codes)) == 10
    assert all(RECOVERY_CODE.match(code) for code in codes)
    assert body["session"]["status"] == "ok" and body["session"]["user"]["amr"] == ["pwd", "otp"]
    assert "kut_refresh" in confirmed.cookies
    with owner_sync_engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE kut_auth"))
        row = connection.execute(
            text(
                'SELECT totp_secret_enc, totp_key_id, totp_confirmed_at FROM "user" WHERE id = :id'
            ),
            {"id": user_id},
        ).one()
    assert secret.encode() not in bytes(row.totp_secret_enc) and row.totp_key_id == "dev-1"
    assert row.totp_confirmed_at is not None
    assert "mfa_enabled" in audit_actions(worker_sync_engine, user_id)
    with owner_sync_engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE kut_auth"))
        hashes = {
            bytes(item)
            for item in connection.scalars(
                text("SELECT code_hash FROM recovery_code WHERE user_id = :id"), {"id": user_id}
            )
        }
    assert hashes == {hashlib.sha256(code.encode()).digest() for code in codes}


async def test_once_enrolled_the_login_asks_for_a_code_and_setup_cannot_be_repeated(
    api: AsyncClient, new_user: Callable[..., tuple[UUID, str]]
) -> None:
    _, email = new_user("accountant")
    secret, _codes, confirmed = await enroll(api, email)
    session_access = confirmed.json()["session"]["access_token"]

    again = (await login(api, email)).json()
    done = await verify(api, again["mfa_token"], code=totp_code(secret, step_offset=1))
    repeat_setup = await api.post(
        f"{AUTH}/mfa/setup", json={"password": PASSWORD}, headers=bearer(session_access)
    )

    assert again["status"] == "mfa_required"
    assert done.status_code == 200 and done.json()["user"]["amr"] == ["pwd", "otp"]
    assert repeat_setup.status_code == 409 and repeat_setup.json() == {
        "detail": "mfa_already_enabled"
    }


async def test_confirm_without_a_started_setup_is_refused(
    api: AsyncClient, new_user: Callable[..., tuple[UUID, str]]
) -> None:
    _, email = new_user("accountant")
    token = (await login(api, email)).json()["mfa_token"]

    no_setup = await api.post(f"{AUTH}/mfa/confirm", json={"mfa_token": token, "code": totp_code()})

    assert no_setup.status_code == 409 and no_setup.json() == {"detail": "mfa_not_started"}


async def test_a_recovery_code_logs_in_once_and_is_case_insensitive(
    api: AsyncClient,
    app: FastAPI,
    new_user: Callable[..., tuple[UUID, str]],
    worker_sync_engine: Engine,
) -> None:
    user_id, email = new_user("accountant")
    _secret, codes, _ = await enroll(api, email)

    async def attempt(code: str, ip: str) -> Response:
        async with client_from(app, ip) as other:
            token = (await login(other, email)).json()["mfa_token"]
            return await verify(other, token, recovery_code=code)

    first = await attempt(codes[0], "10.0.5.1")
    spent = await attempt(codes[0], "10.0.5.2")
    shouted = await attempt(f" {codes[1].upper()} ", "10.0.5.3")
    unknown = await attempt("0000-0000-0000-0000", "10.0.5.4")

    assert first.status_code == 200 and first.json()["user"]["amr"] == ["pwd", "rcv"]
    assert spent.status_code == 401 and spent.json() == {"detail": "invalid_code"}
    assert shouted.status_code == 200
    assert unknown.status_code == 401
    with worker_sync_engine.connect() as connection:
        left = connection.execute(
            text(
                "SELECT meta->>'left' FROM audit_log WHERE action = 'recovery_code_used'"
                " AND row_id = :id ORDER BY id DESC LIMIT 1"
            ),
            {"id": user_id},
        ).scalar_one()
    assert left == "8"
    claims = jwt.decode(first.json()["access_token"], options={"verify_signature": False})
    assert "otp_at" not in claims


async def test_a_foreman_can_enable_2fa_voluntarily_and_then_must_use_it(
    api: AsyncClient, new_user: Callable[..., tuple[UUID, str]]
) -> None:
    _, email = new_user("foreman")
    access = (await login(api, email)).json()["access_token"]

    setup = await api.post(f"{AUTH}/mfa/setup", json={"password": PASSWORD}, headers=bearer(access))
    confirmed = await api.post(
        f"{AUTH}/mfa/confirm",
        json={"code": totp_code(setup.json()["secret"])},
        headers=bearer(access),
    )
    next_login = await login(api, email)

    assert confirmed.status_code == 200 and len(confirmed.json()["recovery_codes"]) == 10
    assert confirmed.json()["session"] is None
    assert next_login.json()["status"] == "mfa_required"


async def test_the_seeded_totp_secret_is_encrypted_with_the_user_bound_key(
    auth_world: AuthWorld, owner_sync_engine: Engine
) -> None:
    with owner_sync_engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE kut_auth"))
        rows: dict[str, Any] = {
            row.email.split("@")[0]: row
            for row in connection.execute(
                text(
                    'SELECT email, id, totp_secret_enc, totp_key_id FROM "user"'
                    " WHERE email IN ('owner@kut.test', 'finance@kut.test')"
                )
            )
        }

    owner = rows["owner"]
    assert decrypt_totp_secret(
        bytes(owner.totp_secret_enc), owner.totp_key_id, user_id=owner.id
    ) == (TOTP_SECRET)
    with pytest.raises(SecretDecryptionError):
        decrypt_totp_secret(
            bytes(owner.totp_secret_enc), owner.totp_key_id, user_id=auth_world.users["finance"]
        )
    assert TOTP_SECRET.encode() not in bytes(owner.totp_secret_enc)


async def secret_stored(engine: Engine, user_id: UUID) -> bool:
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE kut_auth"))
        value = connection.execute(
            text('SELECT totp_secret_enc FROM "user" WHERE id = :id'), {"id": user_id}
        ).scalar_one()
    return value is not None


async def test_setup_needs_the_current_password_on_both_paths(
    api: AsyncClient,
    new_user: Callable[..., tuple[UUID, str]],
    owner_sync_engine: Engine,
) -> None:
    accountant, accountant_email = new_user("accountant")
    foreman, foreman_email = new_user("foreman")
    enroll_token = (await login(api, accountant_email)).json()["mfa_token"]
    access = (await login(api, foreman_email)).json()["access_token"]

    no_password = await api.post(f"{AUTH}/mfa/setup", json={"mfa_token": enroll_token})
    wrong_enroll = await api.post(
        f"{AUTH}/mfa/setup", json={"mfa_token": enroll_token, "password": "not-the-password"}
    )
    wrong_access = await api.post(
        f"{AUTH}/mfa/setup", json={"password": "not-the-password"}, headers=bearer(access)
    )

    assert no_password.status_code == 422
    assert (wrong_enroll.status_code, wrong_access.status_code) == (401, 401)
    assert wrong_enroll.json() == wrong_access.json() == {"detail": "invalid_password"}
    assert not await secret_stored(owner_sync_engine, accountant)
    assert not await secret_stored(owner_sync_engine, foreman)  # украденный токен ключа не даёт
    right = await api.post(f"{AUTH}/mfa/setup", json={"password": PASSWORD}, headers=bearer(access))
    assert right.status_code == 200 and await secret_stored(owner_sync_engine, foreman)


async def test_guessing_the_password_through_setup_is_rate_limited(
    api: AsyncClient, new_user: Callable[..., tuple[UUID, str]]
) -> None:
    _, email = new_user("foreman")
    access = (await login(api, email)).json()["access_token"]

    codes = []
    for _ in range(6):
        response = await api.post(
            f"{AUTH}/mfa/setup", json={"password": "guess-guess-guess"}, headers=bearer(access)
        )
        codes.append(response.status_code)
    right = await api.post(f"{AUTH}/mfa/setup", json={"password": PASSWORD}, headers=bearer(access))

    assert codes == [401, 401, 401, 401, 401, 429]
    assert right.status_code == 429  # после пятой неудачи блокировка и для верного пароля
