"""Проводка по уровням тарифа и ГСМ машиниста (SE-2b, решения PO 3 и 4): эталон журнала до тиына.

Единица с собственным тарифом берёт его (уровень единицы), остальные единицы её типа - тариф типа,
прочие - часовой тариф договора. С машинистом арендодателя - часовой тариф договора с флагом. ГСМ
машиниста попадает в журнал при подтверждении листа. Договор с двумя подходящими тарифами одного
уровня на дату листа не проводится (equipment.rate_ambiguous). Строка = количество x ставка,
округление до тиына. Ожидаемые суммы посчитаны вручную, а не взяты из кода.
"""

from collections import Counter
from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

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
    own_operator,
    own_unit,
    past,
    post_shift,
    shift_body,
    type_id,
)
from tests.test_equipment_costs import journal, money


async def _confirm_sheet(
    api: AsyncClient, author: dict[str, str], approver: dict[str, str], body: dict[str, Any]
) -> dict[str, Any]:
    created = await post_shift(api, author, body)
    assert created.status_code == 201, created.text
    shift = created.json()
    assert (await move(api, author, shift["id"], "submit")).status_code == 200
    done = await move(api, approver, shift["id"], "confirm")
    assert done.status_code == 200, done.text
    return shift


async def level_world(api: AsyncClient, auth_world: AuthWorld) -> dict[str, Any]:
    le = le_east(auth_world)
    east_id = east(auth_world)
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    author = await headers_for(api, "foreman")
    techdir = await headers_for(api, "techdir")
    staff = await own_operator(api, await headers_for(api, "machinist"))
    lessee = await lessor_operator(api, head, le, lessor(auth_world, "А"), "Машинист арендодателя")
    contract = await lessor_contract(
        api,
        finance,
        le,
        lessor(auth_world, "А"),
        [
            {"unit": "hour", "with_operator": True, "amount": "1500.00"},
            {"unit": "hour", "with_operator": False, "amount": "900.00"},
        ],
    )
    type_a = await new_type(api, head)
    type_b = await new_type(api, head)
    type_a_id = await type_id(api, head, type_a)
    # Тариф типа (смена без машиниста) и тариф единицы (час без машиниста) - поверх часа договора.
    by_type = await api.post(
        f"{API}/contracts/{contract}/rates",
        json={
            "unit": "shift",
            "with_operator": False,
            "amount": "4000.00",
            "valid_from": RATE_FROM,
            "equipment_type_id": type_a_id,
        },
        headers={**finance, **idem()},
    )
    assert by_type.status_code == 201, by_type.text
    u1 = await new_unit(
        api, head, auth_world, code=type_a, ownership="rented", contract_id=contract
    )
    by_unit = await api.post(
        f"{API}/contracts/{contract}/rates",
        json={
            "unit": "hour",
            "with_operator": False,
            "amount": "700.00",
            "valid_from": RATE_FROM,
            "equipment_unit_id": u1["id"],
        },
        headers={**finance, **idem()},
    )
    assert by_unit.status_code == 201, by_unit.text
    u2 = await new_unit(
        api, head, auth_world, code=type_a, ownership="rented", contract_id=contract
    )
    u3 = await new_unit(
        api, head, auth_world, code=type_b, ownership="rented", contract_id=contract
    )
    sheets = [
        (u1, staff, 1, "8"),
        (u2, staff, 1, "6"),
        (u3, staff, 2, "7"),
        (u1, lessee, 3, "10"),
    ]
    for unit, operator, day, hours in sheets:
        await _confirm_sheet(
            api,
            author,
            techdir,
            shift_body(
                unit_id=unit["id"],
                project_id=east_id,
                work_date=past(day),
                operator_id=operator,
                hours=hours,
            ),
        )
    # ГСМ в листе прораба: заправку до отправки вносит машинист, в журнал - при проводке листа.
    own = await own_unit(api, auth_world, head, finance)
    machinist = await headers_for(api, "machinist")
    own_sheet = (
        await post_shift(
            api,
            author,
            shift_body(
                unit_id=own["id"],
                project_id=east_id,
                work_date=past(1),
                operator_id=staff,
                hours="8",
            ),
        )
    ).json()
    fuel = await api.post(
        f"{API}/shifts/{own_sheet['id']}/fuel",
        json={"liters": "40.00", "amount": "3200.00"},
        headers={**machinist, **idem()},
    )
    assert fuel.status_code == 201, fuel.text
    assert (await move(api, author, own_sheet["id"], "submit")).status_code == 200
    assert (await move(api, techdir, own_sheet["id"], "confirm")).status_code == 200
    # Два подходящих тарифа одного уровня на дату листа: отказ при отправке, в журнал строк нет.
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
    ambiguous_unit = await new_unit(
        api, head, auth_world, code=type_b, ownership="rented", contract_id=contract
    )
    ambiguous = await post_shift(
        api,
        author,
        shift_body(
            unit_id=ambiguous_unit["id"],
            project_id=east_id,
            work_date=past(1),
            operator_id=lessee,
            hours="5",
        ),
    )
    assert ambiguous.status_code == 201, ambiguous.text
    refused = await move(api, author, ambiguous.json()["id"], "submit")
    assert (refused.status_code, refused.json()) == (
        422,
        {"detail": "equipment.rate_ambiguous"},
    )
    return {
        "units": [u1["id"], u2["id"], u3["id"], own["id"], ambiguous_unit["id"]],
        "east": str(east_id),
        "own": own["id"],
        "u1": u1["id"],
        "u2": u2["id"],
        "u3": u3["id"],
        "ambiguous": ambiguous_unit["id"],
        "fuel_shift": own_sheet["id"],
    }


async def test_the_levels_and_the_machinist_fuel_post_to_the_tiyn(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    world = await level_world(api, auth_world)
    east_id, own, u1, u2, u3 = (
        world["east"],
        world["own"],
        world["u1"],
        world["u2"],
        world["u3"],
    )
    expected = Counter(
        [
            # единица с собственным тарифом (уровень единицы): 8 ч x 700
            (u1, east_id, "equipment_rental", 1, money(8), money(700), money(5600), False),
            # единица типа без своего тарифа (уровень типа): смена, количество 1 x 4000
            (u2, east_id, "equipment_rental", 1, money(1), money(4000), money(4000), False),
            # остальная техника договора (уровень договора, без машиниста): 7 ч x 900
            (u3, east_id, "equipment_rental", 1, money(7), money(900), money(6300), False),
            # та же единица с машинистом арендодателя (уровень договора, с машинистом): 10 ч x 1500
            (u1, east_id, "equipment_rental", 1, money(10), money(1500), money(15000), False),
            # своя техника: норматив 1000 за час x 8 ч
            (own, east_id, "equipment_own", 1, money(8), money(1000), money(8000), False),
            # ГСМ машиниста в его листе: 40 л, 3200,00 - строкой журнала версии 1 листа
            (own, east_id, "equipment_fuel", 1, money("40.00"), None, money("3200.00"), False),
        ]
    )
    assert await journal(worker_engine, world["units"]) == expected
