"""Определители, связанные с объектом, проверяют доступ к объекту (SE-2b, находка c ревью): роль с
правом, но без объекта, не получает чужие итоги, не сторнирует чужую заправку и не закрывает чужой
ремонт. Проверка идёт в самом определителе: доступ к юрлице этого не даёт."""

from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from tests.auth_support import AuthWorld
from tests.equipment_support import (
    API,
    east,
    headers_for,
    idem,
    in_context,
    le_east,
    own_unit,
    past,
    rows,
    second_project,
    signed_in,
)


async def fuel_on(
    api: AsyncClient, writer: dict[str, str], unit: dict[str, Any], project: UUID
) -> str:
    created = await api.post(
        f"{API}/fuel",
        json={
            "equipment_unit_id": unit["id"],
            "project_id": str(project),
            "fuel_date": past(1),
            "liters": "10.00",
            "amount": "1000.00",
        },
        headers={**writer, **idem()},
    )
    assert created.status_code == 201, created.text
    fuel_id: str = created.json()["id"]
    return fuel_id


async def test_a_foreman_of_one_object_gets_no_totals_of_another(
    api: AsyncClient, auth_world: AuthWorld, new_user: Any, worker_sync_engine: Engine
) -> None:
    le = le_east(auth_world)
    extra = second_project(worker_sync_engine, le)
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    unit = await own_unit(api, auth_world, head, finance)
    _, writer = await signed_in(
        api, new_user, worker_sync_engine, "foreman", projects=(extra,), entities=(le,)
    )
    await fuel_on(api, writer, unit, extra)
    east_only_id, east_only = await signed_in(
        api, new_user, worker_sync_engine, "foreman", projects=(east(auth_world),), entities=(le,)
    )
    seen_by_finance = await api.get(
        f"{API}/costs/totals",
        params={"date_from": "2026-01-01", "date_to": "2100-12-31", "project_id": str(extra)},
        headers=finance,
    )
    assert seen_by_finance.status_code == 200 and seen_by_finance.json() != []
    seen = await api.get(
        f"{API}/costs/totals",
        params={"date_from": "2026-01-01", "date_to": "2100-12-31", "project_id": str(extra)},
        headers=east_only,
    )
    assert (seen.status_code, seen.json()) == (200, [])
    ctx = in_context(east_only_id, (east(auth_world),), (le,))
    by_function = await rows(
        "SELECT project_id FROM app.equipment_cost_totals("
        "DATE '2026-01-01', DATE '2100-12-31', NULL)",
        ctx,
    )
    assert all(project != extra for (project,) in by_function)


async def test_a_technical_director_of_one_object_cannot_reverse_a_fuel_of_another(
    api: AsyncClient, auth_world: AuthWorld, new_user: Any, worker_sync_engine: Engine
) -> None:
    le = le_east(auth_world)
    extra = second_project(worker_sync_engine, le)
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    unit = await own_unit(api, auth_world, head, finance)
    _, writer = await signed_in(
        api, new_user, worker_sync_engine, "foreman", projects=(extra,), entities=(le,)
    )
    fuel = await fuel_on(api, writer, unit, extra)
    director_id, _ = await signed_in(
        api,
        new_user,
        worker_sync_engine,
        "technical_director",
        projects=(east(auth_world),),
        entities=(le,),
    )
    ctx = in_context(director_id, (east(auth_world),), (le,))
    with pytest.raises(DBAPIError, match=r"fuel\.forbidden"):
        await rows(
            "SELECT app.equipment_fuel_reverse(CAST(:f AS uuid), 'ошибка')", ctx, {"f": fuel}
        )


async def test_a_mechanic_of_one_object_cannot_close_a_repair_on_another(
    api: AsyncClient, auth_world: AuthWorld, new_user: Any, worker_sync_engine: Engine
) -> None:
    le = le_east(auth_world)
    extra = second_project(worker_sync_engine, le)
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    unit = await own_unit(api, auth_world, head, finance)
    mechanic = await headers_for(api, "mechanic")
    created = await api.post(
        f"{API}/maintenance",
        json={
            "equipment_unit_id": unit["id"],
            "kind": "repair",
            "title": "Гидравлика",
            "planned_from": past(2),
        },
        headers={**mechanic, **idem()},
    )
    assert created.status_code == 201, created.text
    repair = created.json()["id"]
    assert (
        await api.patch(
            f"{API}/maintenance/{repair}", json={"status": "in_progress"}, headers=mechanic
        )
    ).status_code == 200
    east_mechanic_id, _ = await signed_in(
        api, new_user, worker_sync_engine, "mechanic", projects=(east(auth_world),), entities=(le,)
    )
    ctx = in_context(east_mechanic_id, (east(auth_world),), (le,))
    with pytest.raises(DBAPIError, match=r"maintenance\.forbidden"):
        await rows(
            "SELECT app.equipment_maintenance_close(CAST(:m AS uuid), CAST(:p AS uuid), 100.00)",
            ctx,
            {"m": repair, "p": extra},
        )


async def test_a_foreman_of_one_object_cannot_delegate_another_object(
    api: AsyncClient, auth_world: AuthWorld, new_user: Any, worker_sync_engine: Engine
) -> None:
    le = le_east(auth_world)
    extra = second_project(worker_sync_engine, le)
    _, east_foreman = await signed_in(
        api, new_user, worker_sync_engine, "foreman", projects=(east(auth_world),), entities=(le,)
    )
    refused = await api.post(
        f"{API}/delegates",
        json={
            "project_id": str(extra),
            "delegate_user_id": str(auth_world.users["master"]),
            "date_from": "2099-01-01",
            "date_to": "2099-01-02",
            "reason": "чужой объект",
        },
        headers={**east_foreman, **idem()},
    )
    assert (refused.status_code, refused.json()) == (403, {"detail": "forbidden"})
