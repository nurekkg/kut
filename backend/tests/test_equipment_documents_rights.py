"""Документы техники (PO 2026-10-17, R2 и R3): виды equipment_* подчиняются праву техники. Паспорт
видят владелец, финдиректор, техдир, начальник отдела, механик, сметчик, прораб, мастер и бухгалтер;
загружают техдир и начальник, механик - технический осмотр. Страховка и прочее - денежные: файл и
сумма только с equipment:read:rates. Проверяется через API; SQL-политики - в тестах видимости."""

from collections.abc import AsyncIterator
from uuid import UUID

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.engine import Engine

from app.core.redis import get_redis
from tests.auth_support import AuthWorld
from tests.documents_support import (
    API,
    PDF,
    PDF_OK,
    create_document,
    give_access,
    leave_no_access_behind,  # noqa: F401
    upload,
)
from tests.equipment_support import headers_for, le_east, new_unit

DOCS = f"{API}/documents"
READERS = (
    "techdir",
    "equipment_head",
    "mechanic",
    "owner",
    "finance",
    "estimator",
    "foreman",
    "master",
    "accountant",
)
NO_EQUIPMENT_DOCUMENTS = ("storekeeper", "procurement", "lawyer", "customer")
PEOPLE = (*READERS, *NO_EQUIPMENT_DOCUMENTS, "machinist")


@pytest.fixture
async def s3() -> httpx.AsyncClient:
    async with httpx.AsyncClient(timeout=10) as client:
        yield client


@pytest.fixture(autouse=True)
async def forget_the_granted_rights() -> AsyncIterator[None]:
    """Доступы теста отзываются мягко; снимки прав в кэше сбрасываем, чтобы соседние тесты не видели
    доступы этого теста."""
    yield
    await get_redis().flushdb()


async def world(
    api: AsyncClient, auth_world: AuthWorld, engine: Engine
) -> tuple[UUID, str, dict[str, dict[str, str]]]:
    """Юрлицо «Восток», единица техники и заголовки всех, кто участвует в проверке."""
    legal = le_east(auth_world)
    give_access(engine, [auth_world.users[name] for name in PEOPLE], (), legal)
    await get_redis().flushdb()  # снимок прав живёт до минуты: без сброса доступ не виден
    heads = {name: await headers_for(api, name) for name in PEOPLE}
    unit = await new_unit(api, heads["equipment_head"], auth_world, legal_entity=legal)
    return legal, unit["id"], heads


async def create_equipment_document(
    api: AsyncClient,
    headers: dict[str, str],
    legal: UUID,
    unit: str,
    kind: str,
) -> httpx.Response:
    return await create_document(
        api,
        headers,
        {
            "kind": kind,
            "legal_entity_id": str(legal),
            "title": "Документ техники",
            "links": [{"kind": "equipment_unit", "id": unit}],
        },
    )


async def create_ok(
    api: AsyncClient, headers: dict[str, str], legal: UUID, unit: str, kind: str
) -> str:
    response = await create_equipment_document(api, headers, legal, unit, kind)
    assert response.status_code == 201, response.text
    created: str = response.json()["id"]
    return created


async def test_the_passport_is_read_by_the_equipment_readers_and_hidden_from_the_rest(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    legal, unit, heads = await world(api, auth_world, worker_sync_engine)
    passport = await create_ok(api, heads["equipment_head"], legal, unit, "equipment_passport")

    for who in READERS:
        response = await api.get(f"{DOCS}/{passport}", headers=heads[who])
        assert response.status_code == 200, (who, response.text)
    for who in NO_EQUIPMENT_DOCUMENTS:
        response = await api.get(f"{DOCS}/{passport}", headers=heads[who])
        assert response.status_code == 404, (who, response.text)
    response = await api.get(f"{DOCS}/{passport}", headers=heads["machinist"])
    assert response.status_code == 403, response.text


async def test_only_the_equipment_writers_upload_passports_and_the_mechanic_uploads_inspections(
    api: AsyncClient,
    auth_world: AuthWorld,
    worker_sync_engine: Engine,
    s3: httpx.AsyncClient,
) -> None:
    legal, unit, heads = await world(api, auth_world, worker_sync_engine)
    for who in ("mechanic", "foreman"):
        refused = await create_equipment_document(
            api, heads[who], legal, unit, "equipment_passport"
        )
        assert refused.status_code == 403, (who, refused.text)
    refused = await create_equipment_document(
        api, heads["mechanic"], legal, unit, "equipment_insurance"
    )
    assert refused.status_code == 403, refused.text

    passport = await create_ok(api, heads["equipment_head"], legal, unit, "equipment_passport")
    blocked = await upload(api, s3, passport, heads["mechanic"], PDF_OK, PDF)
    assert blocked.status_code == 403, blocked.text
    uploaded = await upload(api, s3, passport, heads["equipment_head"], PDF_OK, PDF)
    assert uploaded.status_code == 201, uploaded.text

    inspection = await create_ok(
        api, heads["mechanic"], legal, unit, "equipment_technical_inspection"
    )
    stored = await upload(api, s3, inspection, heads["mechanic"], PDF_OK, PDF)
    assert stored.status_code == 201, stored.text


async def test_the_insurance_sum_and_file_are_for_the_rate_holders_only(
    api: AsyncClient,
    auth_world: AuthWorld,
    worker_sync_engine: Engine,
    s3: httpx.AsyncClient,
) -> None:
    legal, unit, heads = await world(api, auth_world, worker_sync_engine)
    insurance = await create_ok(api, heads["equipment_head"], legal, unit, "equipment_insurance")
    uploaded = await upload(
        api, s3, insurance, heads["equipment_head"], PDF_OK, PDF, amount="1500.00"
    )
    assert uploaded.status_code == 201, uploaded.text

    for who in ("equipment_head", "lawyer"):
        response = await api.get(f"{DOCS}/{insurance}", headers=heads[who])
        assert response.status_code == 200, (who, response.text)
        assert response.json()["amount"] == "1500.00", who
        assert response.json()["versions"][0]["amount"] == "1500.00", who
        link = await api.get(
            f"{DOCS}/{insurance}/versions/{uploaded.json()['id']}/download-url",
            headers=heads[who],
        )
        assert link.status_code == 200, (who, link.text)

    for who in ("foreman", "master", "mechanic"):
        response = await api.get(f"{DOCS}/{insurance}", headers=heads[who])
        assert response.status_code == 200, (who, response.text)
        assert response.json()["amount"] is None, who
        assert response.json()["versions"][0]["amount"] is None, who
        link = await api.get(
            f"{DOCS}/{insurance}/versions/{uploaded.json()['id']}/download-url",
            headers=heads[who],
        )
        assert link.status_code == 403, (who, link.text)

    for who in ("storekeeper", "procurement"):
        response = await api.get(f"{DOCS}/{insurance}", headers=heads[who])
        assert response.status_code == 404, (who, response.text)


async def test_the_inspection_of_the_mechanic_is_read_by_the_equipment_readers_only(
    api: AsyncClient,
    auth_world: AuthWorld,
    worker_sync_engine: Engine,
) -> None:
    legal, unit, heads = await world(api, auth_world, worker_sync_engine)
    inspection = await create_ok(
        api, heads["mechanic"], legal, unit, "equipment_technical_inspection"
    )

    for who in ("mechanic", "equipment_head", "foreman"):
        response = await api.get(f"{DOCS}/{inspection}", headers=heads[who])
        assert response.status_code == 200, (who, response.text)
    response = await api.get(f"{DOCS}/{inspection}", headers=heads["lawyer"])
    assert response.status_code == 404, response.text
