"""Тариф аренды на дату листа и живая история объекта (ADR-014 часть 2 §3, раздел 1)."""

import asyncio
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.auth_support import AuthWorld
from tests.equipment_support import (
    API,
    assign,
    east,
    headers_for,
    idem,
    le_east,
    lessor,
    lessor_contract,
    lessor_operator,
    move,
    new_type,
    new_unit,
    own_operator,
    own_unit,
    past,
    post_shift,
    second_project,
    shift_body,
    signed_in,
)


async def test_a_rented_shift_on_a_closed_contract_is_refused_at_submission(
    api: AsyncClient, auth_world: AuthWorld, new_user: Any, worker_sync_engine: Engine
) -> None:
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    contract = await lessor_contract(
        api,
        finance,
        le_east(auth_world),
        lessor(auth_world, "Б"),
        [{"unit": "trip", "with_operator": False, "amount": "333.33"}],
    )
    code = await new_type(api, head)
    unit = await new_unit(
        api, head, auth_world, code=code, ownership="rented", contract_id=contract
    )
    operator = await own_operator(api, await headers_for(api, "machinist"))
    _, author = await signed_in(
        api,
        new_user,
        worker_sync_engine,
        "foreman",
        projects=(east(auth_world),),
        entities=(le_east(auth_world),),
    )
    shift = (
        await post_shift(
            api,
            author,
            shift_body(
                unit_id=unit["id"],
                project_id=east(auth_world),
                work_date=past(18),
                operator_id=operator,
                hours="9",
                trips=7,
            ),
        )
    ).json()
    closed = await api.patch(
        f"{API}/contracts/{contract}", json={"status": "closed"}, headers=finance
    )
    assert closed.status_code == 200, closed.text
    refused = await move(api, author, shift["id"], "submit")
    assert refused.json() == {"detail": "equipment.contract_inactive"}


async def test_a_project_with_a_shift_is_not_soft_deleted(
    api: AsyncClient, auth_world: AuthWorld, new_user: Any, worker_sync_engine: Engine
) -> None:
    project = second_project(worker_sync_engine, le_east(auth_world))
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    owner = await headers_for(api, "owner")
    unit = await own_unit(api, auth_world, head, finance)
    operator = await own_operator(api, await headers_for(api, "machinist"))
    _, author = await signed_in(
        api,
        new_user,
        worker_sync_engine,
        "foreman",
        projects=(project,),
        entities=(le_east(auth_world),),
    )
    created = await post_shift(
        api,
        author,
        shift_body(
            unit_id=unit["id"],
            project_id=project,
            work_date=past(19),
            operator_id=operator,
            hours="4",
        ),
    )
    assert created.status_code == 201, created.text
    deleted = await api.delete(f"/api/v1/projects/{project}", headers=owner)
    assert (deleted.status_code, deleted.json()) == (409, {"detail": "project_has_shifts"})
    still = await api.get(f"/api/v1/projects/{project}", headers=owner)
    assert still.status_code == 200, still.text


async def test_two_transactions_cannot_both_fill_one_unit_day(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    """Вторая транзакция ждёт блокировку единицы и после коммита первой видит её лист."""
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    unit = await own_unit(api, auth_world, head, finance)
    operator = await own_operator(api, await headers_for(api, "machinist"))
    insert = text(
        "INSERT INTO equipment_shift (id, legal_entity_id, project_id, equipment_unit_id,"
        " work_date, operator_id, hours, source, status, posting_version, lines_revision)"
        " VALUES (gen_random_uuid(), :le, :project, CAST(:unit AS uuid), :day,"
        " CAST(:operator AS uuid), :hours, 'manual', 'draft', 0, 0)"
    )
    params = {
        "le": le_east(auth_world),
        "project": east(auth_world),
        "unit": unit["id"],
        "operator": operator,
        "day": date(2026, 6, 25),
        "hours": Decimal("20"),
    }
    first = await worker_engine.connect()
    second = await worker_engine.connect()
    try:
        await first.execute(insert, params)
        late = asyncio.ensure_future(second.execute(insert, params))
        await asyncio.sleep(1)
        assert not late.done(), "вторая транзакция должна ждать блокировку единицы"
        await first.commit()
        with pytest.raises(DBAPIError, match=r"shift\.day_hours_exceeded"):
            await late
    finally:
        await second.rollback()
        await first.close()
        await second.close()


async def test_a_rejected_shift_cannot_be_reopened_into_a_full_day(
    api: AsyncClient, auth_world: AuthWorld, new_user: Any, worker_sync_engine: Engine
) -> None:
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    techdir = await headers_for(api, "techdir")
    unit = await own_unit(api, auth_world, head, finance)
    operator = await own_operator(api, await headers_for(api, "machinist"))
    _, author = await signed_in(
        api,
        new_user,
        worker_sync_engine,
        "foreman",
        projects=(east(auth_world),),
        entities=(le_east(auth_world),),
    )

    lessee = await lessor_operator(
        api, head, le_east(auth_world), lessor(auth_world, "А"), "Машинист арендодателя"
    )

    def body(hours: str, who: str) -> dict[str, Any]:
        return shift_body(
            unit_id=unit["id"],
            project_id=east(auth_world),
            work_date=past(21),
            operator_id=who,
            hours=hours,
        )

    first = (await post_shift(api, author, body("20", operator))).json()
    assert (await move(api, author, first["id"], "submit")).status_code == 200
    rejected = await api.post(
        f"{API}/shifts/{first['id']}/reject", json={"reason": "дубль"}, headers=techdir
    )
    assert rejected.status_code == 200, rejected.text
    second = await post_shift(api, author, body("20", lessee))
    assert second.status_code == 201, second.text
    reopened = await move(api, author, first["id"], "reopen")
    assert reopened.json() == {"detail": "equipment.day_hours_exceeded"}


async def test_a_machinist_cannot_enter_fuel(api: AsyncClient, auth_world: AuthWorld) -> None:
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    machinist = await headers_for(api, "machinist")
    operator = await own_operator(api, machinist)
    unit = await own_unit(api, auth_world, head, finance)
    linked = await assign(
        api,
        head,
        unit_id=unit["id"],
        project_id=east(auth_world),
        start=40,
        end=41,
        hours="6",
        operator_id=operator,
    )
    assert linked.status_code == 201, linked.text
    fuel = await api.post(
        f"{API}/fuel",
        json={
            "equipment_unit_id": str(unit["id"]),
            "project_id": str(east(auth_world)),
            "fuel_date": past(22),
            "amount": "500.00",
        },
        headers={**machinist, **idem()},
    )
    assert fuel.status_code == 403 and fuel.json() == {"detail": "forbidden"}
