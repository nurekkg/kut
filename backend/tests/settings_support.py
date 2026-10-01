from typing import Any
from uuid import UUID, uuid4

from httpx import AsyncClient, Response

from tests.auth_support import AUTH, PASSWORD, AuthWorld, bearer

S = "/api/v1/settings"
CFG_DOMAIN = (
    "cfg.test"  # адреса созданных тестами пользователей: фикстура settings_cleanup гасит их
)


def unique(prefix: str) -> str:
    return f"{prefix}{uuid4().hex[:8]}"


async def call(
    api: AsyncClient,
    method: str,
    path: str,
    token: str,
    *,
    json: Any = None,
    key: UUID | None = None,
    params: dict[str, Any] | None = None,
) -> Response:
    headers = bearer(token)
    if key is not None:
        headers["Idempotency-Key"] = str(key)
    return await api.request(method, f"{S}{path}", headers=headers, json=json, params=params)


async def post_new(api: AsyncClient, path: str, token: str, body: dict[str, Any]) -> Response:
    """Создание под свежим Idempotency-Key."""
    return await call(api, "POST", path, token, json=body, key=uuid4())


async def create_user(
    api: AsyncClient,
    token: str,
    world: AuthWorld,
    role: str = "foreman",
    *,
    counterparty_id: UUID | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    body = {
        "full_name": "Сотрудник (cfg)",
        "email": f"{unique('cfg-')}@{CFG_DOMAIN}",
        "role_id": str(world.roles[role]),
        "counterparty_id": str(counterparty_id) if counterparty_id else None,
        **overrides,
    }
    response = await post_new(api, "/users", token, body)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


async def activate(
    api: AsyncClient, token: str, user_id: str | UUID, password: str = PASSWORD
) -> str:
    """Ссылка активации администратора и пароль по ней; возвращает токен ссылки."""
    invite = await call(api, "POST", f"/users/{user_id}/invite", token)
    assert invite.status_code == 200, invite.text
    link = str(invite.json()["path"]).split("token=")[1]
    done = await api.post(f"{AUTH}/activate", json={"token": link, "password": password})
    assert done.status_code == 204, done.text
    return link


async def new_active_user(
    api: AsyncClient,
    token: str,
    world: AuthWorld,
    role: str = "foreman",
    **kwargs: Any,
) -> tuple[str, str]:
    """(id, email) пользователя с паролем PASSWORD, заведённого через настройки."""
    created = await create_user(api, token, world, role, **kwargs)
    await activate(api, token, created["id"])
    return str(created["id"]), str(created["email"])
