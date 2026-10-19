"""Аудит документов (C1, шаг 0 S6a): строки таблиц документов видны по праву на документ.
Ключ файла и хэш версии в аудит не попадают. Проверяется SQL-ом под kut_app и через роль БД."""

from collections.abc import AsyncIterator

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.core.redis import get_redis
from tests.auth_support import AuthWorld
from tests.documents_support import (
    PDF,
    PDF_OK,
    create_document,
    give_access,
    leave_no_access_behind,  # noqa: F401
    open_desk,
    upload,
)
from tests.equipment_support import headers_for, in_context, le_east, new_unit, rows


@pytest.fixture
async def s3() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=10) as client:
        yield client


@pytest.fixture(autouse=True)
async def forget_the_granted_rights() -> AsyncIterator[None]:
    """Доступы теста отзываются мягко; кэш прав сбрасываем, чтобы соседние тесты их не видели."""
    yield
    await get_redis().flushdb()


async def test_a_user_without_the_document_sees_no_audit_rows_of_it(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    """Кладовщик видит юрлицо, но не паспорт техники: строки аудита документа ему закрыты."""
    legal = le_east(auth_world)
    give_access(worker_sync_engine, [auth_world.users["storekeeper"]], (), legal)
    head = await headers_for(api, "equipment_head")
    unit = await new_unit(api, head, auth_world, legal_entity=legal)
    created = await create_document(
        api,
        head,
        {
            "kind": "equipment_passport",
            "legal_entity_id": str(legal),
            "title": "Паспорт для аудита",
            "links": [{"kind": "equipment_unit", "id": unit["id"]}],
        },
    )
    assert created.status_code == 201, created.text
    document = created.json()["id"]

    sql = (
        "SELECT count(*) FROM audit_log WHERE table_name = 'document' AND row_id = CAST(:d AS uuid)"
    )
    [(for_head,)] = await rows(
        sql, in_context(auth_world.users["equipment_head"], (), (legal,)), {"d": document}
    )
    [(for_storekeeper,)] = await rows(
        sql, in_context(auth_world.users["storekeeper"], (), (legal,)), {"d": document}
    )
    assert for_head >= 1
    assert for_storekeeper == 0


async def test_the_file_key_and_the_checksum_are_not_in_the_audit(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, s3: httpx.AsyncClient
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, "estimator")
    created = await create_document(
        api,
        desk.heads["estimator"],
        {
            "kind": "commissioning_act",
            "legal_entity_id": str(desk.legal),
            "project_id": str(desk.project),
            "title": "Акт для аудита",
        },
    )
    assert created.status_code == 201, created.text
    uploaded = await upload(api, s3, created.json()["id"], desk.heads["estimator"], PDF_OK, PDF)
    assert uploaded.status_code == 201, uploaded.text
    version = uploaded.json()["id"]

    with worker_sync_engine.connect() as connection:
        inserted = connection.execute(
            text(
                "SELECT count(*) FROM audit_log WHERE table_name = 'document_version'"
                " AND row_id = CAST(:v AS uuid)"
            ),
            {"v": version},
        ).scalar_one()
        leaked = connection.execute(
            text(
                "SELECT count(*) FROM audit_log WHERE table_name = 'document_version'"
                " AND row_id = CAST(:v AS uuid)"
                " AND (COALESCE(new, old) ->> 's3_key' IS NOT NULL"
                " OR COALESCE(new, old) ->> 'checksum_sha256' IS NOT NULL)"
            ),
            {"v": version},
        ).scalar_one()
    assert inserted >= 1
    assert leaked == 0
