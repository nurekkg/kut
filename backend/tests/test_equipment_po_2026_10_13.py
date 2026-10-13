"""Решения PO 2026-10-13 (SE-2a-fix, backend) по пунктам брифа: подтверждающий не оператор листа
(3), накладная заправка без объекта (4), срок тарифа и удаление с проводками (6), чек заправки до
отправки и после подтверждения (5, 7), право на чек до подписи и прямой записи (8), ремонт сразу в
done (8). Пункты 1 и 2 - в test_equipment_costs.py и test_equipment_shifts.py."""

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from app.modules.equipment.service import bishkek_today
from tests.auth_support import AuthWorld
from tests.equipment_support import (
    API,
    RATE_FROM,
    context,
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
    own_unit,
    past,
    post_shift,
    rows,
    shift_body,
    signed_in,
)
from tests.test_equipment_fuel_shift import add_fuel, sheet, upload_receipt


@pytest.fixture
async def s3() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=10) as client:
        yield client


async def test_the_operator_with_an_account_never_confirms_his_own_hours(
    api: AsyncClient, auth_world: AuthWorld, new_user: Any, worker_sync_engine: Engine
) -> None:
    """Пункт 3: подтверждающий не оператор листа, если у оператора есть учётка."""
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    unit = await own_unit(api, auth_world, head, finance)
    operator_user, operator_foreman = await signed_in(
        api,
        new_user,
        worker_sync_engine,
        "foreman",
        projects=(east(auth_world),),
        entities=(le_east(auth_world),),
    )
    created = await api.post(
        f"{API}/operators",
        json={
            "legal_entity_id": str(le_east(auth_world)),
            "full_name": "Прораб как оператор (тест)",
            "employment": "staff",
            "user_id": str(operator_user),
        },
        headers={**head, **idem()},
    )
    assert created.status_code == 201, created.text
    author = await headers_for(api, "foreman")
    shift = await post_shift(
        api,
        author,
        shift_body(
            unit_id=unit["id"],
            project_id=east(auth_world),
            work_date=past(2),
            operator_id=created.json()["id"],
        ),
    )
    assert shift.status_code == 201, shift.text
    shift_id = shift.json()["id"]
    assert (await move(api, author, shift_id, "submit")).status_code == 200
    refused = await move(api, operator_foreman, shift_id, "confirm")
    assert (refused.status_code, refused.json()) == (
        403,
        {"detail": "equipment.approver_is_operator"},
    )
    techdir = await headers_for(api, "techdir")
    confirmed = await move(api, techdir, shift_id, "confirm")
    assert confirmed.status_code == 200, confirmed.text


