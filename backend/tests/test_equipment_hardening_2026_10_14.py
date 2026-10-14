"""Усиление SE-2 (ветка se-2-hard): тип чека проверяет БД; счётчик «На согласовании» - только то,
что пользователь может подтвердить (то же правило, что гард переходов листа)."""

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from app.modules.equipment.service import bishkek_today
from tests.auth_support import AuthWorld
from tests.equipment_support import (
    API,
    east,
    headers_for,
    idem,
    in_context,
    le_east,
    lessor,
    lessor_operator,
    move,
    own_operator,
    own_unit,
    post_shift,
    rows,
    shift_body,
    signed_in,
)


async def test_the_database_refuses_a_missing_or_forbidden_receipt_type(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    """Тип чека проверяет БД: NULL и тип вне списка отказаны при прямом вызове от kut_app."""
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
            work_date=bishkek_today().isoformat(),
            operator_id=await own_operator(api, machinist),
        ),
    )
    assert shift.status_code == 201, shift.text
    shift_id = shift.json()["id"]
    fuel = await api.post(
        f"{API}/shifts/{shift_id}/fuel",
        json={"liters": "10", "amount": "1000.00"},
        headers={**foreman, **idem()},
    )
    assert fuel.status_code == 201, fuel.text
    fuel_id = fuel.json()["id"]
    key = f"project/{east(auth_world)}/shift/{shift_id}/fuel/{fuel_id}/{uuid4()}.jpg"
    ctx = in_context(auth_world.users["foreman"], (east(auth_world),), (le_east(auth_world),))
    sql = "SELECT app.equipment_fuel_attach_receipt(CAST(:f AS uuid), :k, :t)"
    # Контроль: допустимый тип и правильный ключ проходят, поэтому отказы ниже - про тип.
    await rows(sql, ctx, {"f": fuel_id, "k": key, "t": "image/jpeg"})
    for forbidden in (None, "text/html", "image/svg+xml"):
        with pytest.raises(DBAPIError) as info:
            await rows(sql, ctx, {"f": fuel_id, "k": key, "t": forbidden})
        assert "receipt.invalid" in str(info.value.orig), forbidden


async def test_the_approvals_count_lists_only_the_sheets_the_user_can_confirm(
    api: AsyncClient, auth_world: AuthWorld, new_user: Any, worker_sync_engine: Engine
) -> None:
    """Счётчик: мастер без делегирования - ничего; прораб - чужие листы; делегат на период - листы
    объекта, кроме своих. Свой лист и лист, где сам оператор, никому не считаются."""
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    foreman = await headers_for(api, "foreman")
    machinist = await headers_for(api, "machinist")
    unit = await own_unit(api, auth_world, head, finance)
    operator = await own_operator(api, machinist)
    today = bishkek_today().isoformat()
    project = east(auth_world)
    _, master = await signed_in(
        api,
        new_user,
        worker_sync_engine,
        "master",
        projects=(project,),
        entities=(le_east(auth_world),),
    )
    delegate_id, delegate = await signed_in(
        api,
        new_user,
        worker_sync_engine,
        "master",
        projects=(project,),
        entities=(le_east(auth_world),),
    )

    async def submitted_sheet(headers: dict[str, str], operator_id: str) -> str:
        created = await post_shift(
            api,
            headers,
            shift_body(
                unit_id=unit["id"],
                project_id=project,
                work_date=today,
                operator_id=operator_id,
            ),
        )
        assert created.status_code == 201, created.text
        assert (await move(api, headers, created.json()["id"], "submit")).status_code == 200
        return str(created.json()["id"])

    async def approvable(headers: dict[str, str]) -> set[str]:
        listed = await api.get(
            f"{API}/shifts",
            params={
                "project_id": str(project),
                "status": "submitted",
                "approvable": "true",
                "limit": 500,
            },
            headers=headers,
        )
        assert listed.status_code == 200, listed.text
        return {item["id"] for item in listed.json()}

    # Каждый лист - со своим оператором: лист уникален по единице, дню, объекту и оператору.
    by_master = await submitted_sheet(master, operator)
    by_foreman = await submitted_sheet(
        foreman,
        await lessor_operator(
            api, head, le_east(auth_world), lessor(auth_world, "А"), "Оператор прораба"
        ),
    )
    by_delegate = await submitted_sheet(
        delegate,
        await lessor_operator(
            api, head, le_east(auth_world), lessor(auth_world, "А"), "Оператор делегата"
        ),
    )

    # Мастер без делегирования не подтверждает ничего: ни своих, ни чужих листов.
    assert await approvable(master) == set()
    # Прораб подтверждает листы других, свой не считает.
    foreman_sheets = await approvable(foreman)
    assert by_master in foreman_sheets and by_delegate in foreman_sheets
    assert by_foreman not in foreman_sheets

    # Делегат до назначения не подтверждает; после назначения - листы объекта, кроме своих.
    assert await approvable(delegate) == set()
    created = await api.post(
        f"{API}/delegates",
        json={
            "project_id": str(project),
            "delegate_user_id": str(delegate_id),
            "date_from": today,
            "date_to": (bishkek_today() + timedelta(days=2)).isoformat(),
            "reason": "счётчик: делегат на период",
        },
        headers={**foreman, **idem()},
    )
    assert created.status_code == 201, created.text
    delegated = await approvable(delegate)
    assert {by_foreman, by_master} <= delegated
    assert by_delegate not in delegated
    revoked = await api.post(f"{API}/delegates/{created.json()['id']}/revoke", headers=foreman)
    assert revoked.status_code == 200, revoked.text
    assert await approvable(delegate) == set()
