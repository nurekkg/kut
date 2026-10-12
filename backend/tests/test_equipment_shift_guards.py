"""Тариф аренды на дату листа и живая история объекта (ADR-014 часть 2 §3, раздел 1)."""

from typing import Any

from httpx import AsyncClient
from sqlalchemy.engine import Engine

from tests.auth_support import AuthWorld
from tests.equipment_support import (
    API,
    east,
    headers_for,
    le_east,
    lessor,
    lessor_contract,
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
    assert deleted.status_code == 409, deleted.text
    still = await api.get(f"/api/v1/projects/{project}", headers=owner)
    assert still.status_code == 200, still.text
