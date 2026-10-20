"""Комментарии документов под деньгами (PO 2026-10-18, раунд R1-R4, шаг 1).

Комментарий версии и комментарий события журнала статусов видят только с правом на деньги вида:
для финансового документа - documents:read:financial, для денежных видов техники -
equipment:read:rates, для нефинансового - право на сам документ. Колонка comment у kut_app не
читается напрямую: через функции app.version_comment и app.event_comment. Журнал аудита отдаёт
строку с комментарием по тому же праву. Размер, хэш, автор и дата видны по праву на документ.
"""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from app.modules.auth import devseed
from tests.auth_support import AuthWorld
from tests.documents_support import (
    API,
    PDF,
    PDF_OK,
    Desk,
    create_document,
    leave_no_access_behind,  # noqa: F401
    open_desk,
    upload,
)
from tests.equipment_support import in_context, rows

COMMENT = "Смета на согласование"


@pytest.fixture
async def s3() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=10) as client:
        yield client


async def financial_document(api: AsyncClient, desk: Desk, auth_world: AuthWorld) -> dict[str, Any]:
    response = await create_document(
        api,
        desk.heads["estimator"],
        {
            "kind": "ks2",
            "legal_entity_id": str(desk.legal),
            "project_id": str(desk.project),
            "counterparty_id": str(auth_world.counterparties[devseed.CUSTOMER]),
            "title": "КС-2 с комментарием",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def comment_as(
    auth_world: AuthWorld, desk: Desk, who: str, version: str, sql: str
) -> list[tuple[Any, ...]]:
    context = in_context(auth_world.users[who], (desk.project,), (desk.legal,))
    return await rows(sql, context, {"v": version})


def transit(owner_engine: Engine, version: str, target: str, comment: str | None) -> None:
    with owner_engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE kut_auth"))
        connection.execute(
            text("SELECT app.version_move(CAST(:v AS uuid), :to, :comment, NULL)"),
            {"v": version, "to": target, "comment": comment},
        )


async def test_the_foreman_gets_no_comment_of_a_financial_version(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, s3: httpx.AsyncClient
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, "estimator", "foreman")
    document = await financial_document(api, desk, auth_world)
    uploaded = await upload(
        api, s3, document["id"], desk.heads["estimator"], PDF_OK, PDF, comment=COMMENT
    )
    assert uploaded.status_code == 201, uploaded.text
    version = uploaded.json()["id"]

    seen = (
        await api.get(f"{API}/documents/{document['id']}", headers=desk.heads["foreman"])
    ).json()
    assert seen["versions"][0]["comment"] is None
    assert len(seen["versions"][0]["checksum_sha256"]) == 64  # хэш виден по праву на документ
    own = (
        await api.get(f"{API}/documents/{document['id']}", headers=desk.heads["estimator"])
    ).json()
    assert own["versions"][0]["comment"] == COMMENT

    sql = "SELECT app.version_comment(CAST(:v AS uuid))"
    assert await comment_as(auth_world, desk, "foreman", version, sql) == [(None,)]
    assert await comment_as(auth_world, desk, "estimator", version, sql) == [(COMMENT,)]


async def test_the_foreman_reads_the_comment_of_a_non_financial_document(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, s3: httpx.AsyncClient
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, "estimator", "foreman")
    created = await create_document(
        api,
        desk.heads["estimator"],
        {
            "kind": "commissioning_act",
            "legal_entity_id": str(desk.legal),
            "project_id": str(desk.project),
            "title": "Акт ввода с комментарием",
        },
    )
    assert created.status_code == 201, created.text
    document = created.json()
    uploaded = await upload(
        api, s3, document["id"], desk.heads["estimator"], PDF_OK, PDF, comment=COMMENT
    )
    assert uploaded.status_code == 201, uploaded.text
    version = uploaded.json()["id"]

    seen = (
        await api.get(f"{API}/documents/{document['id']}", headers=desk.heads["foreman"])
    ).json()
    assert seen["versions"][0]["comment"] == COMMENT
    sql = "SELECT app.version_comment(CAST(:v AS uuid))"
    assert await comment_as(auth_world, desk, "foreman", version, sql) == [(COMMENT,)]


async def test_the_status_journal_shows_statuses_to_the_foreman_and_hides_the_comments(
    api: AsyncClient,
    auth_world: AuthWorld,
    worker_sync_engine: Engine,
    owner_sync_engine: Engine,
    s3: httpx.AsyncClient,
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, "estimator", "foreman")
    document = await financial_document(api, desk, auth_world)
    uploaded = await upload(api, s3, document["id"], desk.heads["estimator"], PDF_OK, PDF)
    assert uploaded.status_code == 201, uploaded.text
    version = uploaded.json()["id"]
    transit(owner_sync_engine, version, "on_approval", "Передано на согласование")
    transit(owner_sync_engine, version, "approved", None)

    events = (
        await api.get(f"{API}/documents/{document['id']}/events", headers=desk.heads["foreman"])
    ).json()
    assert {item["to_status"] for item in events} >= {"on_approval", "approved"}
    assert all(item["comment"] is None for item in events)
    mine = (
        await api.get(f"{API}/documents/{document['id']}/events", headers=desk.heads["estimator"])
    ).json()
    assert "Передано на согласование" in [item["comment"] for item in mine]


async def test_the_audit_journal_hides_the_comment_row_from_the_foreman(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, s3: httpx.AsyncClient
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, "estimator", "foreman")
    document = await financial_document(api, desk, auth_world)
    uploaded = await upload(
        api, s3, document["id"], desk.heads["estimator"], PDF_OK, PDF, comment=COMMENT
    )
    assert uploaded.status_code == 201, uploaded.text
    version = uploaded.json()["id"]

    sql = (
        "SELECT count(*) FROM audit_log WHERE table_name = 'document_version'"
        " AND row_id = CAST(:v AS uuid) AND COALESCE(new, old) ->> 'comment' IS NOT NULL"
    )
    assert await comment_as(auth_world, desk, "foreman", version, sql) == [(0,)]
    [(seen_by_estimator,)] = await comment_as(auth_world, desk, "estimator", version, sql)
    assert seen_by_estimator >= 1


async def test_kut_app_reads_the_comment_only_through_the_functions(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    context = in_context(auth_world.users["estimator"], (), ())
    with pytest.raises(DBAPIError, match="permission denied"):
        await rows("SELECT comment FROM document_version LIMIT 1", context)
    with pytest.raises(DBAPIError, match="permission denied"):
        await rows("SELECT comment FROM document_status_event LIMIT 1", context)

    with worker_sync_engine.connect() as connection:
        for table in ("document_version", "document_status_event"):
            privileges = dict(
                connection.execute(
                    text(
                        "SELECT column_name, has_column_privilege('kut_app', :table, column_name,"
                        " 'SELECT') FROM information_schema.columns"
                        " WHERE table_schema = 'public' AND table_name = :table"
                    ),
                    {"table": table},
                ).all()
            )
            assert privileges["comment"] is False, table
            assert all(value for name, value in privileges.items() if name != "comment"), table
