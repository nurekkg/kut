"""Номер регистрации единицы в листах, которые видит пользователь без права на реестр.

Функция app.equipment_unit_labels отдаёт номер только по id единиц, которые вызывающий получил
из видимых ему листов. Мастер-делегат реестр не видит: номер в списке листов - единственный путь
узнать технику."""

from httpx import AsyncClient

from tests.auth_support import AuthWorld
from tests.equipment_support import (
    API,
    east,
    headers_for,
    move,
    own_operator,
    own_unit,
    past,
    post_shift,
    shift_body,
)


async def test_a_delegate_reads_the_registry_number_of_the_units_of_the_sheets_he_sees(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    unit = await own_unit(api, auth_world, head, finance)
    foreman = await headers_for(api, "foreman")
    machinist = await headers_for(api, "machinist")
    created = await post_shift(
        api,
        foreman,
        shift_body(
            unit_id=unit["id"],
            project_id=east(auth_world),
            work_date=past(4),
            operator_id=await own_operator(api, machinist),
        ),
    )
    assert created.status_code == 201, created.text
    shift_id = created.json()["id"]
    assert (await move(api, foreman, shift_id, "submit")).status_code == 200

    master = await headers_for(api, "master")
    registry = (await api.get(f"{API}/units", headers=master)).json()
    assert unit["registry_number"] not in [row["registry_number"] for row in registry]
    listed = await api.get(
        f"{API}/shifts",
        params={"status": "submitted", "project_id": str(east(auth_world))},
        headers=master,
    )
    assert listed.status_code == 200, listed.text
    row = next(item for item in listed.json() if item["id"] == shift_id)
    assert row["unit_registry_number"] == unit["registry_number"]
    card = await api.get(f"{API}/shifts/{shift_id}", headers=master)
    assert card.status_code == 200, card.text
    assert card.json()["unit_registry_number"] == unit["registry_number"]
