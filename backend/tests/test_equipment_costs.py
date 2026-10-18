"""Деньги SE-2a: эталонный расчёт журнала построчно до тиына, правка техдиректора со сторно, ГСМ,
ремонт, итоги для прораба, неизменяемость журнала и граница юрлица (ADR-014 часть 1 §3, часть 2
§3-5, решение D13). Ожидаемые суммы посчитаны вручную по формулам ADR, а не взяты из кода."""

from collections import Counter
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.context import use_context
from app.core.db import get_sessionmaker
from app.modules.auth import devseed
from tests.auth_support import AuthWorld
from tests.equipment_support import (
    API,
    east,
    headers_for,
    idem,
    in_context,
    le_east,
    lessor,
    lessor_contract,
    lessor_operator,
    money_keys,
    move,
    new_type,
    new_unit,
    own_operator,
    own_unit,
    past,
    post_shift,
    rows,
    second_project,
    shift_body,
    signed_in,
)

WINDOW = {"date_from": "2026-01-01", "date_to": "2100-12-31"}


def money(value: Any) -> str | None:
    return None if value is None else format(Decimal(str(value)).quantize(Decimal("0.001")), "f")


async def journal(engine: AsyncEngine, unit_ids: list[str]) -> Counter[tuple[Any, ...]]:
    """Строки журнала единиц как есть (BYPASSRLS): сверка с эталоном без вмешательства политик."""
    async with engine.connect() as connection:
        result = await connection.execute(
            text(
                "SELECT equipment_unit_id, project_id, category, posting_version, quantity, rate,"
                " amount, reversal_of_id FROM cost_entry"
                " WHERE equipment_unit_id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": unit_ids},
        )
        return Counter(
            (
                str(unit),
                str(project) if project else None,
                category,
                version,
                money(quantity),
                money(rate),
                money(amount),
                reversal is not None,
            )
            for unit, project, category, version, quantity, rate, amount, reversal in result
        )


async def confirmed(
    client: AsyncClient, author: dict[str, str], approver: dict[str, str], body: dict[str, Any]
) -> dict[str, Any]:
    created = await post_shift(client, author, body)
    assert created.status_code == 201, created.text
    shift = created.json()
    assert (await move(client, author, shift["id"], "submit")).status_code == 200
    done = await move(client, approver, shift["id"], "confirm")
    assert done.status_code == 200, done.text
    return done.json()


