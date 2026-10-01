import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import pyotp
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

PASSWORD = "correct-horse-battery-1"
NEW_PASSWORD = "another-long-password-2"
# Общеизвестный пример из документации pyotp: в тестах секрет не защищает ничего.
TOTP_SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
AUTH = "/api/v1/auth"


@dataclass(frozen=True)
class AuthWorld:
    """Демо-данные devseed в тестовой БД: пользователи по логину (owner, foreman, sub_a, ...)."""

    users: dict[str, UUID]
    projects: dict[str, UUID]  # east / west
    counterparties: dict[str, UUID]
    legal_entities: dict[str, UUID]
    roles: dict[str, UUID]

    @staticmethod
    def email(who: str) -> str:
        return who if "@" in who else f"{who}@kut.test"


def totp_code(secret: str = TOTP_SECRET, *, step_offset: int = 0) -> str:
    """Код TOTP; сервер принимает шаги -1, 0 и +1, поэтому до трёх разных кодов подряд."""
    return pyotp.TOTP(secret).at(time.time() + 30 * step_offset)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def login(client: AsyncClient, who: str, password: str = PASSWORD) -> Response:
    return await client.post(
        f"{AUTH}/login", json={"identifier": AuthWorld.email(who), "password": password}
    )


async def sign_in(client: AsyncClient, who: str, *, step_offset: int = 0) -> dict[str, Any]:
    """Полный вход (с TOTP, если он настроен): тело ответа с access_token."""
    response = await login(client, who)
    body: dict[str, Any] = response.json()
    if response.status_code == 200 and body.get("status") == "mfa_required":
        response = await client.post(
            f"{AUTH}/mfa/verify",
            json={"mfa_token": body["mfa_token"], "code": totp_code(step_offset=step_offset)},
        )
        body = response.json()
    assert response.status_code == 200, (who, response.status_code, body)
    assert body["status"] == "ok", body
    return body


async def token_for(client: AsyncClient, who: str, *, step_offset: int = 0) -> str:
    return str((await sign_in(client, who, step_offset=step_offset))["access_token"])


def client_from(app: FastAPI, ip: str) -> AsyncClient:
    """Клиент с заданным адресом: лимит попыток считается по адресу, поэтому тесты разводят их."""
    return AsyncClient(
        transport=ASGITransport(app=app, client=(ip, 40000)), base_url="https://test"
    )
