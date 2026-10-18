"""Связь строки срока техники с документом S5a (ADR-015 часть 3, Q8): документ виден вызывающему,
того же вида, что строка срока, и связан с этой единицей техники."""

from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy.engine import Engine

from tests.auth_support import AuthWorld
from tests.documents_support import API as DOCS
from tests.documents_support import (
    create_document,
    give_access,
    leave_no_access_behind,  # noqa: F401
)
from tests.equipment_support import API, headers_for, idem, le_east, new_unit

DOCUMENT_KIND = "equipment_permit"


async def legal_document(
    api: AsyncClient,
    techdir: dict[str, str],
    legal: str,
    unit: str,
    *,
    kind: str = DOCUMENT_KIND,
) -> dict[str, str]:
    body = {
        "kind": kind,
        "legal_entity_id": legal,
        "title": "Разрешение",
        "links": [{"kind": "equipment_unit", "id": unit}],
    }
    response = await create_document(api, techdir, body)
    assert response.status_code == 201, response.text
    return response.json()


async def test_a_linked_document_of_the_same_kind_is_attached_to_the_row(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    legal = str(le_east(auth_world))
    give_access(worker_sync_engine, [auth_world.users["techdir"]], (), le_east(auth_world))
    techdir = await headers_for(api, "techdir")
    head = await headers_for(api, "equipment_head")
    unit = await new_unit(api, head, auth_world, legal_entity=le_east(auth_world))
    document = await legal_document(api, techdir, legal, unit["id"])

    row = await api.post(
        f"{API}/units/{unit['id']}/documents",
        json={
            "kind": "permit",
            "number": "P-1",
            "valid_until": "2035-12-31",
            "document_id": document["id"],
        },
        headers={**techdir, **idem()},
    )

    assert row.status_code == 201, row.text
    assert row.json()["document_id"] == document["id"]
    assert row.json()["document_no"] == document["registration_no"]
    listed = await api.get(f"{API}/units/{unit['id']}/documents", headers=techdir)
    numbers = {item["id"]: item["document_no"] for item in listed.json()}
    assert numbers[row.json()["id"]] == document["registration_no"]
    linked = await api.get(
        f"{DOCS}/documents", params={"equipment_unit_id": unit["id"]}, headers=techdir
    )
    assert [item["id"] for item in linked.json()] == [document["id"]]


async def test_a_document_of_another_kind_is_refused_with_its_code(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    legal = str(le_east(auth_world))
    give_access(worker_sync_engine, [auth_world.users["techdir"]], (), le_east(auth_world))
    techdir = await headers_for(api, "techdir")
    head = await headers_for(api, "equipment_head")
    unit = await new_unit(api, head, auth_world, legal_entity=le_east(auth_world))
    insurance = await legal_document(api, techdir, legal, unit["id"], kind="equipment_insurance")

    row = await api.post(
        f"{API}/units/{unit['id']}/documents",
        json={
            "kind": "permit",
            "number": "P-2",
            "valid_until": "2035-12-31",
            "document_id": insurance["id"],
        },
        headers={**techdir, **idem()},
    )

    assert row.status_code == 422, row.text
    assert row.json()["detail"] == "equipment.document_kind_mismatch"


async def test_a_document_not_linked_to_the_unit_is_refused_with_its_code(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    legal = str(le_east(auth_world))
    give_access(worker_sync_engine, [auth_world.users["techdir"]], (), le_east(auth_world))
    techdir = await headers_for(api, "techdir")
    head = await headers_for(api, "equipment_head")
    unit = await new_unit(api, head, auth_world, legal_entity=le_east(auth_world))
    other = await new_unit(api, head, auth_world, legal_entity=le_east(auth_world))
    stray = await legal_document(api, techdir, legal, other["id"])

    row = await api.post(
        f"{API}/units/{unit['id']}/documents",
        json={
            "kind": "permit",
            "number": "P-3",
            "valid_until": "2035-12-31",
            "document_id": stray["id"],
        },
        headers={**techdir, **idem()},
    )

    assert row.status_code == 422, row.text
    assert row.json()["detail"] == "equipment.document_unit_mismatch"


async def test_an_unknown_document_is_not_found(api: AsyncClient, auth_world: AuthWorld) -> None:
    techdir = await headers_for(api, "techdir")
    head = await headers_for(api, "equipment_head")
    unit = await new_unit(api, head, auth_world, legal_entity=le_east(auth_world))

    row = await api.post(
        f"{API}/units/{unit['id']}/documents",
        json={
            "kind": "permit",
            "number": "P-4",
            "valid_until": "2035-12-31",
            "document_id": str(uuid4()),
        },
        headers={**techdir, **idem()},
    )

    assert row.status_code == 404, row.text
    assert row.json()["detail"] == "not_found"
