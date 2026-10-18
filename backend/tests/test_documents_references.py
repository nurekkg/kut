"""Справочные поля реестра (S5b, хвосты): контрагенты для выбора - только id и название, внутренним
с правом на документы; название юрлица - внутренним; фильтр по узлу объекта (ADR-015 часть 3,
раздел 11). Внешним связи документов не отдаются."""

from typing import Any
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.core.context import RequestContext, use_context
from app.core.db import get_portal_sessionmaker
from app.modules.auth import devseed
from app.modules.documents.service import list_counterparties
from tests.auth_support import AuthWorld
from tests.documents_support import (
    API,
    Desk,
    create_document,
    leave_no_access_behind,  # noqa: F401
    open_desk,
)
from tests.equipment_support import headers_for


async def create(api: AsyncClient, desk: Desk, who: str, body: dict[str, Any]) -> dict[str, Any]:
    response = await create_document(
        api,
        desk.heads[who],
        {
            "legal_entity_id": str(desk.legal),
            "project_id": str(desk.project),
            "title": "Справочные поля",
            **body,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def listing(api: AsyncClient, headers: dict[str, str], **params: str) -> list[dict[str, Any]]:
    response = await api.get(f"{API}/documents", params=params, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def test_the_counterparty_search_gives_only_id_and_name_to_internal_readers(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    sub_a = auth_world.counterparties[devseed.SUB_A]
    with worker_sync_engine.connect() as connection:
        name = connection.scalar(
            text("SELECT name FROM counterparty WHERE id = :id"), {"id": sub_a}
        )

    response = await api.get(
        f"{API}/documents/counterparties",
        params={"q": name},
        headers=await headers_for(api, "estimator"),
    )

    assert response.status_code == 200, response.text
    found = response.json()
    assert {"id": str(sub_a), "name": name} in found
    assert all(set(item) == {"id", "name"} for item in found)
    for who in ("customer", "mechanic"):
        refused = await api.get(
            f"{API}/documents/counterparties", headers=await headers_for(api, who)
        )
        assert refused.status_code == 403, (who, refused.text)


async def test_a_deleted_counterparty_is_not_offered_for_selection(
    api: AsyncClient, worker_sync_engine: Engine
) -> None:
    name = f"Удалённый {uuid4()}"
    with worker_sync_engine.begin() as connection:
        identifier = connection.scalar(
            text(
                "INSERT INTO counterparty (name, types) VALUES (:n, ARRAY['supplier']::varchar[])"
                " RETURNING id"
            ),
            {"n": name},
        )
        connection.execute(
            text("UPDATE counterparty SET deleted_at = now() WHERE id = :id"), {"id": identifier}
        )

    response = await api.get(
        f"{API}/documents/counterparties",
        params={"q": name},
        headers=await headers_for(api, "estimator"),
    )

    assert response.status_code == 200, response.text
    assert response.json() == []


async def test_a_portal_user_lists_only_its_own_counterparty_from_the_service(
    auth_world: AuthWorld,
) -> None:
    own = auth_world.counterparties[devseed.SUB_A]
    context = RequestContext(
        user_id=auth_world.users["sub_a"],
        is_internal=False,
        project_ids=frozenset(),
        legal_entity_ids=frozenset(),
        counterparty_id=own,
        request_id=None,
        sees_all_users=False,
    )
    with use_context(context):
        async with get_portal_sessionmaker()() as session:
            listed = await list_counterparties(session, None)
    assert [item.id for item in listed] == [own]


async def test_the_registry_names_the_legal_entity_for_internal_readers(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, "estimator")
    document = await create(api, desk, "estimator", {"kind": "estimate_local"})
    with worker_sync_engine.connect() as connection:
        name = connection.scalar(
            text("SELECT name FROM legal_entity WHERE id = :id"), {"id": desk.legal}
        )

    response = await api.get(f"{API}/documents/{document['id']}", headers=desk.heads["estimator"])

    assert response.status_code == 200, response.text
    assert response.json()["legal_entity_name"] == name


async def test_the_registry_filters_by_project_node_and_outside_roles_get_nothing(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    desk = await open_desk(api, auth_world, worker_sync_engine, "estimator", "foreman")
    with worker_sync_engine.begin() as connection:
        node = connection.scalar(
            text(
                "INSERT INTO project_node (project_id, legal_entity_id, type, name)"
                " VALUES (:p, :le, 'building', :n) RETURNING id"
            ),
            {"p": desk.project, "le": desk.legal, "n": f"Корпус {uuid4()}"},
        )
    linked = await create(
        api,
        desk,
        "estimator",
        {"kind": "material_request", "links": [{"kind": "project_node", "id": str(node)}]},
    )
    await create(api, desk, "estimator", {"kind": "estimate_local"})

    listed = await listing(api, desk.heads["foreman"], project_node_id=str(node))

    assert [item["id"] for item in listed] == [linked["id"]]
    customer = await headers_for(api, "customer")
    assert await listing(api, customer, project_node_id=str(node)) == []
