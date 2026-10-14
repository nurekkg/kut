"""Подсказки экрана техники (SE-2): единица тарификации без ставки и кандидаты в делегаты.
Право и объект проверяют функции БД: app.equipment_quantity_unit и
app.equipment_delegate_candidates."""

from typing import Any

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.modules.equipment.devseed import SECOND_EAST
from tests.auth_support import AuthWorld
from tests.equipment_support import (
    API,
    east,
    headers_for,
    le_east,
    lessor,
    lessor_contract,
    lessor_operator,
    money_keys,
    new_type,
    new_unit,
    own_operator,
    own_unit,
    past,
)


def project_named(engine: Engine, name: str) -> str:
    with engine.connect() as connection:
        return str(
            connection.execute(
                text("SELECT id FROM project WHERE name = :name AND deleted_at IS NULL"),
                {"name": name},
            ).scalar_one()
        )


async def test_the_quantity_unit_names_what_to_enter_and_never_the_rate(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    machinist = await headers_for(api, "machinist")
    foreman = await headers_for(api, "foreman")
    own = await own_unit(api, auth_world, head, finance)
    own_op = await own_operator(api, machinist)
    own_answer = await api.get(
        f"{API}/quantity-unit",
        params={
            "equipment_unit_id": own["id"],
            "operator_id": own_op,
            "work_date": past(2),
        },
        headers=foreman,
    )
    assert own_answer.status_code == 200, own_answer.text
    assert own_answer.json() == {"tariff_unit": "hour"}
    assert money_keys(own_answer.json()) == set()

    le = le_east(auth_world)
    contract = await lessor_contract(
        api,
        finance,
        le,
        lessor(auth_world, "Б"),
        [{"unit": "trip", "with_operator": True, "amount": "333.33"}],
    )
    rented = await new_unit(
        api,
        head,
        auth_world,
        code=await new_type(api, head),
        ownership="rented",
        contract_id=contract,
    )
    lessee = await lessor_operator(api, head, le, lessor(auth_world, "Б"), "Машинист Б (тест)")
    rented_answer = await api.get(
        f"{API}/quantity-unit",
        params={"equipment_unit_id": rented["id"], "operator_id": lessee, "work_date": past(2)},
        headers=foreman,
    )
    assert rented_answer.status_code == 200, rented_answer.text
    assert set(rented_answer.json()) == {"tariff_unit"}
    assert rented_answer.json()["tariff_unit"] == "trip"
    assert money_keys(rented_answer.json()) == set()


async def test_the_delegate_candidates_are_eligible_staff_of_the_object_for_an_approver_only(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    foreman = await headers_for(api, "foreman")
    east_id = str(east(auth_world))
    listed = await api.get(
        f"{API}/delegate-candidates", params={"project_id": east_id}, headers=foreman
    )
    assert listed.status_code == 200, listed.text
    candidates: list[dict[str, Any]] = listed.json()
    ids = {row["user_id"] for row in candidates}
    assert str(auth_world.users["master"]) in ids
    assert str(auth_world.users["machinist"]) not in ids
    assert str(auth_world.users["foreman2"]) not in ids
    assert all(set(row) == {"user_id", "full_name", "role_code"} for row in candidates)

    second = project_named(worker_sync_engine, SECOND_EAST)
    other_object = await api.get(
        f"{API}/delegate-candidates", params={"project_id": second}, headers=foreman
    )
    assert (other_object.status_code, other_object.json()) == (403, {"detail": "forbidden"})

    master = await headers_for(api, "master")
    no_right = await api.get(
        f"{API}/delegate-candidates", params={"project_id": east_id}, headers=master
    )
    assert (no_right.status_code, no_right.json()) == (403, {"detail": "forbidden"})

    second_foreman = await headers_for(api, "foreman2")
    own_object = await api.get(
        f"{API}/delegate-candidates", params={"project_id": second}, headers=second_foreman
    )
    assert own_object.status_code == 200, own_object.text
    own_ids = {row["user_id"] for row in own_object.json()}
    assert str(auth_world.users["foreman2"]) in own_ids
    assert str(auth_world.users["machinist"]) not in own_ids