async def reference_world(
    api: AsyncClient, auth_world: AuthWorld, new_user: Any, worker_sync_engine: Engine
) -> dict[str, Any]:
    """Эталонный стенд: своя техника, три аренды (с машинистом и без, часы/рейсы/моточасы), два
    объекта одного юрлица, один день на двух объектах; подтверждает техдиректор, вводит прораб."""
    le = le_east(auth_world)
    extra = second_project(worker_sync_engine, le)
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    author_id, author = await signed_in(
        api,
        new_user,
        worker_sync_engine,
        "foreman",
        projects=(east(auth_world), extra),
        entities=(le,),
    )
    staff = await own_operator(api, await headers_for(api, "machinist"))
    lessee = await lessor_operator(api, head, le, lessor(auth_world, "А"), "Машинист арендодателя")
    own = await own_unit(api, auth_world, head, finance)
    rent_contract = await lessor_contract(
        api,
        finance,
        le,
        lessor(auth_world, "А"),
        [
            {"unit": "hour", "with_operator": True, "amount": "1500.00"},
            {"unit": "hour", "with_operator": False, "amount": "900.00"},
        ],
    )
    rent = await new_unit(
        api,
        head,
        auth_world,
        code=await new_type(api, head),
        ownership="rented",
        contract_id=rent_contract,
    )
    trip_contract = await lessor_contract(
        api,
        finance,
        le,
        lessor(auth_world, "Б"),
        [{"unit": "trip", "with_operator": False, "amount": "333.33"}],
    )
    trip = await new_unit(
        api,
        head,
        auth_world,
        code=await new_type(api, head),
        ownership="rented",
        contract_id=trip_contract,
    )
    motor_contract = await lessor_contract(
        api,
        finance,
        le,
        lessor(auth_world, "В"),
        [{"unit": "motor_hour", "with_operator": True, "amount": "166.65"}],
    )
    motor = await new_unit(
        api,
        head,
        auth_world,
        code=await new_type(api, head),
        ownership="rented",
        contract_id=motor_contract,
    )
    techdir = await headers_for(api, "techdir")
    east_id, extra_id = east(auth_world), extra

    def body(
        unit: dict[str, Any], project: UUID, day: int, operator: str, **extra_fields: Any
    ) -> dict[str, Any]:
        return shift_body(
            unit_id=unit["id"],
            project_id=project,
            work_date=past(day),
            operator_id=operator,
            **extra_fields,
        )

    await confirmed(api, author, techdir, body(own, east_id, 1, staff, hours="8"))
    await confirmed(api, author, techdir, body(own, extra_id, 1, staff, hours="6.5"))
    await confirmed(
        api,
        author,
        techdir,
        body(own, east_id, 3, staff, hours="8", lines=[{"hours": "5"}, {"hours": "3"}]),
    )
    s3 = await confirmed(
        api,
        author,
        techdir,
        body(
            rent,
            east_id,
            2,
            lessee,
            hours="10",
            downtime_hours="2",
            downtime_reason="ожидание бетона",
        ),
    )
    await confirmed(api, author, techdir, body(rent, extra_id, 2, staff, hours="7.5"))
    await confirmed(api, author, techdir, body(trip, east_id, 4, staff, hours="9", trips=7))
    await confirmed(
        api,
        author,
        techdir,
        body(
            motor,
            east_id,
            5,
            lessee,
            hours="10",
            odometer_from="100.00",
            odometer_to="107.50",
            lines=[{"hours": "3"}, {"hours": "7"}],
        ),
    )
    fuel_extra = await api.post(
        f"{API}/fuel",
        json={
            "equipment_unit_id": str(own["id"]),
            "project_id": str(extra_id),
            "fuel_date": past(1),
            "liters": "120.50",
            "amount": "12500.50",
        },
        headers={**author, **idem()},
    )
    assert fuel_extra.status_code == 201, fuel_extra.text
    fuel_overhead = await api.post(
        f"{API}/fuel",
        json={"equipment_unit_id": str(own["id"]), "fuel_date": past(1), "amount": "3000.00"},
        headers={**head, **idem()},
    )
    assert fuel_overhead.status_code == 201, fuel_overhead.text
    mechanic = await headers_for(api, "mechanic")
    repairs = []
    for amount, project in (("45000.00", extra_id), ("1000.00", None)):
        created = await api.post(
            f"{API}/maintenance",
            json={
                "equipment_unit_id": str(own["id"]),
                "kind": "repair",
                "title": "Гидравлика",
                "planned_from": past(2),
            },
            headers={**mechanic, **idem()},
        )
        assert created.status_code == 201, created.text
        started = await api.patch(
            f"{API}/maintenance/{created.json()['id']}",
            json={"status": "in_progress"},
            headers=mechanic,
        )
        assert started.status_code == 200, started.text
        closing: dict[str, Any] = {"status": "done", "amount": amount}
        if project is not None:
            closing["project_id"] = str(project)
        done = await api.patch(
            f"{API}/maintenance/{created.json()['id']}", json=closing, headers=mechanic
        )
        assert done.status_code == 200 and "amount" not in done.json(), done.text
        repairs.append(created.json()["id"])
    correction = await api.patch(f"{API}/shifts/{s3['id']}", json={"hours": "9"}, headers=techdir)
    assert correction.status_code == 200, correction.text
    reversed_once = await api.post(
        f"{API}/fuel/{fuel_overhead.json()['id']}/reverse",
        json={"reason": "ошибка чека"},
        headers=techdir,
    )
    assert reversed_once.status_code == 200 and reversed_once.json()["reversed_at"]
    reversed_again = await api.post(
        f"{API}/fuel/{fuel_overhead.json()['id']}/reverse",
        json={"reason": "ошибка чека"},
        headers=techdir,
    )
    assert reversed_again.status_code == 200
    return {
        "author_id": author_id,
        "author": author,
        "techdir": techdir,
        "finance": finance,
        "own": own,
        "rent": rent,
        "trip": trip,
        "motor": motor,
        "east": east_id,
        "extra": extra_id,
        "s3": s3["id"],
        "fuel_overhead": fuel_overhead.json()["id"],
        "repairs": repairs,
    }


