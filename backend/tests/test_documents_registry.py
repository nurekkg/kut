"""Реестр документов S5b (ADR-015 часть 3, раздел 11): фильтры, последняя видимая версия, журнал
статусов и имя автора версии. Журнал и имена - внутренним; заказчик видит только утверждённое."""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.modules.auth import devseed
from tests.auth_support import AuthWorld
from tests.documents_support import (
    PDF,
    PDF_OK,
    Desk,
    approve,
    create_document,
    leave_no_access_behind,  # noqa: F401
    open_desk,
    upload,
)

API = "/api/v1"


@pytest.fixture
async def s3() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=10) as client:
        yield client


async def create(api: AsyncClient, desk: Desk, who: str, body: dict[str, Any]) -> dict[str, Any]:
    response = await create_document(
        api,
        desk.heads[who],
        {
            "legal_entity_id": str(desk.legal),
            "project_id": str(desk.project),
            "title": "Тест реестра",
            **body,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def listing(api: AsyncClient, headers: dict[str, str], **params: str) -> list[dict[str, Any]]:
    response = await api.get(f"{API}/documents", params=params, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def test_the_registry_filters_by_status_counterparty_number_and_period(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, s3: httpx.AsyncClient
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, "estimator", "foreman")
    customer = auth_world.counterparties[devseed.CUSTOMER]
    signed = await create(api, desk, "estimator", {"kind": "ks2", "counterparty_id": str(customer)})
    uploaded = await upload(api, s3, signed["id"], desk.heads["estimator"], PDF_OK, PDF)
    assert uploaded.status_code == 201, uploaded.text
    approve(worker_sync_engine, uploaded.json()["id"])
    draft = await create(api, desk, "estimator", {"kind": "estimate_local"})
    drafted = await upload(api, s3, draft["id"], desk.heads["estimator"], PDF_OK, PDF)
    assert drafted.status_code == 201, drafted.text

    headers = desk.heads["foreman"]
    project = str(desk.project)
    everything = await listing(api, headers, project_id=project)
    assert {item["id"] for item in everything} == {signed["id"], draft["id"]}
    approved = await listing(api, headers, project_id=project, status="approved")
    assert [item["id"] for item in approved] == [signed["id"]]
    drafts = await listing(api, headers, project_id=project, status="draft")
    assert [item["id"] for item in drafts] == [draft["id"]]
    by_customer = await listing(api, headers, project_id=project, counterparty_id=str(customer))
    assert [item["id"] for item in by_customer] == [signed["id"]]
    by_number = await listing(api, headers, project_id=project, q=signed["registration_no"])
    assert [item["id"] for item in by_number] == [signed["id"]]
    assert len(await listing(api, headers, project_id=project, date_from="2000-01-01")) == 2
    assert await listing(api, headers, project_id=project, date_from="2100-01-01") == []

    row = next(item for item in everything if item["id"] == signed["id"])
    assert (row["latest_status"], row["latest_version_no"]) == ("approved", 1)
    assert row["counterparty_name"]
    draft_row = next(item for item in everything if item["id"] == draft["id"])
    assert draft_row["latest_status"] == "draft"


async def test_the_customer_sees_only_the_approved_version_and_no_author_or_journal(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, s3: httpx.AsyncClient
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, "estimator", "customer")
    customer = auth_world.counterparties[devseed.CUSTOMER]
    approved = await create(
        api, desk, "estimator", {"kind": "ks2", "counterparty_id": str(customer)}
    )
    uploaded = await upload(api, s3, approved["id"], desk.heads["estimator"], PDF_OK, PDF)
    assert uploaded.status_code == 201, uploaded.text
    approve(worker_sync_engine, uploaded.json()["id"])
    hidden = await create(api, desk, "estimator", {"kind": "ks2", "counterparty_id": str(customer)})
    await upload(api, s3, hidden["id"], desk.heads["estimator"], PDF_OK, PDF)

    headers = desk.heads["customer"]
    seen = await listing(api, headers, project_id=str(desk.project))
    assert [item["id"] for item in seen] == [approved["id"]]
    assert seen[0]["latest_status"] == "approved"
    assert seen[0]["registration_no"] is None
    detail = await api.get(f"{API}/documents/{approved['id']}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert all(version["author_name"] is None for version in detail.json()["versions"])
    journal = await api.get(f"{API}/documents/{approved['id']}/events", headers=headers)
    assert journal.status_code == 403


async def test_the_journal_of_statuses_is_for_internal_users_only(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, s3: httpx.AsyncClient
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, "estimator", "foreman", "customer")
    customer = auth_world.counterparties[devseed.CUSTOMER]
    doc = await create(api, desk, "estimator", {"kind": "ks2", "counterparty_id": str(customer)})
    uploaded = await upload(api, s3, doc["id"], desk.heads["estimator"], PDF_OK, PDF)
    assert uploaded.status_code == 201, uploaded.text
    approve(worker_sync_engine, uploaded.json()["id"])

    journal = await api.get(f"{API}/documents/{doc['id']}/events", headers=desk.heads["foreman"])
    assert journal.status_code == 200, journal.text
    events = journal.json()
    assert [event["to_status"] for event in events][-2:] == ["on_approval", "approved"]
    assert {event["version_no"] for event in events} == {1}
    refused = await api.get(f"{API}/documents/{doc['id']}/events", headers=desk.heads["customer"])
    assert refused.status_code == 403


async def test_the_author_of_a_version_is_named_for_internal_users(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, s3: httpx.AsyncClient
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, "estimator", "foreman")
    doc = await create(api, desk, "estimator", {"kind": "estimate_local"})
    uploaded = await upload(api, s3, doc["id"], desk.heads["estimator"], PDF_OK, PDF)
    assert uploaded.status_code == 201, uploaded.text
    with worker_sync_engine.connect() as connection:
        name = connection.execute(
            text('SELECT full_name FROM "user" WHERE id = :user'),
            {"user": auth_world.users["estimator"]},
        ).scalar_one()

    assert uploaded.json()["author_name"] == name
    detail = await api.get(f"{API}/documents/{doc['id']}", headers=desk.heads["foreman"])
    assert detail.json()["versions"][0]["author_name"] == name