async def test_an_overhead_fuel_is_entered_by_the_head_and_the_technical_director_only(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    """Пункт 4: накладная заправка без объекта - начальник отдела и техдиректор; прораб, мастер и
    машинист - нет; заправка с объектом без права записи листа - тоже нет."""
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    unit = await own_unit(api, auth_world, head, finance)
    body = {"equipment_unit_id": str(unit["id"]), "fuel_date": past(1), "liters": "10.00"}
    for who in ("foreman", "master", "machinist"):
        response = await api.post(
            f"{API}/fuel",
            json={**body, "amount": "100.00"},
            headers={**await headers_for(api, who), **idem()},
        )
        # Прораб видит технику и получает 403 от БД; мастер и машинист технику не видят - 404.
        assert response.status_code in (403, 404), f"{who}: {response.text}"
        assert response.status_code == 404 or response.json() == {"detail": "forbidden"}
    techdir = await headers_for(api, "techdir")
    for headers in (head, techdir):
        response = await api.post(
            f"{API}/fuel", json={**body, "amount": "100.00"}, headers={**headers, **idem()}
        )
        assert response.status_code == 201, response.text
    with_object = await api.post(
        f"{API}/fuel",
        json={**body, "project_id": str(east(auth_world)), "amount": "100.00"},
        headers={**head, **idem()},
    )
    assert (with_object.status_code, with_object.json()) == (403, {"detail": "forbidden"})


async def test_a_receipt_is_replaced_while_the_sheet_is_a_draft_and_locked_after_submit(
    api: AsyncClient, auth_world: AuthWorld, s3: httpx.AsyncClient
) -> None:
    """Пункты 5 и 7: чек меняют до отправки; после отправки и подтверждения - нет. Даже с уже
    выданной ссылкой загрузки (проверка и в подписи, и в записи)."""
    finance = await headers_for(api, "finance")
    machinist, foreman, shift_id = await sheet(api, auth_world, finance)
    fuel_id = (await add_fuel(api, machinist, shift_id)).json()["id"]
    first = await upload_receipt(api, s3, machinist, shift_id, fuel_id, b"first-jpeg!!")
    second = await upload_receipt(api, s3, machinist, shift_id, fuel_id, b"second-jpeg!")
    assert (
        await api.post(
            f"{API}/shifts/{shift_id}/fuel/{fuel_id}/receipt/confirm",
            json={"key": first},
            headers=machinist,
        )
    ).status_code == 200
    assert (await move(api, foreman, shift_id, "submit")).status_code == 200
    presign = await api.post(
        f"{API}/shifts/{shift_id}/fuel/{fuel_id}/receipt",
        json={"content_type": "image/jpeg", "size_bytes": 12},
        headers=machinist,
    )
    assert (presign.status_code, presign.json()) == (409, {"detail": "equipment.receipt_locked"})
    late = await api.post(
        f"{API}/shifts/{shift_id}/fuel/{fuel_id}/receipt/confirm",
        json={"key": second},
        headers=machinist,
    )
    assert (late.status_code, late.json()) == (409, {"detail": "equipment.receipt_locked"})
    techdir = await headers_for(api, "techdir")
    assert (await move(api, techdir, shift_id, "confirm")).status_code == 200
    after = await api.post(
        f"{API}/shifts/{shift_id}/fuel/{fuel_id}/receipt/confirm",
        json={"key": second},
        headers=machinist,
    )
    assert (after.status_code, after.json()) == (409, {"detail": "equipment.receipt_locked"})
    link = await api.get(f"{API}/shifts/{shift_id}/fuel/{fuel_id}/receipt", headers=finance)
    assert link.status_code == 200 and first in link.json()["url"]


async def test_a_foreman_who_did_not_write_the_sheet_gets_no_upload_address(
    api: AsyncClient, auth_world: AuthWorld, new_user: Any, worker_sync_engine: Engine
) -> None:
    """Пункт 8: право на чек проверяет БД до подписи: чужой прораб получает отказ, адреса нет."""
    finance = await headers_for(api, "finance")
    machinist, _, shift_id = await sheet(api, auth_world, finance)
    fuel_id = (await add_fuel(api, machinist, shift_id)).json()["id"]
    _, other = await signed_in(
        api,
        new_user,
        worker_sync_engine,
        "foreman",
        projects=(east(auth_world),),
        entities=(le_east(auth_world),),
    )
    refused = await api.post(
        f"{API}/shifts/{shift_id}/fuel/{fuel_id}/receipt",
        json={"content_type": "image/jpeg", "size_bytes": 12},
        headers=other,
    )
    assert (refused.status_code, refused.json()) == (403, {"detail": "forbidden"})
    assert "upload_url" not in refused.json()


async def test_the_application_cannot_write_the_receipt_columns_directly(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    """Пункт 8: колонки чека у приложения не записываются - только через определитель."""
    finance = await headers_for(api, "finance")
    machinist, _, shift_id = await sheet(api, auth_world, finance)
    fuel_id = (await add_fuel(api, machinist, shift_id)).json()["id"]
    ctx = context(
        auth_world,
        "foreman",
        (east(auth_world),),
        (le_east(auth_world),),
    )
    statements = (
        "UPDATE equipment_fuel SET receipt_key = 'x' WHERE id = CAST(:id AS uuid) RETURNING id",
        "UPDATE equipment_fuel SET receipt_content_type = 'image/jpeg'"
        " WHERE id = CAST(:id AS uuid) RETURNING id",
        "UPDATE equipment_fuel SET receipt_uploaded_by = gen_random_uuid()"
        " WHERE id = CAST(:id AS uuid) RETURNING id",
    )
    for statement in statements:
        with pytest.raises(DBAPIError, match="permission denied"):
            await rows(statement, ctx, {"id": fuel_id})


async def test_a_repair_is_never_inserted_as_done_only_the_closing_function_books_it(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    """Пункт 8: ремонт сразу в done отказан триггером; закрытие - только через определитель."""
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    unit = await own_unit(api, auth_world, head, finance)
    ctx = context(auth_world, "mechanic", (east(auth_world),), (le_east(auth_world),))
    with pytest.raises(DBAPIError, match=r"maintenance\.close_forbidden"):
        await rows(
            "INSERT INTO equipment_maintenance (id, legal_entity_id, equipment_unit_id, kind,"
            " title,"
            " planned_from, planned_to, status, done_at)"
            " VALUES (gen_random_uuid(), CAST(:le AS uuid), CAST(:unit AS uuid), 'repair', 'Тест',"
            " CURRENT_DATE, CURRENT_DATE, 'done', now()) RETURNING id",
            ctx,
            {"le": str(le_east(auth_world)), "unit": str(unit["id"])},
        )
    inserted = await rows(
        "INSERT INTO equipment_maintenance (id, legal_entity_id, equipment_unit_id, kind,"
        " title,"
        " planned_from, planned_to, status)"
        " VALUES (gen_random_uuid(), CAST(:le AS uuid), CAST(:unit AS uuid), 'repair', 'Тест',"
        " CURRENT_DATE, CURRENT_DATE, 'in_progress') RETURNING id",
        ctx,
        {"le": str(le_east(auth_world)), "unit": str(unit["id"])},
    )
    assert len(inserted) == 1


async def test_a_rate_with_an_end_date_leaves_the_choice_and_a_wrong_period_is_refused(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    """Пункт 6: окончание тарифа выводит его из выбора на поздние даты (неоднозначности нет)."""
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
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
    trip = await api.post(
        f"{API}/contracts/{contract}/rates",
        json={
            "unit": "trip",
            "with_operator": True,
            "amount": "333.33",
            "valid_from": "2026-05-01",
            "valid_to": "2026-05-10",
        },
        headers={**finance, **idem()},
    )
    assert trip.status_code == 201, trip.text
    foreman = await headers_for(api, "foreman")
    on_day = await post_shift(
        api,
        foreman,
        shift_body(
            unit_id=unit["id"],
            project_id=east(auth_world),
            work_date="2026-05-20",
            operator_id=operator,
        ),
    )
    assert on_day.status_code == 201, on_day.text
    submitted = await move(api, foreman, on_day.json()["id"], "submit")
    assert submitted.status_code == 200, submitted.text
    wrong = await api.post(
        f"{API}/contracts/{contract}/rates",
        json={
            "unit": "hour",
            "with_operator": False,
            "amount": "900.00",
            "valid_from": "2026-05-01",
            "valid_to": "2026-04-30",
        },
        headers={**finance, **idem()},
    )
    assert (wrong.status_code, wrong.json()) == (
        422,
        {"detail": "equipment.rate_period_invalid"},
    )


async def test_a_rate_with_postings_is_not_deleted_and_an_unused_one_is(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    """Пункт 6: мягкое удаление тарифа - только без проводок (409 иначе)."""
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
    spare = await new_unit(
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
    refused = await api.delete(f"{API}/contracts/{contract}/rates/{used['id']}", headers=finance)
    assert (refused.status_code, refused.json()) == (
        409,
        {"detail": "equipment.rate_has_postings"},
    )
    unit_rate = await api.post(
        f"{API}/contracts/{contract}/rates",
        json={
            "unit": "hour",
            "with_operator": True,
            "amount": "1700.00",
            "valid_from": RATE_FROM,
            "equipment_unit_id": str(spare["id"]),
        },
        headers={**finance, **idem()},
    )
    assert unit_rate.status_code == 201, unit_rate.text
    deleted = await api.delete(
        f"{API}/contracts/{contract}/rates/{unit_rate.json()['id']}", headers=finance
    )
    assert deleted.status_code == 204, deleted.text
    after = (await api.get(f"{API}/contracts/{contract}/rates", headers=finance)).json()
    assert unit_rate.json()["id"] not in [rate["id"] for rate in after]
    ended = await api.patch(
        f"{API}/contracts/{contract}/rates/{used['id']}",
        json={"valid_to": (bishkek_today() + timedelta(days=30)).isoformat()},
        headers={**finance, **idem()},
    )
    assert ended.status_code == 200, ended.text
    assert ended.json()["valid_to"] is not None
