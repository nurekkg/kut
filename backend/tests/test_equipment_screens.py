"""Экраны SE-1 (ADR-014 часть 3 §6): статус на сегодня, проверка назначения, календарь, контрагенты.

Даты сегодняшнего окна берутся от реального дня по Бишкеку: статус и проверки считает сервер по
своему «сегодня», а общие помощники тестов работают от условной даты 2031 года.
"""

from datetime import timedelta
from typing import Any
from uuid import UUID

from httpx import AsyncClient

from app.modules.equipment.service import bishkek_today
from tests.auth_support import AuthWorld
from tests.equipment_support import API, east, headers_for, idem, new_type, new_unit, type_id


def on(offset: int) -> str:
    return (bishkek_today() + timedelta(days=offset)).isoformat()


def assignment_body(
    unit_id: str, project_id: UUID, start: int, end: int, hours: str
) -> dict[str, Any]:
    return {
        "equipment_unit_id": unit_id,
        "project_id": str(project_id),
        "date_from": on(start),
        "date_to": on(end),
        "planned_hours_per_day": hours,
    }


async def ids_with_status(client: AsyncClient, headers: dict[str, str], status: str) -> set[str]:
    listed = await client.get(
        f"{API}/units", headers=headers, params={"status": status, "limit": 500}
    )
    assert listed.status_code == 200, listed.text
    return {item["id"] for item in listed.json()}


