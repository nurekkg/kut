"""Исправления по ревью безопасности SE-2a-fix (2026-10-13).

Каждый тест на прежнем коде дал бы другой ответ: тариф сжат и удалён вместе с проводками, машинист
получил единицу тарифа с чужим оператором, номер регистрации отдан вне юрлица, заправка создателя
видна после отзыва доступа к объекту."""

from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.modules.auth.principal import principal_store
from tests.auth_support import AuthWorld
from tests.equipment_support import (
    API,
    RATE_FROM,
    assign,
    day,
    east,
    headers_for,
    idem,
    in_context,
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
    rows,
    shift_body,
)


async def test_a_rate_closed_after_postings_is_not_deleted(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    """Сжатие valid_to не открывает удаление тарифа, по которому есть проводки."""
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    techdir = await headers_for(api, "techdir")
    le = le_east(auth_world)
    contract = await lessor_contract(
        api,
        finance,
        le,
        lessor(auth_world, "А"),
        [{"unit": "hour", "with_operator": True, "amount": "1500.00"}],
    )
    unit = await new_unit(
        api,
        head,
        auth_world,
        code=await new_type(api, head),
        ownership="rented",
        contract_id=contract,
    )
    operator = await lessor_operator(api, head, le, lessor(auth_world, "А"), "Машинист А")
    foreman = await headers_for(api, "foreman")
    shift = await post_shift(
        api,
        foreman,
        shift_body(
            unit_id=unit["id"],
            project_id=east(auth_world),
            work_date=past(3),
            operator_id=operator,
        ),
    )
    assert shift.status_code == 201, shift.text
    assert (await move(api, foreman, shift.json()["id"], "submit")).status_code == 200
    assert (await move(api, techdir, shift.json()["id"], "confirm")).status_code == 200
    listed = (await api.get(f"{API}/contracts/{contract}/rates", headers=finance)).json()
    used = next(rate for rate in listed if rate["equipment_unit_id"] is None)
    closed = await api.patch(
        f"{API}/contracts/{contract}/rates/{used['id']}",
        json={"valid_to": RATE_FROM},
        headers={**finance, **idem()},
    )
    assert closed.status_code == 200, closed.text
    refused = await api.delete(f"{API}/contracts/{contract}/rates/{used['id']}", headers=finance)
    assert (refused.status_code, refused.json()) == (
        409,
        {"detail": "equipment.rate_has_postings"},
    )


async def test_a_machinist_gets_the_unit_only_with_his_own_operator(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    """Машинист запрашивает единицу тарифа только со своим оператором; чужой отказан."""
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    machinist = await headers_for(api, "machinist")
    unit = await own_unit(api, auth_world, head, finance)
    own = await own_operator(api, machinist)
    planned = await assign(
        api,
        head,
        unit_id=str(unit["id"]),
        project_id=east(auth_world),
        start=0,
        end=0,
        hours="8",
        operator_id=own,
    )
    assert planned.status_code == 201, planned.text
    query = {"equipment_unit_id": str(unit["id"]), "work_date": day(0)}
    mine = await api.get(
        f"{API}/quantity-unit", params={**query, "operator_id": own}, headers=machinist
    )
    assert mine.status_code == 200, mine.text
    foreign = await lessor_operator(
        api, head, le_east(auth_world), lessor(auth_world, "А"), "Чужой оператор"
    )
    other = await api.get(
        f"{API}/quantity-unit", params={**query, "operator_id": foreign}, headers=machinist
    )
    assert (other.status_code, other.json()) == (422, {"detail": "equipment.operator_invalid"})


async def test_unit_labels_are_given_only_for_units_on_the_callers_projects(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    """Номер регистрации - только для единиц с листом на объекте вызывающего, не по одному id."""
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    foreman = await headers_for(api, "foreman")
    machinist = await headers_for(api, "machinist")
    unit = await own_unit(api, auth_world, head, finance)
    shift = await post_shift(
        api,
        foreman,
        shift_body(
            unit_id=unit["id"],
            project_id=east(auth_world),
            work_date=past(2),
            operator_id=await own_operator(api, machinist),
        ),
    )
    assert shift.status_code == 201, shift.text
    sql = "SELECT o_id, o_registry FROM app.equipment_unit_labels(CAST(:ids AS uuid[]))"
    params = {"ids": [str(unit["id"])]}
    on_project = in_context(auth_world.users["equipment_head"], (east(auth_world),), ())
    elsewhere = in_context(auth_world.users["equipment_head"], (), (uuid4(),))
    inside = await rows(sql, on_project, params)
    assert [(row[0], row[1]) for row in inside] == [(UUID(unit["id"]), unit["registry_number"])]
    assert await rows(sql, elsewhere, params) == []


async def test_own_fuel_is_not_listed_after_the_project_access_is_revoked(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    """Заправка создателя видна только при доступе к объекту: после отзыва доступа - не видна."""
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    foreman = await headers_for(api, "foreman")
    machinist = await headers_for(api, "machinist")
    unit = await own_unit(api, auth_world, head, finance)
    shift = await post_shift(
        api,
        foreman,
        shift_body(
            unit_id=unit["id"],
            project_id=east(auth_world),
            work_date=past(1),
            operator_id=await own_operator(api, machinist),
        ),
    )
    assert shift.status_code == 201, shift.text
    fuel = await api.post(
        f"{API}/shifts/{shift.json()['id']}/fuel",
        json={"liters": "10", "amount": "1000.00"},
        headers={**machinist, **idem()},
    )
    assert fuel.status_code == 201, fuel.text
    fuel_id = fuel.json()["id"]
    before = await api.get(f"{API}/fuel", headers=machinist)
    assert before.status_code == 200, before.text
    assert fuel_id in [row["id"] for row in before.json()]

    user, project = auth_world.users["machinist"], east(auth_world)
    revoke = (
        "UPDATE user_project_access SET deleted_at = now()"
        " WHERE user_id = :u AND project_id = :p AND deleted_at IS NULL"
    )
    restore = (
        "UPDATE user_project_access SET deleted_at = NULL WHERE user_id = :u AND project_id = :p"
    )
    with worker_sync_engine.begin() as connection:
        connection.execute(text(revoke), {"u": user, "p": project})
    await principal_store.bump_scope()
    try:
        after = await api.get(f"{API}/fuel", headers=machinist)
        assert after.status_code == 200, after.text
        assert fuel_id not in [row["id"] for row in after.json()]
    finally:
        with worker_sync_engine.begin() as connection:
            connection.execute(text(restore), {"u": user, "p": project})
        await principal_store.bump_scope()


async def test_an_inactive_user_is_neither_a_bill_role_nor_a_delegate_candidate(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    """Неактивный пользователь не вводит накладные ГСМ и не назначается делегатом.

    Функцию подтверждающего-делегата вызываем через POST /delegates: гард создания проверяет её
    от имени владельца; кандидатный список фильтрует активность сам и проверку не покрывает."""
    foreman = await headers_for(api, "foreman")
    head, master = auth_world.users["equipment_head"], auth_world.users["master"]
    deactivate = 'UPDATE "user" SET is_active = false WHERE id = :u'
    activate = 'UPDATE "user" SET is_active = true WHERE id = :u'
    with worker_sync_engine.begin() as connection:
        connection.execute(text(deactivate), {"u": head})
        connection.execute(text(deactivate), {"u": master})
    try:
        bill = await rows("SELECT app.equipment_is_fuel_bill_role()", in_context(head, (), ()))
        assert bill == [(False,)]
        delegated = await api.post(
            f"{API}/delegates",
            json={
                "project_id": str(east(auth_world)),
                "delegate_user_id": str(master),
                "date_from": day(0),
                "date_to": day(2),
                "reason": "неактивный",
            },
            headers={**foreman, **idem()},
        )
        assert (delegated.status_code, delegated.json()) == (
            422,
            {"detail": "equipment.delegate_not_eligible"},
        )
    finally:
        with worker_sync_engine.begin() as connection:
            connection.execute(text(activate), {"u": head})
            connection.execute(text(activate), {"u": master})
