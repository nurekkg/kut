import asyncio

from fastapi import FastAPI
from httpx import AsyncClient

from app.modules.health.router import get_checks
from app.modules.health.service import run_readiness


async def test_live_returns_ok_without_touching_dependencies(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["cache-control"] == "no-store"


async def test_ready_reports_all_dependencies_ok(client: AsyncClient) -> None:
    response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"db": "ok", "redis": "ok", "s3": "ok"}}


async def test_ready_returns_503_and_hides_error_details_when_a_check_fails(
    app: FastAPI, client: AsyncClient
) -> None:
    async def healthy() -> None:
        return None

    async def broken() -> None:
        raise ConnectionError("secret-host:6379 refused")

    app.dependency_overrides[get_checks] = lambda: {
        "db": healthy,
        "redis": broken,
        "s3": healthy,
    }
    response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "checks": {"db": "ok", "redis": "fail", "s3": "ok"},
    }
    assert "secret-host" not in response.text


async def test_ready_marks_a_slow_check_as_failed() -> None:
    async def slow() -> None:
        await asyncio.sleep(5)

    result = await run_readiness({"db": slow}, timeout_seconds=0.05)

    assert result.status == "degraded"
    assert result.checks == {"db": "fail"}
