"""Сумма принадлежит версии (PO 2026-10-17, R1): правится только в черновике, новая сумма - новая
версия. Текущая сумма - у действующей версии (утверждённой или подписанной), а пока её нет - у
последней видимой. Проверяется через API и через SQL под kut_app."""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from app.core.context import use_context
from app.core.db import get_sessionmaker
from app.modules.auth import devseed
from tests.auth_support import AuthWorld
from tests.documents_support import (
    API,
    PDF,
    PDF_OK,
    Desk,
    approve,
    create_document,
    leave_no_access_behind,  # noqa: F401
    open_desk,
    upload,
)
from tests.equipment_support import in_context, rows


@pytest.fixture
async def s3() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=10) as client:
        yield client


async def ks2(api: AsyncClient, desk: Desk, auth_world: AuthWorld) -> dict[str, Any]:
    response = await create_document(
        api,
        desk.heads["estimator"],
        {
            "kind": "ks2",
            "legal_entity_id": str(desk.legal),
            "project_id": str(desk.project),
            "counterparty_id": str(auth_world.counterparties[devseed.CUSTOMER]),
            "title": "КС-2 с суммой",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def detail(api: AsyncClient, desk: Desk, who: str, document_id: str) -> dict[str, Any]:
    response = await api.get(f"{API}/documents/{document_id}", headers=desk.heads[who])
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_sum_is_edited_on_the_draft_and_a_later_sum_is_a_new_version(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, s3: httpx.AsyncClient
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, "estimator", "foreman")
    document = await ks2(api, desk, auth_world)
    first = await upload(
        api, s3, document["id"], desk.heads["estimator"], PDF_OK, PDF, amount="100.00"
    )
    assert first.status_code == 201, first.text
    version = first.json()["id"]
    draft = await detail(api, desk, "estimator", document["id"])
    assert (draft["amount"], draft["amount_version_no"], draft["effective_version_no"]) == (
        "100.00",
        1,
        None,
    )

    changed = await api.put(
        f"{API}/documents/{document['id']}/versions/{version}/amount",
        json={"amount": "150.00"},
        headers=desk.heads["estimator"],
    )
    assert changed.status_code == 204, changed.text
    assert (await detail(api, desk, "estimator", document["id"]))["amount"] == "150.00"

    approve(worker_sync_engine, version)
    approved = await detail(api, desk, "estimator", document["id"])
    assert (approved["amount"], approved["effective_version_no"]) == ("150.00", 1)
    locked = await api.put(
        f"{API}/documents/{document['id']}/versions/{version}/amount",
        json={"amount": "1.00"},
        headers=desk.heads["estimator"],
    )
    assert locked.status_code == 409, locked.text
    assert locked.json()["detail"] == "document.version_not_draft"
    assert (await detail(api, desk, "estimator", document["id"]))["amount"] == "150.00"

    second = await upload(
        api, s3, document["id"], desk.heads["estimator"], PDF_OK, PDF, amount="300.00"
    )
    assert second.status_code == 201, second.text
    newer = await detail(api, desk, "estimator", document["id"])
    assert (
        newer["amount"],
        newer["amount_version_no"],
        newer["effective_version_no"],
        newer["latest_version_no"],
    ) == ("150.00", 1, 1, 2)
    assert [item["amount"] for item in newer["versions"]] == ["150.00", "300.00"]
    foreman = await detail(api, desk, "foreman", document["id"])
    assert foreman["amount"] is None
    assert [item["amount"] for item in foreman["versions"]] == [None, None]


async def test_the_application_cannot_change_a_sum_once_its_version_has_left_the_draft(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, s3: httpx.AsyncClient
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, "estimator")
    document = await ks2(api, desk, auth_world)
    first = await upload(
        api, s3, document["id"], desk.heads["estimator"], PDF_OK, PDF, amount="100.00"
    )
    version = first.json()["id"]
    approve(worker_sync_engine, version)

    context = in_context(auth_world.users["estimator"], (desk.project,), (desk.legal,))
    with use_context(context):
        async with get_sessionmaker()() as session:
            with pytest.raises(DBAPIError, match=r"amount\.version_not_draft"):
                await session.execute(
                    text(
                        "UPDATE document_version_amount SET amount = 1"
                        " WHERE version_id = CAST(:v AS uuid)"
                    ),
                    {"v": version},
                )
            await session.rollback()


async def test_a_writer_without_the_money_right_cannot_insert_a_sum_row_directly(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, s3: httpx.AsyncClient
) -> None:
    # Прораб пишет в документ, но денег не видит: строку суммы не вставит и SQL мимо сервиса.
    # Вставку проверяет политика вставки и гард версии; ответ PUT этого не показывает.
    desk = await open_desk(api, auth_world, worker_sync_engine, "estimator", "foreman")
    document = await ks2(api, desk, auth_world)
    uploaded = await upload(api, s3, document["id"], desk.heads["estimator"], PDF_OK, PDF)
    assert uploaded.status_code == 201, uploaded.text
    version = uploaded.json()["id"]

    context = in_context(auth_world.users["foreman"], (desk.project,), (desk.legal,))
    may_write, money = (
        await rows(
            "SELECT app.document_writable(CAST(:d AS uuid)),"
            " app.version_money_right(CAST(:v AS uuid))",
            context,
            {"d": document["id"], "v": version},
        )
    )[0]
    assert (may_write, money) == (True, False)
    with use_context(context):
        async with get_sessionmaker()() as session:
            with pytest.raises(DBAPIError, match=r"document\.forbidden|row-level security"):
                await session.execute(
                    text(
                        "INSERT INTO document_version_amount"
                        " (version_id, document_id, legal_entity_id, project_id, amount)"
                        " VALUES (CAST(:v AS uuid), CAST(:d AS uuid), CAST(:le AS uuid),"
                        " CAST(:p AS uuid), 1)"
                    ),
                    {
                        "v": version,
                        "d": document["id"],
                        "le": str(desk.legal),
                        "p": str(desk.project),
                    },
                )
            await session.rollback()
    with worker_sync_engine.connect() as connection:
        stored = connection.execute(
            text(
                "SELECT count(*) FROM document_version_amount WHERE version_id = CAST(:v AS uuid)"
            ),
            {"v": version},
        ).scalar_one()
    assert stored == 0


async def test_the_current_sum_of_an_unapproved_document_is_the_last_visible_version(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, s3: httpx.AsyncClient
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, "estimator")
    document = await ks2(api, desk, auth_world)
    await upload(api, s3, document["id"], desk.heads["estimator"], PDF_OK, PDF, amount="10.00")
    await upload(api, s3, document["id"], desk.heads["estimator"], PDF_OK, PDF, amount="20.00")

    listed = await detail(api, desk, "estimator", document["id"])

    assert (listed["amount"], listed["amount_version_no"], listed["effective_version_no"]) == (
        "20.00",
        2,
        None,
    )
    assert [item["amount"] for item in listed["versions"]] == ["10.00", "20.00"]


async def test_an_archived_document_keeps_its_draft_sum(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, s3: httpx.AsyncClient
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, "estimator", "owner")
    document = await ks2(api, desk, auth_world)
    uploaded = await upload(
        api, s3, document["id"], desk.heads["estimator"], PDF_OK, PDF, amount="7.00"
    )
    archived = await api.post(
        f"{API}/documents/{document['id']}/archive", headers=desk.heads["owner"]
    )
    assert archived.status_code == 204, archived.text

    changed = await api.put(
        f"{API}/documents/{document['id']}/versions/{uploaded.json()['id']}/amount",
        json={"amount": "8.00"},
        headers=desk.heads["estimator"],
    )

    assert changed.status_code == 409, changed.text
    assert changed.json()["detail"] == "document.archived"
    # Сумма черновика не изменилась: отказ стоит до записи.
    kept = await api.get(f"{API}/documents/{document['id']}", headers=desk.heads["owner"])
    assert kept.json()["versions"][0]["amount"] == "7.00"


async def test_a_version_without_a_sum_has_no_sum_number(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, s3: httpx.AsyncClient
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, "estimator", "foreman")
    document = await ks2(api, desk, auth_world)
    await upload(api, s3, document["id"], desk.heads["estimator"], PDF_OK, PDF)

    listed = await detail(api, desk, "foreman", document["id"])

    assert (listed["amount"], listed["amount_version_no"]) == (None, None)