async def test_the_journal_matches_the_reference_line_by_line_to_the_tiyn(
    api: AsyncClient,
    auth_world: AuthWorld,
    new_user: Any,
    worker_sync_engine: Engine,
    worker_engine: AsyncEngine,
) -> None:
    world = await reference_world(api, auth_world, new_user, worker_sync_engine)
    own, rent, trip, motor = (str(world[key]["id"]) for key in ("own", "rent", "trip", "motor"))
    east_id, extra_id = str(world["east"]), str(world["extra"])
    expected = Counter(
        [
            (own, east_id, "equipment_own", 1, money(8), money(1000), money(8000), False),
            (own, extra_id, "equipment_own", 1, money("6.5"), money(1000), money(6500), False),
            (own, east_id, "equipment_own", 1, money(5), money(1000), money(5000), False),
            (own, east_id, "equipment_own", 1, money(3), money(1000), money(3000), False),
            (own, extra_id, "equipment_fuel", 1, money("120.50"), None, money("12500.50"), False),
            (own, None, "equipment_fuel", 1, None, None, money(3000), False),
            (own, None, "equipment_fuel", 1, None, None, money(-3000), True),
            (own, extra_id, "equipment_repair", 1, None, None, money(45000), False),
            (own, None, "equipment_repair", 1, None, None, money(1000), False),
            (rent, east_id, "equipment_rental", 1, money(10), money(1500), money(15000), False),
            (rent, east_id, "equipment_rental", 1, money(-10), money(1500), money(-15000), True),
            (rent, east_id, "equipment_rental", 2, money(9), money(1500), money(13500), False),
            (rent, extra_id, "equipment_rental", 1, money("7.5"), money(900), money(6750), False),
            (
                trip,
                east_id,
                "equipment_rental",
                1,
                money(7),
                money("333.33"),
                money("2333.31"),
                False,
            ),
            (
                motor,
                east_id,
                "equipment_rental",
                1,
                money("2.25"),
                money("166.65"),
                money("374.96"),
                False,
            ),
            (
                motor,
                east_id,
                "equipment_rental",
                1,
                money("5.25"),
                money("166.65"),
                money("874.91"),
                False,
            ),
        ]
    )
    assert await journal(worker_engine, [own, rent, trip, motor]) == expected


async def test_totals_for_the_foreman_hold_no_money_at_any_depth_and_no_rows_or_tariffs(
    api: AsyncClient,
    auth_world: AuthWorld,
    new_user: Any,
    worker_sync_engine: Engine,
) -> None:
    """Q11 C: прораб (approve:shift без read:rates) получает количества, но ни одной суммы."""
    world = await reference_world(api, auth_world, new_user, worker_sync_engine)
    author = world["author"]
    window = {**WINDOW, "project_id": str(world["extra"])}
    totals = await api.get(f"{API}/costs/totals", params=window, headers=author)
    assert (totals.status_code, totals.json()) == (200, [])
    everything = await api.get(f"{API}/costs/totals", params=WINDOW, headers=author)
    assert (everything.status_code, everything.json()) == (200, [])
    usage = await api.get(f"{API}/usage/totals", params=window, headers=author)
    assert usage.status_code == 200, usage.text
    assert usage.json() != [] and money_keys(usage.json()) == set()
    fuel = await api.get(
        f"{API}/fuel", params={**WINDOW, "project_id": str(world["extra"])}, headers=author
    )
    assert fuel.status_code == 200 and money_keys(fuel.json()) == set()
    techdir = await headers_for(api, "techdir")
    priced = await api.get(f"{API}/costs/totals", params=window, headers=techdir)
    assert {(row["category"], money(row["amount"]), row["entries"]) for row in priced.json()} == {
        ("equipment_own", money(6500), 1),
        ("equipment_rental", money(6750), 1),
        ("equipment_fuel", money("12500.50"), 1),
        ("equipment_repair", money(45000), 1),
    }
    rows_for_foreman = await api.get(
        f"{API}/costs/entries", params={**WINDOW, "limit": 500}, headers=author
    )
    assert rows_for_foreman.status_code == 403
    ctx = in_context(world["author_id"], (east(auth_world), world["extra"]), (le_east(auth_world),))
    assert await rows("SELECT id FROM cost_entry", ctx) == []
    assert (
        await rows(
            "SELECT category FROM app.equipment_cost_totals("
            "DATE '2026-01-01', DATE '2100-12-31', NULL)",
            ctx,
        )
        == []
    )
    # Сумма ГСМ приложению не выдана колонкой (ревизия f2b7d4c9e1a6): отказ, а не строка с нулём.
    with pytest.raises(DBAPIError, match="permission denied for table equipment_fuel"):
        await rows("SELECT amount FROM equipment_fuel", ctx)
    assert await rows("SELECT liters FROM equipment_fuel", ctx) != []


