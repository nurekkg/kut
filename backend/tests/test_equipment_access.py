"""Роли, области видимости и граница денег модуля «Техника» (ADR-014 часть 2 §5)."""

import pytest
from httpx import AsyncClient

from app.modules.auth import devseed
from tests.auth_support import AuthWorld
from tests.equipment_support import (
    API,
    assign,
    context,
    day,
    east,
    headers_for,
    idem,
    new_unit,
    rows,
    west,
)

AUDIT_MONEY = (
    "SELECT count(*) FROM audit_log"
    " WHERE table_name IN ('equipment_own_rate', 'equipment_rate', 'contract')"
)


async def test_the_foreman_gets_no_money_from_the_api(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    foreman = await headers_for(api, "foreman")
    assert (await api.get(f"{API}/own-rates", headers=foreman)).status_code == 403
    assert (await api.get(f"{API}/contracts", headers=foreman)).status_code == 403
    assert (
        await api.post(f"{API}/own-rates", json={}, headers={**foreman, **idem()})
    ).status_code == 403


async def test_the_head_reads_money_and_the_lawyer_reads_contracts_but_not_the_registry(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    head = await headers_for(api, "equipment_head")
    lawyer = await headers_for(api, "lawyer")
    assert (await api.get(f"{API}/own-rates", headers=head)).status_code == 200
    assert (await api.get(f"{API}/contracts", headers=head)).status_code == 200
    assert (await api.get(f"{API}/contracts", headers=lawyer)).status_code == 200
    assert (await api.get(f"{API}/units", headers=lawyer)).status_code == 403


async def test_the_mechanic_maintains_units_but_cannot_assign_or_import(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    head = await headers_for(api, "equipment_head")
    mechanic = await headers_for(api, "mechanic")
    unit = await new_unit(api, head, auth_world)
    assert (await api.get(f"{API}/units/{unit['id']}", headers=mechanic)).status_code == 200
    denied = await assign(
        api, mechanic, unit_id=unit["id"], project_id=east(auth_world), start=40, end=41, hours="8"
    )
    assert denied.status_code == 403
    check = await api.post(f"{API}/import/check?filename=a.csv", content=b"x", headers=mechanic)
    assert check.status_code == 403
    assert (
        await api.post(f"{API}/units", json={}, headers={**mechanic, **idem()})
    ).status_code == 403


async def test_the_foreman_sees_only_assignments_of_their_own_object(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    head = await headers_for(api, "equipment_head")
    foreman = await headers_for(api, "foreman")
    unit = await new_unit(api, head, auth_world)
    await assign(
        api, head, unit_id=unit["id"], project_id=west(auth_world), start=70, end=71, hours="8"
    )
    seen = (
        await api.get(f"{API}/assignments", headers=foreman, params={"unit_id": unit["id"]})
    ).json()
    assert seen == []
    mine = await assign(
        api, head, unit_id=unit["id"], project_id=east(auth_world), start=72, end=73, hours="8"
    )
    assert mine.status_code == 201
    seen = (
        await api.get(f"{API}/assignments", headers=foreman, params={"unit_id": unit["id"]})
    ).json()
    assert [item["project_id"] for item in seen] == [str(east(auth_world))]


async def test_the_machinist_sees_only_their_own_assignments_and_no_requests_or_maintenance(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    machinist = await headers_for(api, "machinist")
    own = (await api.get(f"{API}/operators", headers=machinist)).json()
    assert [item["user_id"] for item in own] == [str(auth_world.users["machinist"])]
    seen = (await api.get(f"{API}/assignments", headers=machinist)).json()
    assert seen, "демо-назначение машиниста должно быть видно"
    assert {item["operator_id"] for item in seen} == {own[0]["id"]}
    assert (await api.get(f"{API}/requests", headers=machinist)).json() == []
    assert (await api.get(f"{API}/maintenance", headers=machinist)).json() == []


async def test_the_money_gate_holds_in_sql_for_the_application_role(
    auth_world: AuthWorld,
) -> None:
    foreman = context(
        auth_world, "foreman", (east(auth_world),), (auth_world.legal_entities[devseed.LE_EAST],)
    )
    head = context(
        auth_world,
        "equipment_head",
        (east(auth_world), west(auth_world)),
        tuple(auth_world.legal_entities.values()),
    )
    assert await rows("SELECT amount FROM equipment_own_rate", foreman) == []
    assert await rows("SELECT amount FROM equipment_rate", foreman) == []
    assert await rows("SELECT id FROM contract", foreman) == []
    assert await rows("SELECT id FROM equipment_own_rate", head)
    assert await rows("SELECT id FROM contract", head)


async def test_the_audit_journal_hides_rate_and_rental_rows_from_the_foreman(
    auth_world: AuthWorld,
) -> None:
    foreman = context(
        auth_world, "foreman", (east(auth_world),), (auth_world.legal_entities[devseed.LE_EAST],)
    )
    finance = context(
        auth_world,
        "finance",
        tuple(auth_world.projects.values()),
        tuple(auth_world.legal_entities.values()),
    )
    hidden = await rows(
        AUDIT_MONEY,
        foreman,
    )
    visible = await rows(
        AUDIT_MONEY,
        finance,
    )
    assert hidden == [(0,)]
    assert visible[0][0] > 0


async def test_a_context_without_legal_entities_sees_no_units(auth_world: AuthWorld) -> None:
    """Пустой охват юрлиц: политика реестра не пропускает ни одной единицы, даже у владельца."""
    owner_without_scope = context(auth_world, "owner", (), ())
    assert await rows("SELECT count(*) FROM equipment_unit", owner_without_scope) == [(0,)]


@pytest.mark.parametrize("table", ["equipment_unit", "equipment_assignment", "equipment_document"])
async def test_an_empty_context_sees_no_rows_of_the_module(
    auth_world: AuthWorld, table: str
) -> None:
    empty = context(auth_world, "foreman", (), ())
    assert await rows(f"SELECT count(*) FROM {table}", empty) == [(0,)]  # noqa: S608 - имена из списка


async def test_a_foreign_object_is_not_found_for_the_foreman(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    """Чужой объект прорабу - 404, не 403: его существование не раскрываем (ADR-014 часть 2 §5)."""
    foreman = await headers_for(api, "foreman")
    type_row = (await api.get(f"{API}/types", headers=foreman)).json()
    sam = next(item["id"] for item in type_row if item["code"] == "SAM")
    body = {"project_id": str(west(auth_world)), "equipment_type_id": sam,
            "date_from": "2031-04-01",
            "date_to": "2031-04-02", "planned_hours_per_day": "8"}  # fmt: skip
    response = await api.post(f"{API}/requests", json=body, headers={**foreman, **idem()})
    assert response.status_code == 404
    assert response.json() == {"detail": "not_found"}


async def test_the_module_audit_rows_are_hidden_from_the_machinist(auth_world: AuthWorld) -> None:
    """Журнал аудита техники: машинист не видит строк модуля, прораб - видит (ADR-014)."""
    query = "SELECT count(*) FROM audit_log WHERE table_name LIKE 'equipment\\_%'"
    scope = (east(auth_world),), (auth_world.legal_entities[devseed.LE_EAST],)
    machinist = context(auth_world, "machinist", *scope)
    foreman = context(auth_world, "foreman", *scope)
    assert await rows(query, machinist) == [(0,)]
    assert (await rows(query, foreman))[0][0] > 0


async def test_the_mechanic_closes_a_repair_writes_a_document_and_sees_no_money(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    head = await headers_for(api, "equipment_head")
    mechanic = await headers_for(api, "mechanic")
    unit = await new_unit(api, head, auth_world)
    repair = await api.post(
        f"{API}/maintenance",
        json={
            "equipment_unit_id": unit["id"],
            "kind": "repair",
            "title": "Гидравлика",
            "planned_from": day(10),
        },
        headers={**mechanic, **idem()},
    )
    assert repair.status_code == 201, repair.text
    maintenance = repair.json()["id"]
    started = await api.patch(
        f"{API}/maintenance/{maintenance}", json={"status": "in_progress"}, headers=mechanic
    )
    assert started.status_code == 200, started.text
    closed = await api.patch(
        f"{API}/maintenance/{maintenance}",
        json={"status": "done", "amount": "1500.00"},
        headers=mechanic,
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["done_at"] is not None
    # Механик вносит строку срока только для технического осмотра (PO 2026-10-18); страховка - 403.
    insurance = await api.post(
        f"{API}/units/{unit['id']}/documents",
        json={"kind": "insurance", "number": "СТ-2031", "valid_until": "2036-12-31"},
        headers={**mechanic, **idem()},
    )
    assert insurance.status_code == 403, insurance.text
    document = await api.post(
        f"{API}/units/{unit['id']}/documents",
        json={"kind": "technical_inspection", "number": "ТО-2031", "valid_until": "2036-12-31"},
        headers={**mechanic, **idem()},
    )
    assert document.status_code == 201, document.text
    card = await api.get(f"{API}/units/{unit['id']}", headers=mechanic)
    assert card.status_code == 200, card.text
    assert not {"rate", "rates", "amount", "contract"} & set(card.json())
    assert (await api.get(f"{API}/own-rates", headers=mechanic)).status_code == 403
    assert (await api.get(f"{API}/contracts", headers=mechanic)).status_code == 403