async def test_the_registry_reports_todays_status_and_filters_by_it(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    head = await headers_for(api, "equipment_head")
    repaired = await new_unit(api, head, auth_world)
    full = await new_unit(api, head, auth_world)
    busy = await new_unit(api, head, auth_world)
    expired = await new_unit(api, head, auth_world)
    repair = await api.post(
        f"{API}/maintenance",
        json={
            "equipment_unit_id": repaired["id"],
            "kind": "repair",
            "title": "Ходовая",
            "planned_from": on(-1),
            "planned_to": on(3),
        },
        headers={**head, **idem()},
    )
    assert repair.status_code == 201, repair.text
    started = await api.patch(
        f"{API}/maintenance/{repair.json()['id']}", json={"status": "in_progress"}, headers=head
    )
    assert started.status_code == 200, started.text
    for unit, hours in ((full, "24"), (busy, "8")):
        created = await api.post(
            f"{API}/assignments",
            json=assignment_body(unit["id"], east(auth_world), 0, 0, hours),
            headers={**head, **idem()},
        )
        assert created.status_code == 201, created.text
    stale = await api.post(
        f"{API}/units/{expired['id']}/documents",
        json={"kind": "insurance", "number": "СТ-OLD", "valid_until": on(-1)},
        headers={**head, **idem()},
    )
    assert stale.status_code == 201, stale.text

    assert repaired["id"] in await ids_with_status(api, head, "in_repair")
    assert full["id"] in await ids_with_status(api, head, "day_full")
    assert busy["id"] in await ids_with_status(api, head, "assigned")
    assert expired["id"] in await ids_with_status(api, head, "doc_expired")
    assert repaired["id"] not in await ids_with_status(api, head, "day_full")

    by_project = await api.get(
        f"{API}/units",
        headers=head,
        params={"project_id": str(east(auth_world)), "limit": 500},
    )
    assert by_project.status_code == 200, by_project.text
    in_project = {item["id"] for item in by_project.json()}
    assert {busy["id"], full["id"]} <= in_project
    assert repaired["id"] not in in_project
    own = await api.get(
        f"{API}/units", headers=head, params={"q": busy["registry_number"], "limit": 5}
    )
    assert [item["status"] for item in own.json() if item["id"] == busy["id"]] == ["assigned"]


async def test_the_check_names_the_reason_and_writes_nothing(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    head = await headers_for(api, "equipment_head")
    foreman = await headers_for(api, "foreman")
    unit = await new_unit(api, head, auth_world)
    held = await api.post(
        f"{API}/assignments",
        json=assignment_body(unit["id"], east(auth_world), 0, 0, "16"),
        headers={**head, **idem()},
    )
    assert held.status_code == 201, held.text

    over = await api.post(
        f"{API}/assignments/check",
        json=assignment_body(unit["id"], east(auth_world), 0, 0, "10"),
        headers=head,
    )
    assert over.status_code == 409
    assert over.json() == {"detail": "equipment.day_hours_exceeded"}

    fits = await api.post(
        f"{API}/assignments/check",
        json=assignment_body(unit["id"], east(auth_world), 0, 0, "8"),
        headers=head,
    )
    assert fits.status_code == 200, fits.text
    assert fits.json() == {"warnings": []}

    listed = await api.get(f"{API}/assignments", headers=head, params={"unit_id": unit["id"]})
    assert len(listed.json()) == 1
    denied = await api.post(
        f"{API}/assignments/check",
        json=assignment_body(unit["id"], east(auth_world), 0, 0, "8"),
        headers=foreman,
    )
    assert denied.status_code == 403


async def test_the_calendar_shows_maintenance_and_documents_that_expire_in_the_window(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    """Календарь фильтруется по собственному типу теста: число единиц в базе и limit не влияют."""
    head = await headers_for(api, "equipment_head")
    code = await new_type(api, head)
    unit = await new_unit(api, head, auth_world, code=code)
    maintenance = await api.post(
        f"{API}/maintenance",
        json={
            "equipment_unit_id": unit["id"],
            "kind": "to",
            "title": "ТО-250",
            "planned_from": on(2),
            "planned_to": on(3),
        },
        headers={**head, **idem()},
    )
    assert maintenance.status_code == 201, maintenance.text
    passport = await api.post(
        f"{API}/units/{unit['id']}/documents",
        json={"kind": "passport", "number": "ПС-7", "valid_until": on(5)},
        headers={**head, **idem()},
    )
    assert passport.status_code == 201, passport.text

    calendar = await api.get(
        f"{API}/calendar",
        headers=head,
        params={
            "date_from": on(-4),
            "date_to": on(10),
            "limit": 1000,
            "type_id": await type_id(api, head, code),
        },
    )
    assert calendar.status_code == 200, calendar.text
    assert [item["id"] for item in calendar.json()["units"]] == [unit["id"]]
    row = next(item for item in calendar.json()["units"] if item["id"] == unit["id"])
    assert [item["title"] for item in row["maintenance"]] == ["ТО-250"]
    assert [(item["kind"], item["valid_until"]) for item in row["warnings"]] == [
        ("passport", on(5))
    ]


async def test_the_counterparty_names_are_listed_only_for_contract_writers(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    accountant = await headers_for(api, "accountant")
    foreman = await headers_for(api, "foreman")
    mechanic = await headers_for(api, "mechanic")
    listed = await api.get(f"{API}/counterparties", headers=accountant)
    assert listed.status_code == 200, listed.text
    assert listed.json() and set(listed.json()[0]) == {"id", "name"}
    assert (await api.get(f"{API}/counterparties", headers=foreman)).status_code == 403
    assert (await api.get(f"{API}/counterparties", headers=mechanic)).status_code == 403


async def test_the_request_and_assignment_lists_name_the_object(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    head = await headers_for(api, "equipment_head")
    foreman = await headers_for(api, "foreman")
    unit = await new_unit(api, head, auth_world)
    created = await api.post(
        f"{API}/requests",
        json={
            "project_id": str(east(auth_world)),
            "equipment_type_id": unit["equipment_type_id"],
            "date_from": on(40),
            "date_to": on(40),
            "planned_hours_per_day": "4",
        },
        headers={**foreman, **idem()},
    )
    assert created.status_code == 201, created.text
    listed = await api.get(
        f"{API}/requests", headers=head, params={"project_id": str(east(auth_world))}
    )
    assert listed.status_code == 200, listed.text
    assert listed.json() and all(item["project_name"] for item in listed.json())
    assignment = await api.post(
        f"{API}/assignments",
        json=assignment_body(unit["id"], east(auth_world), 41, 41, "4"),
        headers={**head, **idem()},
    )
    assert assignment.status_code == 201, assignment.text
    shown = await api.get(f"{API}/assignments", headers=head, params={"unit_id": unit["id"]})
    assert shown.status_code == 200, shown.text
    assert shown.json() and all(item["project_name"] for item in shown.json())