async def test_finance_reads_every_journal_row_of_the_reference_and_the_shift_change_is_audited(
    api: AsyncClient,
    auth_world: AuthWorld,
    new_user: Any,
    worker_sync_engine: Engine,
    worker_engine: AsyncEngine,
) -> None:
    world = await reference_world(api, auth_world, new_user, worker_sync_engine)
    listed = await api.get(
        f"{API}/costs/entries", params={**WINDOW, "limit": 500}, headers=world["finance"]
    )
    assert listed.status_code == 200, listed.text
    ids = {str(world[key]["id"]) for key in ("own", "rent", "trip", "motor")}
    assert len([row for row in listed.json() if row["equipment_unit_id"] in ids]) == 16
    async with worker_engine.connect() as connection:
        updates = await connection.scalar(
            text(
                "SELECT count(*) FROM audit_log WHERE table_name = 'equipment_shift'"
                " AND row_id = CAST(:id AS uuid) AND action = 'update'"
            ),
            {"id": world["s3"]},
        )
    assert updates >= 1


async def test_a_fuel_reversal_needs_the_after_approval_right(
    api: AsyncClient, auth_world: AuthWorld, new_user: Any, worker_sync_engine: Engine
) -> None:
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    unit = await own_unit(api, auth_world, head, finance)
    _, author = await signed_in(
        api,
        new_user,
        worker_sync_engine,
        "foreman",
        projects=(east(auth_world),),
        entities=(le_east(auth_world),),
    )
    head = await headers_for(api, "equipment_head")
    fuel = await api.post(
        f"{API}/fuel",
        json={"equipment_unit_id": str(unit["id"]), "fuel_date": past(9), "amount": "250.00"},
        headers={**head, **idem()},
    )
    assert fuel.status_code == 201, fuel.text
    denied = await api.post(
        f"{API}/fuel/{fuel.json()['id']}/reverse",
        json={"reason": "ошибка"},
        headers=author,
    )
    assert denied.status_code == 403 and denied.json() == {"detail": "forbidden"}
    blank = await api.post(
        f"{API}/fuel/{fuel.json()['id']}/reverse", json={"reason": " "}, headers=finance
    )
    assert blank.status_code == 422


