from collections.abc import Iterator

import pytest
from fastapi import APIRouter
from httpx import ASGITransport, AsyncClient

from app.api import API_V1_PREFIX, api_v1_router
from app.core.config import get_settings
from app.main import create_app

DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")


@pytest.fixture
def production_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    yield
    monkeypatch.undo()
    get_settings.cache_clear()


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
