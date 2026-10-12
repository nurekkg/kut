"""Тарифы по уровню (SE-2b, решение PO 4): привязка к единице или типу, запрет пересечения уровня и
даты начала при вводе, одна привязка на строку, ставка на дату работы (решение PO 6)."""

from typing import Any

from httpx import AsyncClient

from tests.auth_support import AuthWorld
from tests.equipment_support import (
    API,
    RATE_FROM,
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
    past,
    post_shift,
    shift_body,
    type_id,
)


async def _rate(
    api: AsyncClient, finance: dict[str, str], contract: str, body: dict[str, Any]
) -> Any:
    return await api.post(
        f"{API}/contracts/{contract}/rates",
        json={"valid_from": RATE_FROM, **body},
        headers={**finance, **idem()},
    )


async def test_a_second_billing_unit_with_the_same_start_is_refused_at_input(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    finance = await headers_for(api, "finance")
    contract = await lessor_contract(
        api,
        finance,
        le_east(auth_world),
        lessor(auth_world, "А"),
        [{"unit": "hour", "with_operator": True, "amount": "1500.00"}],
    )
    clash = await _rate(
        api, finance, contract, {"unit": "shift", "with_operator": True, "amount": "2000.00"}
    )
    assert (clash.status_code, clash.json()) == (
        422,
        {"detail": "equipment.rate_level_conflict"},
    )
    other_flag = await _rate(
        api, finance, contract, {"unit": "shift", "with_operator": False, "amount": "2000.00"}
    )
    assert other_flag.status_code == 201, other_flag.text


async def test_a_rate_names_one_target_that_belongs_to_its_contract(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    le = le_east(auth_world)
    contract = await lessor_contract(
        api,
        finance,
        le,
        lessor(auth_world, "А"),
        [{"unit": "hour", "with_operator": False, "amount": "900.00"}],
    )
    other = await lessor_contract(
        api,
        finance,
        le,
        lessor(auth_world, "Б"),
        [{"unit": "trip", "with_operator": False, "amount": "300.00"}],
    )
    own_code = await new_type(api, head)
    foreign_unit = await new_unit(
        api, head, auth_world, code=own_code, ownership="rented", contract_id=other
    )
    foreign = await _rate(
        api,
        finance,
        contract,
        {
            "unit": "hour",
            "with_operator": True,
            "amount": "1200.00",
            "equipment_unit_id": foreign_unit["id"],
        },
    )
    assert (foreign.status_code, foreign.json()) == (
        422,
        {"detail": "equipment.rate_target_invalid"},
    )
    own_unit = await new_unit(
        api, head, auth_world, code=own_code, ownership="rented", contract_id=contract
    )
    both = await _rate(
        api,
        finance,
        contract,
        {
            "unit": "hour",
            "with_operator": True,
            "amount": "1200.00",
            "equipment_unit_id": own_unit["id"],
            "equipment_type_id": await type_id(api, head, own_code),
        },
    )
    assert (both.status_code, both.json()) == (422, {"detail": "equipment.rate_target_invalid"})


async def test_two_billing_units_of_one_level_refuse_only_the_days_they_both_cover(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    """Тариф с поздней датой начала не трогает прежние дни; на дату, где подходят оба, - отказ."""
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    foreman = await headers_for(api, "foreman")
    le = le_east(auth_world)
    lessee = await lessor_operator(api, head, le, lessor(auth_world, "А"), "Машинист арендодателя")
    contract = await lessor_contract(
        api,
        finance,
        le,
        lessor(auth_world, "А"),
        [{"unit": "hour", "with_operator": True, "amount": "1500.00"}],
    )
    later = await api.post(
        f"{API}/contracts/{contract}/rates",
        json={
            "unit": "shift",
            "with_operator": True,
            "amount": "2000.00",
            "valid_from": "2026-05-01",
        },
        headers={**finance, **idem()},
    )
    assert later.status_code == 201, later.text
    unit = await new_unit(
        api,
        head,
        auth_world,
        code=await new_type(api, head),
        ownership="rented",
        contract_id=contract,
    )

    def sheet(work_date: str) -> dict[str, Any]:
        return shift_body(
            unit_id=unit["id"],
            project_id=east(auth_world),
            work_date=work_date,
            operator_id=lessee,
            hours="6",
        )

    before = await post_shift(api, foreman, sheet("2026-04-15"))
    assert before.status_code == 201, before.text
    assert (await move(api, foreman, before.json()["id"], "submit")).status_code == 200
    after = await post_shift(api, foreman, sheet(past(1)))
    assert after.status_code == 201, after.text
    refused = await move(api, foreman, after.json()["id"], "submit")
    assert (refused.status_code, refused.json()) == (422, {"detail": "equipment.rate_ambiguous"})