async def test_a_repair_closes_only_with_an_amount_and_the_mechanic_never_reads_money(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    mechanic = await headers_for(api, "mechanic")
    unit = await own_unit(api, auth_world, head, finance)
    created = await api.post(
        f"{API}/maintenance",
        json={
            "equipment_unit_id": str(unit["id"]),
            "kind": "repair",
            "title": "Двигатель",
            "planned_from": past(10),
        },
        headers={**mechanic, **idem()},
    )
    maintenance = created.json()["id"]
    await api.patch(
        f"{API}/maintenance/{maintenance}", json={"status": "in_progress"}, headers=mechanic
    )
    no_amount = await api.patch(
        f"{API}/maintenance/{maintenance}", json={"status": "done"}, headers=mechanic
    )
    assert no_amount.json() == {"detail": "equipment.repair_amount_required"}
    money_read = await api.get(f"{API}/costs/entries", params=WINDOW, headers=mechanic)
    assert money_read.status_code == 403
    maintenance_view = await api.get(
        f"{API}/maintenance", params={"unit_id": str(unit["id"])}, headers=mechanic
    )
    assert maintenance_view.status_code == 200
    assert all("amount" not in row for row in maintenance_view.json())


async def test_a_service_visit_closes_without_an_amount_and_books_nothing(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    mechanic = await headers_for(api, "mechanic")
    unit = await own_unit(api, auth_world, head, finance)
    created = await api.post(
        f"{API}/maintenance",
        json={
            "equipment_unit_id": str(unit["id"]),
            "kind": "to",
            "title": "ТО-1",
            "planned_from": past(11),
        },
        headers={**mechanic, **idem()},
    )
    done = await api.patch(
        f"{API}/maintenance/{created.json()['id']}", json={"status": "done"}, headers=mechanic
    )
    assert done.status_code == 200 and done.json()["status"] == "done"
    assert await journal(worker_engine, [str(unit["id"])]) == Counter()


async def test_the_journal_refuses_every_change_and_keeps_its_guard(
    api: AsyncClient,
    auth_world: AuthWorld,
    new_user: Any,
    worker_sync_engine: Engine,
    worker_engine: AsyncEngine,
    owner_engine: AsyncEngine,
) -> None:
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    unit = await own_unit(api, auth_world, head, finance)
    author_id, _ = await signed_in(
        api,
        new_user,
        worker_sync_engine,
        "foreman",
        projects=(east(auth_world),),
        entities=(le_east(auth_world),),
    )
    head = await headers_for(api, "equipment_head")
    fuel = await api.post(
        f"{API}/fuel",
        json={"equipment_unit_id": str(unit["id"]), "fuel_date": past(12), "amount": "99.00"},
        headers={**head, **idem()},
    )
    assert fuel.status_code == 201, fuel.text
    async with worker_engine.connect() as connection:
        cost_id = await connection.scalar(
            text(
                "SELECT id FROM cost_entry WHERE source_type = 'equipment_fuel'"
                " AND source_id = CAST(:fuel AS uuid)"
            ),
            {"fuel": fuel.json()["id"]},
        )
    changes = (
        "UPDATE cost_entry SET amount = 0 WHERE id = CAST(:id AS uuid)",
        "DELETE FROM cost_entry WHERE id = CAST(:id AS uuid)",
    )
    async with worker_engine.connect() as connection:
        for statement in changes:
            with pytest.raises(DBAPIError, match="permission denied"):
                await connection.execute(text(statement), {"id": cost_id})
            await connection.rollback()
    ctx = in_context(author_id, (east(auth_world),), (le_east(auth_world),))
    with use_context(ctx):
        async with get_sessionmaker()() as session:
            for statement in changes:
                with pytest.raises(DBAPIError, match="permission denied"):
                    await session.execute(text(statement), {"id": cost_id})
                await session.rollback()
    async with owner_engine.connect() as connection:
        guarded = await connection.scalar(
            text(
                "SELECT count(*) FROM pg_trigger WHERE tgname = 'cost_entry_forbid_change'"
                " AND tgrelid = 'cost_entry'::regclass"
            )
        )
    assert guarded == 1


async def test_overhead_money_stays_inside_its_legal_entity(
    api: AsyncClient,
    auth_world: AuthWorld,
    new_user: Any,
    worker_sync_engine: Engine,
    worker_engine: AsyncEngine,
) -> None:
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    west_unit = await new_unit(
        api,
        head,
        auth_world,
        legal_entity=auth_world.legal_entities[devseed.LE_WEST],
    )
    east_unit = await own_unit(api, auth_world, head, finance)
    for unit, legal_entity, amount in (
        (west_unit, auth_world.legal_entities[devseed.LE_WEST], "555.00"),
        (east_unit, le_east(auth_world), "777.00"),
    ):
        async with worker_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO equipment_fuel (id, legal_entity_id, project_id,"
                    " equipment_unit_id, fuel_date, amount, source)"
                    " VALUES (gen_random_uuid(), :le, NULL, CAST(:unit AS uuid), :day, :amount,"
                    " 'manual')"
                ),
                {
                    "le": legal_entity,
                    "unit": unit["id"],
                    "day": past(13),
                    "amount": amount,
                },
            )
    lawyer_id, lawyer = await signed_in(
        api, new_user, worker_sync_engine, "lawyer", entities=(le_east(auth_world),)
    )
    listed = await api.get(f"{API}/costs/entries", params={**WINDOW, "limit": 500}, headers=lawyer)
    assert listed.status_code == 200, listed.text
    units = {row["equipment_unit_id"] for row in listed.json()}
    assert east_unit["id"] in units and west_unit["id"] not in units
    ctx = in_context(lawyer_id, (), (le_east(auth_world),))
    by_unit = "SELECT id FROM cost_entry WHERE equipment_unit_id = CAST(:unit AS uuid)"
    assert await rows(by_unit, ctx, {"unit": west_unit["id"]}) == []
    assert await rows(by_unit, ctx, {"unit": east_unit["id"]}) != []


async def test_the_audit_journal_shows_no_money_rows_without_the_rate_right(
    api: AsyncClient,
    auth_world: AuthWorld,
    new_user: Any,
    worker_sync_engine: Engine,
) -> None:
    """F1 (ADR-015 часть 3, раздел 13): строки журнала по деньгам (проводки, ГСМ) с суммами
    видны только с equipment:read:rates. Прораб читает журнал объекта по equipment:read,
    но суммы - нет."""
    await reference_world(api, auth_world, new_user, worker_sync_engine)
    money_rows = (
        "SELECT count(*) FROM audit_log WHERE table_name IN ('cost_entry', 'equipment_fuel')"
        " AND (new ? 'amount' OR old ? 'amount')"
    )
    foreman = in_context(auth_world.users["foreman"], (east(auth_world),), (le_east(auth_world),))
    finance = in_context(
        auth_world.users["finance"],
        tuple(auth_world.projects.values()),
        tuple(auth_world.legal_entities.values()),
    )
    assert await rows(money_rows, foreman) == [(0,)]
    assert (await rows(money_rows, finance))[0][0] > 0
