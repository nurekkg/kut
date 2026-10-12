"""Правила БД и переходы состояний модуля «Техника» (ADR-014 часть 1 §1 и §3, часть 3 §9 SE-1)."""

import asyncio
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.modules.auth import devseed
from tests.auth_support import AuthWorld
from tests.equipment_support import API, assign, day, east, headers_for, idem, new_unit, west


def lessor_a(auth_world: AuthWorld):  # type: ignore[no-untyped-def]
    return next(
        value
        for name, value in auth_world.counterparties.items()
        if name.startswith("Арендодатель А")
    )


async def test_day_limit_is_per_unit_across_objects_and_names_no_other_numbers(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    head = await headers_for(api, "equipment_head")
    unit = await new_unit(api, head, auth_world)
    first = await assign(
        api, head, unit_id=unit["id"], project_id=east(auth_world), start=0, end=2, hours="16"
    )
    assert first.status_code == 201, first.text
    over = await assign(
        api, head, unit_id=unit["id"], project_id=west(auth_world), start=0, end=2, hours="10"
    )
    assert over.status_code == 409
    assert over.json() == {"detail": "equipment.day_hours_exceeded"}
    fits = await assign(
        api, head, unit_id=unit["id"], project_id=west(auth_world), start=0, end=2, hours="8"
    )
    assert fits.status_code == 201, fits.text


async def test_twelve_and_twelve_on_two_objects_are_both_created(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    head = await headers_for(api, "equipment_head")
    unit = await new_unit(api, head, auth_world)
    one = await assign(
        api, head, unit_id=unit["id"], project_id=east(auth_world), start=5, end=7, hours="12"
    )
    two = await assign(
        api, head, unit_id=unit["id"], project_id=west(auth_world), start=5, end=7, hours="12"
    )
    assert one.status_code == 201 and two.status_code == 201


async def test_a_second_transaction_waits_for_the_unit_lock_and_then_fails(
    worker_engine: AsyncEngine, auth_world: AuthWorld
) -> None:
    """Суточный лимит без блокировки обходится двумя параллельными вставками: вторая не видит
    незакоммиченную первую. Блокировка единицы (FOR UPDATE в триггере) заставляет вторую ждать."""
    unit_id, sam = uuid4(), uuid4()
    legal, project = auth_world.legal_entities[devseed.LE_EAST], east(auth_world)
    async with worker_engine.connect() as setup:
        type_row = (
            await setup.execute(text("SELECT id FROM equipment_type WHERE code = 'SAM'"))
        ).scalar_one()
        await setup.execute(
            text(
                "INSERT INTO equipment_unit (id, legal_entity_id, equipment_type_id, ownership,"
                " registry_key, registry_number) VALUES (:id, :le, :type, 'own', :key, :key)"
            ),
            {"id": unit_id, "le": legal, "type": type_row, "key": f"LOCK{uuid4().hex[:10]}"},
        )
        await setup.commit()
    insert = text(
        "INSERT INTO equipment_assignment (id, project_id, legal_entity_id, equipment_unit_id,"
        " date_from, date_to, planned_hours_per_day, status) VALUES (:id, :project, :le, :unit,"
        " DATE '2032-01-01', DATE '2032-01-03', :hours, 'planned')"
    )
    first = await worker_engine.connect()
    second = await worker_engine.connect()
    try:
        await first.execute(
            insert, {"id": uuid4(), "project": project, "le": legal, "unit": unit_id, "hours": 16}
        )
        pending = asyncio.ensure_future(
            second.execute(
                insert, {"id": sam, "project": project, "le": legal, "unit": unit_id, "hours": 10}
            )
        )
        await asyncio.sleep(0.5)
        assert not pending.done(), "вторая вставка должна ждать блокировку единицы"
        await first.commit()
        with pytest.raises(DBAPIError, match=r"equipment\.day_hours_exceeded"):
            await pending
    finally:
        await first.close()
        await second.rollback()
        await second.close()


async def test_a_document_that_expires_inside_the_period_blocks_the_assignment(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    head = await headers_for(api, "equipment_head")
    mechanic = await headers_for(api, "mechanic")
    unit = await new_unit(api, head, auth_world)
    expired = await api.post(
        f"{API}/units/{unit['id']}/documents",
        json={"kind": "technical_inspection", "number": "ТО-2", "valid_until": day(2)},
        headers={**mechanic, **idem()},
    )
    assert expired.status_code == 201, expired.text
    blocked = await assign(
        api, head, unit_id=unit["id"], project_id=east(auth_world), start=0, end=4, hours="8"
    )
    assert blocked.json() == {"detail": "equipment.document_expired"}
    assert blocked.status_code == 409


async def test_a_prompt_document_expiring_inside_the_period_only_warns(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    head = await headers_for(api, "equipment_head")
    mechanic = await headers_for(api, "mechanic")
    unit = await new_unit(api, head, auth_world)
    await api.post(
        f"{API}/units/{unit['id']}/documents",
        json={"kind": "passport", "number": "ПС-1", "valid_until": day(3)},
        headers={**mechanic, **idem()},
    )
    created = await assign(
        api, head, unit_id=unit["id"], project_id=east(auth_world), start=0, end=6, hours="8"
    )
    assert created.status_code == 201, created.text
    warnings = created.json()["warnings"]
    assert [item["kind"] for item in warnings] == ["passport"]
    assert warnings[0]["code"] == "equipment.document_expiring"


async def test_an_open_repair_blocks_and_a_planned_maintenance_does_not(
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
            "planned_to": day(12),
        },
        headers={**mechanic, **idem()},
    )
    assert repair.status_code == 201, repair.text
    repair_id = repair.json()["id"]
    blocked = await assign(
        api, head, unit_id=unit["id"], project_id=east(auth_world), start=11, end=14, hours="8"
    )
    assert blocked.json() == {"detail": "equipment.maintenance_overlap"}
    to_only = await api.post(
        f"{API}/maintenance",
        json={
            "equipment_unit_id": unit["id"],
            "kind": "to",
            "title": "ТО-250",
            "planned_from": day(10),
            "planned_to": None,
        },
        headers={**mechanic, **idem()},
    )
    assert to_only.status_code == 201
    unlocked = await assign(
        api, head, unit_id=unit["id"], project_id=east(auth_world), start=20, end=21, hours="8"
    )
    assert unlocked.status_code == 201, unlocked.text
    assert repair_id


async def test_maintenance_moves_only_along_the_documented_states(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    head = await headers_for(api, "equipment_head")
    mechanic = await headers_for(api, "mechanic")
    unit = await new_unit(api, head, auth_world)
    created = await api.post(
        f"{API}/maintenance",
        json={
            "equipment_unit_id": unit["id"],
            "kind": "repair",
            "title": "Ходовая",
            "planned_from": day(30),
        },
        headers={**mechanic, **idem()},
    )
    maintenance = created.json()["id"]
    skip_to_done = await api.patch(
        f"{API}/maintenance/{maintenance}", json={"status": "done"}, headers=mechanic
    )
    assert skip_to_done.json() == {"detail": "equipment.transition_forbidden"}
    started = await api.patch(
        f"{API}/maintenance/{maintenance}", json={"status": "in_progress"}, headers=mechanic
    )
    assert started.json()["status"] == "in_progress"
    again = await api.patch(
        f"{API}/maintenance/{maintenance}", json={"status": "in_progress"}, headers=mechanic
    )
    assert again.status_code == 200
    done = await api.patch(
        f"{API}/maintenance/{maintenance}", json={"status": "done"}, headers=mechanic
    )
    assert done.json()["status"] == "done" and done.json()["done_at"]
    back = await api.patch(
        f"{API}/maintenance/{maintenance}", json={"status": "cancelled"}, headers=mechanic
    )
    assert back.json() == {"detail": "equipment.transition_forbidden"}


async def test_rented_unit_and_rate_need_an_active_contract_of_the_right_kind(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    finance = await headers_for(api, "finance")
    head = await headers_for(api, "equipment_head")
    contract = await api.post(
        f"{API}/contracts",
        json={
            "legal_entity_id": str(auth_world.legal_entities[devseed.LE_EAST]),
            "counterparty_id": str(lessor_a(auth_world)),
            "kind": "rental_equipment",
            "number": f"АР-{uuid4().hex[:6]}",
            "date_from": day(0),
        },
        headers={**finance, **idem()},
    )
    assert contract.status_code == 201, contract.text
    contract_id = contract.json()["id"]
    early_rate = await api.post(
        f"{API}/contracts/{contract_id}/rates",
        json={"unit": "hour", "with_operator": False, "amount": "2000", "valid_from": day(0)},
        headers={**finance, **idem()},
    )
    assert early_rate.json() == {"detail": "equipment.contract_not_active"}
    active = await api.patch(
        f"{API}/contracts/{contract_id}", json={"status": "active"}, headers=finance
    )
    assert active.json()["status"] == "active"
    rate = await api.post(
        f"{API}/contracts/{contract_id}/rates",
        json={
            "unit": "hour",
            "with_operator": True,
            "amount": "2600",
            "min_quantity": "24",
            "valid_from": day(0),
        },
        headers={**finance, **idem()},
    )
    assert rate.status_code == 201, rate.text
    rented = await new_unit(api, head, auth_world, ownership="rented", contract_id=contract_id)
    assert rented["contract_id"] == contract_id
    await api.patch(f"{API}/contracts/{contract_id}", json={"status": "closed"}, headers=finance)
    reattach = await api.post(
        f"{API}/units",
        json={
            "legal_entity_id": str(auth_world.legal_entities[devseed.LE_EAST]),
            "equipment_type_id": rented["equipment_type_id"],
            "ownership": "rented",
            "registry_number": f"R-{uuid4().hex[:8]}",
            "contract_id": contract_id,
        },
        headers={**head, **idem()},
    )
    assert reattach.json() == {"detail": "equipment.contract_not_active"}


async def test_own_rate_is_approved_once_and_never_by_its_proposer(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    techdir = await headers_for(api, "techdir")
    le = str(auth_world.legal_entities[devseed.LE_EAST])
    type_row = (await api.get(f"{API}/types", headers=head)).json()
    sam = next(item["id"] for item in type_row if item["code"] == "SAM")
    proposal = await api.post(
        f"{API}/own-rates",
        json={
            "legal_entity_id": le,
            "equipment_type_id": sam,
            "unit": "trip",
            "amount": "3500",
            "valid_from": day(400),
        },
        headers={**head, **idem()},
    )
    assert proposal.status_code == 201, proposal.text
    rate_id = proposal.json()["id"]
    assert proposal.json()["approved_by"] is None
    by_head = await api.post(f"{API}/own-rates/{rate_id}/approve", headers=head)
    assert by_head.status_code == 403
    by_techdir = await api.post(f"{API}/own-rates/{rate_id}/approve", headers=techdir)
    assert by_techdir.status_code == 403
    approved = await api.post(f"{API}/own-rates/{rate_id}/approve", headers=finance)
    assert approved.status_code == 200 and approved.json()["approved_by"]
    again = await api.post(f"{API}/own-rates/{rate_id}/approve", headers=finance)
    assert again.json() == {"detail": "equipment.rate_already_approved"}


async def test_request_goes_draft_submitted_rejected_reopened_assigned_closed(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    foreman = await headers_for(api, "foreman")
    head = await headers_for(api, "equipment_head")
    type_row = (await api.get(f"{API}/types", headers=head)).json()
    sam_id = next(item["id"] for item in type_row if item["code"] == "SAM")
    body = {
        "project_id": str(east(auth_world)),
        "equipment_type_id": sam_id,
        "date_from": day(50),
        "date_to": day(52),
        "planned_hours_per_day": "8",
    }
    created = await api.post(f"{API}/requests", json=body, headers={**foreman, **idem()})
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]
    assert created.json()["status"] == "draft"
    submitted = await api.post(f"{API}/requests/{request_id}/submit", headers=foreman)
    assert submitted.json()["status"] == "submitted"
    no_reason = await api.post(
        f"{API}/requests/{request_id}/reject", json={"reason": ""}, headers=head
    )
    assert no_reason.status_code == 422
    rejected = await api.post(
        f"{API}/requests/{request_id}/reject", json={"reason": "Нет техники"}, headers=head
    )
    assert rejected.json()["status"] == "rejected"
    reopened = await api.post(f"{API}/requests/{request_id}/reopen", headers=foreman)
    assert reopened.json()["status"] == "draft"
    await api.post(f"{API}/requests/{request_id}/submit", headers=foreman)
    unit = await new_unit(api, head, auth_world)
    assigned = await assign(
        api,
        head,
        unit_id=unit["id"],
        project_id=east(auth_world),
        start=50,
        end=52,
        hours="8",
        request_id=request_id,
    )
    assert assigned.status_code == 201, assigned.text
    requests = (
        await api.get(f"{API}/requests", headers=head, params={"project_id": str(east(auth_world))})
    ).json()
    assert next(item for item in requests if item["id"] == request_id)["status"] == "assigned"
    finished = await api.post(f"{API}/assignments/{assigned.json()['id']}/finish", headers=head)
    assert finished.json()["status"] == "finished"
    requests = (
        await api.get(f"{API}/requests", headers=head, params={"project_id": str(east(auth_world))})
    ).json()
    assert next(item for item in requests if item["id"] == request_id)["status"] == "closed"


async def test_cancelling_an_assigned_request_cancels_its_planned_assignments(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    foreman = await headers_for(api, "foreman")
    head = await headers_for(api, "equipment_head")
    types = (await api.get(f"{API}/types", headers=head)).json()
    sam_id = next(item["id"] for item in types if item["code"] == "SAM")
    body = {
        "project_id": str(east(auth_world)),
        "equipment_type_id": sam_id,
        "date_from": day(60),
        "date_to": day(62),
        "planned_hours_per_day": "8",
    }
    request_id = (
        await api.post(f"{API}/requests", json=body, headers={**foreman, **idem()})
    ).json()["id"]
    await api.post(f"{API}/requests/{request_id}/submit", headers=foreman)
    unit = await new_unit(api, head, auth_world)
    created = await assign(
        api,
        head,
        unit_id=unit["id"],
        project_id=east(auth_world),
        start=60,
        end=62,
        hours="8",
        request_id=request_id,
    )
    cancelled = await api.post(f"{API}/requests/{request_id}/cancel", headers=head)
    assert cancelled.json()["status"] == "cancelled"
    rows = (
        await api.get(f"{API}/assignments", headers=head, params={"unit_id": unit["id"]})
    ).json()
    assert (
        next(item for item in rows if item["id"] == created.json()["id"])["status"] == "cancelled"
    )
    assert cancelled.status_code == 200


async def test_a_node_with_a_live_equipment_request_cannot_be_deleted(
    worker_engine: AsyncEngine, auth_world: AuthWorld
) -> None:
    """Точка расширения S2: живая заявка техники на узел мешает удалению узла (ADR-014 SE-1)."""
    project = east(auth_world)
    request_id = uuid4()
    async with worker_engine.begin() as connection:
        node = (
            await connection.execute(
                text(
                    "SELECT n.id FROM project_node n WHERE n.project_id = :project"
                    " AND n.deleted_at IS NULL"
                    " AND NOT EXISTS (SELECT 1 FROM project_node c WHERE c.parent_id = n.id"
                    " AND c.deleted_at IS NULL)"
                    " AND NOT EXISTS (SELECT 1 FROM schedule_item s WHERE s.node_id = n.id"
                    " AND s.deleted_at IS NULL)"
                    " AND NOT EXISTS (SELECT 1 FROM work_actual w WHERE w.node_id = n.id"
                    " AND w.deleted_at IS NULL) LIMIT 1"
                ),
                {"project": project},
            )
        ).scalar_one()
        sam = (
            await connection.execute(text("SELECT id FROM equipment_type WHERE code = 'SAM'"))
        ).scalar_one()
        await connection.execute(
            text(
                "INSERT INTO equipment_request (id, project_id, legal_entity_id, node_id,"
                " equipment_type_id, date_from, date_to, planned_hours_per_day, status)"
                " VALUES (:id, :project, :le, :node, :type, DATE '2031-05-01',"
                " DATE '2031-05-02', 8, 'draft')"
            ),
            {
                "id": request_id,
                "project": project,
                "le": auth_world.legal_entities[devseed.LE_EAST],
                "node": node,
                "type": sam,
            },
        )
    try:
        async with worker_engine.connect() as connection:
            with pytest.raises(DBAPIError, match=r"node\.has_dependents"):
                await connection.execute(
                    text("UPDATE project_node SET deleted_at = now() WHERE id = :node"),
                    {"node": node},
                )
                await connection.commit()
    finally:
        async with worker_engine.begin() as connection:
            await connection.execute(
                text("UPDATE equipment_request SET deleted_at = now() WHERE id = :id"),
                {"id": request_id},
            )


async def test_a_foreman_cannot_edit_a_draft_written_by_another_author(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    head_id = auth_world.users["equipment_head"]
    request_id = uuid4()
    async with worker_engine.begin() as connection:
        sam = (
            await connection.execute(text("SELECT id FROM equipment_type WHERE code = 'SAM'"))
        ).scalar_one()
        await connection.execute(
            text(
                "INSERT INTO equipment_request (id, project_id, legal_entity_id, equipment_type_id,"
                " date_from, date_to, planned_hours_per_day, status, created_by)"
                " VALUES (:id, :project, :le, :type, DATE '2031-06-01', DATE '2031-06-02', 8,"
                " 'draft', :author)"
            ),
            {
                "id": request_id,
                "project": east(auth_world),
                "le": auth_world.legal_entities[devseed.LE_EAST],
                "type": sam,
                "author": head_id,
            },
        )
    foreman = await headers_for(api, "foreman")
    response = await api.patch(
        f"{API}/requests/{request_id}", json={"planned_hours_per_day": "6"}, headers=foreman
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "forbidden"}
