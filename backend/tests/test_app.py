import secrets
from collections.abc import Iterator

import pytest
from fastapi import APIRouter
from httpx import ASGITransport, AsyncClient

from app.api import API_V1_PREFIX, api_v1_router
from app.core.config import get_settings
from app.main import create_app
from app.modules.auth.keys import get_jwt_keys, get_totp_keys

DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")


# Ключ без префикса «dev», чтобы API стартовал с APP_ENV=production. Случайный при каждом запуске:
# в репозитории ключа нет.
PRODUCTION_LIKE_KEY = secrets.token_urlsafe(32)


def clear_auth_caches() -> None:
    get_settings.cache_clear()
    get_jwt_keys.cache_clear()
    get_totp_keys.cache_clear()


@pytest.fixture
def production_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_PRIVATE_KEYS", f"prod-1:{PRODUCTION_LIKE_KEY}")
    monkeypatch.setenv("JWT_ACTIVE_KID", "prod-1")
    monkeypatch.setenv("TOTP_ENCRYPTION_KEYS", f"prod-1:{PRODUCTION_LIKE_KEY}")
    monkeypatch.setenv("TOTP_ACTIVE_KEY_ID", "prod-1")
    clear_auth_caches()
    yield
    monkeypatch.undo()
    clear_auth_caches()


@pytest.mark.parametrize("path", DOCS_PATHS)
async def test_docs_are_served_outside_production(client: AsyncClient, path: str) -> None:
    response = await client.get(path)

    assert response.status_code == 200


@pytest.mark.parametrize("path", DOCS_PATHS)
async def test_docs_are_not_served_in_production(production_env: None, path: str) -> None:
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        docs = await client.get(path)
        health = await client.get("/health")

    assert docs.status_code == 404
    assert health.status_code == 200


async def test_routes_on_the_v1_router_are_served_only_under_the_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = APIRouter()

    @probe.get("/probe")
    async def probe_endpoint() -> dict[str, str]:
        return {"probe": "ok"}

    monkeypatch.setattr(api_v1_router, "routes", list(api_v1_router.routes))
    api_v1_router.include_router(probe)
    app = create_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        versioned = await client.get(f"{API_V1_PREFIX}/probe")
        unversioned = await client.get("/probe")
        health = await client.get("/health/ready")

    assert API_V1_PREFIX == "/api/v1"
    assert versioned.status_code == 200
    assert versioned.json() == {"probe": "ok"}
    assert unversioned.status_code == 404
    assert health.status_code != 404
