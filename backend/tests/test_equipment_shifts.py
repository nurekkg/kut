"""Путевые листы SE-2a: переходы, автор, объекты, суточный лимит, делегат, видимость.

ADR-014 часть 1 §3, часть 2 §5, часть 3 §9. Даты листов в прошлом: лист не может быть позже сегодня.
"""

from datetime import timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine

from app.modules.auth import devseed
from app.modules.equipment.service import bishkek_today
from tests.auth_support import AuthWorld
from tests.equipment_support import (
    API,
    assign,
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
    own_operator,
    own_unit,
    past,
    post_shift,
    rows,
    second_project,
    shift_body,
    signed_in,
)


async def journal_count(engine: AsyncEngine, shift_id: str) -> int:
    async with engine.connect() as connection:
        return int(
            await connection.scalar(
                text(
                    "SELECT count(*) FROM cost_entry WHERE source_type = 'equipment_shift'"
                    " AND source_id = CAST(:id AS uuid)"
                ),
                {"id": shift_id},
            )
        )


async def test_a_shift_moves_draft_submitted_confirmed_and_a_repeated_confirm_posts_nothing(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    techdir = await headers_for(api, "techdir")
    foreman = await headers_for(api, "foreman")
    unit = await own_unit(api, auth_world, head, finance)
    operator = await own_operator(api, await headers_for(api, "machinist"))
    created = await post_shift(
        api,
        foreman,
        shift_body(
            unit_id=unit["id"], project_id=east(auth_world), work_date=past(0), operator_id=operator
        ),
    )
    assert created.status_code == 201, created.text
    shift = created.json()
    assert shift["status"] == "draft" and shift["posting_version"] == 0
    assert shift["unplanned"] is True
    early = await move(api, techdir, shift["id"], "confirm")
    assert early.json() == {"detail": "equipment.shift_transition_forbidden"}
    submitted = await move(api, foreman, shift["id"], "submit")
    assert submitted.status_code == 200 and submitted.json()["status"] == "submitted"
    confirmed = await move(api, techdir, shift["id"], "confirm")
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["status"] == "confirmed" and body["posting_version"] == 1
    assert body["confirmed_by"] == str(auth_world.users["techdir"])
    assert await journal_count(worker_engine, shift["id"]) == 1
    again = await move(api, techdir, shift["id"], "confirm")
    assert again.status_code == 200 and again.json()["posting_version"] == 1
    assert await journal_count(worker_engine, shift["id"]) == 1


async def test_the_author_of_a_shift_never_confirms_it(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    foreman = await headers_for(api, "foreman")
    unit = await own_unit(api, auth_world, head, finance)
    operator = await own_operator(api, await headers_for(api, "machinist"))
    shift = (
        await post_shift(
            api,
            foreman,
            shift_body(
                unit_id=unit["id"],
                project_id=east(auth_world),
                work_date=past(1),
                operator_id=operator,
            ),
        )
    ).json()
    assert (await move(api, foreman, shift["id"], "submit")).status_code == 200
    own = await move(api, foreman, shift["id"], "confirm")
    assert own.json() == {"detail": "equipment.shift_transition_forbidden"}
    assert await journal_count(worker_engine, shift["id"]) == 0


async def test_a_writer_who_does_not_approve_cannot_confirm_and_gets_403(
    api: AsyncClient, auth_world: AuthWorld, new_user: Any, worker_sync_engine: Engine
) -> None:
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    foreman = await headers_for(api, "foreman")
    unit = await own_unit(api, auth_world, head, finance)
    operator = await own_operator(api, await headers_for(api, "machinist"))
    _, master = await signed_in(
        api,
        new_user,
        worker_sync_engine,
        "master",
        projects=(east(auth_world),),
        entities=(le_east(auth_world),),
    )
    shift = (
        await post_shift(
            api,
            foreman,
            shift_body(
                unit_id=unit["id"],
                project_id=east(auth_world),
                work_date=past(2),
                operator_id=operator,
            ),
        )
    ).json()
    assert (await move(api, foreman, shift["id"], "submit")).status_code == 200
    denied = await move(api, master, shift["id"], "confirm")
    assert denied.status_code == 403 and denied.json() == {"detail": "forbidden"}


async def test_one_unit_on_two_objects_of_one_legal_entity_shares_a_24_hour_day(
    api: AsyncClient, auth_world: AuthWorld, new_user: Any, worker_sync_engine: Engine
) -> None:
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    extra = second_project(worker_sync_engine, le_east(auth_world))
    unit = await own_unit(api, auth_world, head, finance)
    operator = await own_operator(api, await headers_for(api, "machinist"))
    _, author = await signed_in(
        api,
        new_user,
        worker_sync_engine,
        "foreman",
        projects=(east(auth_world), extra),
        entities=(le_east(auth_world),),
    )

    def body(project: Any, hours: str) -> dict[str, Any]:
        return shift_body(
            unit_id=unit["id"],
            project_id=project,
            work_date=past(3),
            operator_id=operator,
            hours=hours,
        )

    first = await post_shift(api, author, body(east(auth_world), "14"))
    assert first.status_code == 201, first.text
    over = await post_shift(api, author, body(extra, "12"))
    assert over.status_code == 422 and over.json() == {"detail": "equipment.day_hours_exceeded"}
    fits = await post_shift(api, author, body(extra, "10"))
    assert fits.status_code == 201, fits.text


async def test_two_operators_on_one_unit_share_the_same_day_limit(
    api: AsyncClient, auth_world: AuthWorld, new_user: Any, worker_sync_engine: Engine
) -> None:
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    extra = second_project(worker_sync_engine, le_east(auth_world))
    unit = await own_unit(api, auth_world, head, finance)
    staff = await own_operator(api, await headers_for(api, "machinist"))
    lessee = await lessor_operator(
        api, head, le_east(auth_world), lessor(auth_world, "А"), "Машинист арендодателя"
    )
    _, author = await signed_in(
        api,
        new_user,
        worker_sync_engine,
        "foreman",
        projects=(east(auth_world), extra),
        entities=(le_east(auth_world),),
    )

    def body(operator: str, project: Any, hours: str) -> dict[str, Any]:
        return shift_body(
            unit_id=unit["id"],
            project_id=project,
            work_date=past(4),
            operator_id=operator,
            hours=hours,
        )

    assert (await post_shift(api, author, body(staff, east(auth_world), "12"))).status_code == 201
    assert (await post_shift(api, author, body(lessee, extra, "12"))).status_code == 201
    over = await post_shift(api, author, body(lessee, extra, "1"))
    assert over.json() == {"detail": "equipment.day_hours_exceeded"}


async def test_a_trip_rental_needs_trips_before_the_shift_is_submitted(
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
        api,
        head,
        auth_world,
        code=code,
        ownership="rented",
        contract_id=contract,
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
                work_date=past(5),
                operator_id=operator,
                hours="9",
            ),
        )
    ).json()
    missing = await move(api, author, shift["id"], "submit")
    assert missing.json() == {"detail": "equipment.shift_quantity_missing"}
    trips = await api.patch(f"{API}/shifts/{shift['id']}", json={"trips": 7}, headers=author)
    assert trips.status_code == 200, trips.text
    submitted = await move(api, author, shift["id"], "submit")
    assert submitted.status_code == 200, submitted.text


async def test_the_lines_must_add_up_to_the_shift_total(
    api: AsyncClient, auth_world: AuthWorld, new_user: Any, worker_sync_engine: Engine
) -> None:
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    unit = await own_unit(api, auth_world, head, finance)
    operator = await own_operator(api, await headers_for(api, "machinist"))
    _, author = await signed_in(
        api,
        new_user,
        worker_sync_engine,
        "foreman",
        projects=(east(auth_world),),
        entities=(le_east(auth_world),),
    )

    def body(lines: list[dict[str, str]]) -> dict[str, Any]:
        return shift_body(
            unit_id=unit["id"],
            project_id=east(auth_world),
            work_date=past(6),
            operator_id=operator,
            hours="8",
            lines=lines,
        )

    short = await post_shift(api, author, body([{"hours": "5"}, {"hours": "2"}]))
    assert short.status_code == 422 and short.json() == {"detail": "equipment.shift_lines_mismatch"}
    exact = await post_shift(api, author, body([{"hours": "5"}, {"hours": "3"}]))
    assert exact.status_code == 201, exact.text
    assert sorted(line["hours"] for line in exact.json()["lines"]) == ["3.00", "5.00"]


async def test_a_batch_is_one_transaction_and_one_idempotency_key(
    api: AsyncClient, auth_world: AuthWorld, new_user: Any, worker_sync_engine: Engine
) -> None:
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    extra = second_project(worker_sync_engine, le_east(auth_world))
    unit = await own_unit(api, auth_world, head, finance)
    operator = await own_operator(api, await headers_for(api, "machinist"))
    _, author = await signed_in(
        api,
        new_user,
        worker_sync_engine,
        "foreman",
        projects=(east(auth_world), extra),
        entities=(le_east(auth_world),),
    )
    items = [
        shift_body(
            unit_id=unit["id"],
            project_id=east(auth_world),
            work_date=past(7),
            operator_id=operator,
            hours="4",
        ),
        shift_body(
            unit_id=unit["id"], project_id=extra, work_date=past(7), operator_id=operator, hours="5"
        ),
    ]
    key = idem()
    first = await api.post(f"{API}/shifts/batch", json={"items": items}, headers={**author, **key})
    assert first.status_code == 201, first.text
    replay = await api.post(f"{API}/shifts/batch", json={"items": items}, headers={**author, **key})
    assert replay.json() == first.json()
    listed = await api.get(
        f"{API}/shifts",
        params={"unit_id": unit["id"], "date_from": past(7), "date_to": past(7)},
        headers=author,
    )
    assert len(listed.json()) == 2
    too_many = await api.post(
        f"{API}/shifts/batch",
        json={"items": [items[0]] * 21},
        headers={**author, **idem()},
    )
    assert too_many.status_code == 422


async def test_a_machinist_sees_and_enters_only_their_own_operator(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    machinist = await headers_for(api, "machinist")
    mine_id = await own_operator(api, machinist)
    other_id = await lessor_operator(
        api, head, le_east(auth_world), lessor(auth_world, "В"), "Арендный машинист"
    )
    unit = await own_unit(api, auth_world, head, finance)
    linked = await assign(
        api,
        head,
        unit_id=unit["id"],
        project_id=east(auth_world),
        start=10,
        end=12,
        hours="8",
        operator_id=mine_id,
    )
    assert linked.status_code == 201, linked.text
    mine = await post_shift(
        api,
        machinist,
        shift_body(
            unit_id=unit["id"],
            project_id=east(auth_world),
            work_date=past(8),
            operator_id=mine_id,
            hours="6",
        ),
    )
    assert mine.status_code == 201, mine.text
    theirs = await post_shift(
        api,
        machinist,
        shift_body(
            unit_id=unit["id"],
            project_id=east(auth_world),
            work_date=past(8),
            operator_id=other_id,
            hours="2",
        ),
    )
    assert theirs.status_code == 403 and theirs.json() == {"detail": "forbidden"}
    listed = (await api.get(f"{API}/shifts", headers=machinist)).json()
    assert listed and all(item["operator_id"] == mine_id for item in listed)
    assert mine.json()["id"] in {item["id"] for item in listed}


async def test_a_delegate_confirms_inside_the_period_and_nowhere_else(
    api: AsyncClient, auth_world: AuthWorld, new_user: Any, worker_sync_engine: Engine
) -> None:
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    foreman = await headers_for(api, "foreman")
    unit = await own_unit(api, auth_world, head, finance)
    operator = await own_operator(api, await headers_for(api, "machinist"))
    delegate_id, delegate = await signed_in(
        api,
        new_user,
        worker_sync_engine,
        "master",
        projects=(east(auth_world),),
        entities=(le_east(auth_world),),
    )
    _, master = await signed_in(
        api,
        new_user,
        worker_sync_engine,
        "master",
        projects=(east(auth_world),),
        entities=(le_east(auth_world),),
    )
    machinist_id = auth_world.users["machinist"]
    not_eligible = await api.post(
        f"{API}/delegates",
        json={
            "project_id": str(east(auth_world)),
            "delegate_user_id": str(machinist_id),
            "date_from": bishkek_today().isoformat(),
            "date_to": (bishkek_today() + timedelta(days=2)).isoformat(),
            "reason": "машинист не подтверждает свои часы",
        },
        headers={**foreman, **idem()},
    )
    assert not_eligible.json() == {"detail": "equipment.delegate_not_eligible"}
    created = await api.post(
        f"{API}/delegates",
        json={
            "project_id": str(east(auth_world)),
            "delegate_user_id": str(delegate_id),
            "date_from": bishkek_today().isoformat(),
            "date_to": (bishkek_today() + timedelta(days=2)).isoformat(),
            "reason": "отпуск прораба",
        },
        headers={**foreman, **idem()},
    )
    assert created.status_code == 201, created.text
    by_delegate = await api.post(
        f"{API}/delegates",
        json={
            "project_id": str(east(auth_world)),
            "delegate_user_id": str(delegate_id),
            "date_from": bishkek_today().isoformat(),
            "date_to": (bishkek_today() + timedelta(days=2)).isoformat(),
            "reason": "самоназначение",
        },
        headers={**master, **idem()},
    )
    assert by_delegate.status_code == 403

    def shift(work_date: str) -> dict[str, Any]:
        return shift_body(
            unit_id=unit["id"],
            project_id=east(auth_world),
            work_date=work_date,
            operator_id=operator,
        )

    inside = (await post_shift(api, foreman, shift(bishkek_today().isoformat()))).json()
    outside = (await post_shift(api, foreman, shift(past(20)))).json()
    for item in (inside, outside):
        assert (await move(api, foreman, item["id"], "submit")).status_code == 200
    assert (await move(api, delegate, inside["id"], "confirm")).status_code == 200
    # Право - по дате подтверждения (сегодня, Бишкек): лист за прошлый день, подтверждённый внутри
    # периода делегата, проходит (решение PO 2026-10-13, пункт 2).
    assert (await move(api, delegate, outside["id"], "confirm")).status_code == 200
    # Делегирование отозвано: тот же делегат и тот же день - отказ.
    revoked = await api.post(f"{API}/delegates/{created.json()['id']}/revoke", headers=foreman)
    assert revoked.status_code == 200, revoked.text
    ended = (await post_shift(api, foreman, shift(past(21)))).json()
    assert (await move(api, foreman, ended["id"], "submit")).status_code == 200
    late = await move(api, delegate, ended["id"], "confirm")
    assert late.status_code == 403 and late.json() == {"detail": "forbidden"}


async def test_a_delegation_cannot_start_before_today(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    """Делегирование задним числом закрыто: период не раньше сегодня (закрытие месяца - SE-3)."""
    foreman = await headers_for(api, "foreman")
    yesterday = (bishkek_today() - timedelta(days=1)).isoformat()
    refused = await api.post(
        f"{API}/delegates",
        json={
            "project_id": str(east(auth_world)),
            "delegate_user_id": str(auth_world.users["master"]),
            "date_from": yesterday,
            "date_to": bishkek_today().isoformat(),
            "reason": "задним числом",
        },
        headers={**foreman, **idem()},
    )
    assert (refused.status_code, refused.json()) == (422, {"detail": "equipment.delegate_in_past"})


async def test_another_objects_foreman_neither_sees_nor_confirms_the_shift(
    api: AsyncClient, auth_world: AuthWorld, new_user: Any, worker_sync_engine: Engine
) -> None:
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    foreman = await headers_for(api, "foreman")
    extra = second_project(worker_sync_engine, le_east(auth_world))
    unit = await own_unit(api, auth_world, head, finance)
    operator = await own_operator(api, await headers_for(api, "machinist"))
    _, other = await signed_in(
        api,
        new_user,
        worker_sync_engine,
        "foreman",
        projects=(extra,),
        entities=(le_east(auth_world),),
    )
    shift = (
        await post_shift(
            api,
            other,
            shift_body(
                unit_id=unit["id"], project_id=extra, work_date=past(13), operator_id=operator
            ),
        )
    ).json()
    assert (await api.get(f"{API}/shifts/{shift['id']}", headers=foreman)).status_code == 404
    assert (await move(api, foreman, shift["id"], "submit")).status_code == 404
    listed = (
        await api.get(f"{API}/shifts", params={"unit_id": unit["id"]}, headers=foreman)
    ).json()
    assert shift["id"] not in {item["id"] for item in listed}
    ctx = context(auth_world, "foreman", (east(auth_world),), (le_east(auth_world),))
    visible = await rows(
        "SELECT id FROM equipment_shift WHERE id = CAST(:id AS uuid)", ctx, {"id": shift["id"]}
    )
    assert visible == []


async def test_a_rejected_shift_goes_back_to_its_author_only(
    api: AsyncClient, auth_world: AuthWorld, new_user: Any, worker_sync_engine: Engine
) -> None:
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    techdir = await headers_for(api, "techdir")
    foreman = await headers_for(api, "foreman")
    unit = await own_unit(api, auth_world, head, finance)
    operator = await own_operator(api, await headers_for(api, "machinist"))
    _, other_writer = await signed_in(
        api,
        new_user,
        worker_sync_engine,
        "master",
        projects=(east(auth_world),),
        entities=(le_east(auth_world),),
    )
    shift = (
        await post_shift(
            api,
            foreman,
            shift_body(
                unit_id=unit["id"],
                project_id=east(auth_world),
                work_date=past(14),
                operator_id=operator,
            ),
        )
    ).json()
    assert (await move(api, foreman, shift["id"], "submit")).status_code == 200
    rejected = await api.post(
        f"{API}/shifts/{shift['id']}/reject", json={"reason": "нет табеля"}, headers=techdir
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["reject_reason"] == "нет табеля"
    by_other = await move(api, other_writer, shift["id"], "reopen")
    assert by_other.status_code == 403 and by_other.json() == {"detail": "forbidden"}
    reopened = await move(api, foreman, shift["id"], "reopen")
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["status"] == "draft" and reopened.json()["reject_reason"] is None


async def test_the_content_of_a_shift_is_locked_after_submission_and_after_confirmation(
    api: AsyncClient, auth_world: AuthWorld, new_user: Any, worker_sync_engine: Engine
) -> None:
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    techdir = await headers_for(api, "techdir")
    foreman = await headers_for(api, "foreman")
    unit = await own_unit(api, auth_world, head, finance)
    operator = await own_operator(api, await headers_for(api, "machinist"))
    shift = (
        await post_shift(
            api,
            foreman,
            shift_body(
                unit_id=unit["id"],
                project_id=east(auth_world),
                work_date=past(15),
                operator_id=operator,
            ),
        )
    ).json()
    draft = await api.patch(f"{API}/shifts/{shift['id']}", json={"hours": "7"}, headers=foreman)
    assert draft.status_code == 200 and draft.json()["hours"] == "7.00"
    assert (await move(api, foreman, shift["id"], "submit")).status_code == 200
    locked = await api.patch(f"{API}/shifts/{shift['id']}", json={"hours": "6"}, headers=foreman)
    assert locked.json() == {"detail": "equipment.shift_locked"}
    assert (await move(api, techdir, shift["id"], "confirm")).status_code == 200
    after = await api.patch(f"{API}/shifts/{shift['id']}", json={"hours": "6"}, headers=foreman)
    assert after.status_code == 403 and after.json() == {"detail": "forbidden"}


async def test_the_date_and_the_operator_are_checked_by_the_database(
    api: AsyncClient, auth_world: AuthWorld, new_user: Any, worker_sync_engine: Engine
) -> None:
    head = await headers_for(api, "equipment_head")
    finance = await headers_for(api, "finance")
    unit = await own_unit(api, auth_world, head, finance)
    operator = await own_operator(api, await headers_for(api, "machinist"))
    west_operator = await lessor_operator(
        api,
        head,
        auth_world.legal_entities[devseed.LE_WEST],
        lessor(auth_world, "Б"),
        "Машинист Запад",
    )
    _, author = await signed_in(
        api,
        new_user,
        worker_sync_engine,
        "foreman",
        projects=(east(auth_world),),
        entities=(le_east(auth_world),),
    )
    future = await post_shift(
        api,
        author,
        shift_body(
            unit_id=unit["id"],
            project_id=east(auth_world),
            work_date="2099-01-01",
            operator_id=operator,
        ),
    )
    assert future.json() == {"detail": "equipment.shift_date_in_future"}
    foreign = await post_shift(
        api,
        author,
        shift_body(
            unit_id=unit["id"],
            project_id=east(auth_world),
            work_date=past(16),
            operator_id=west_operator,
        ),
    )
    assert foreign.json() == {"detail": "equipment.operator_invalid"}


async def test_a_unit_of_another_legal_entity_is_not_found_for_the_foreman(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    head = await headers_for(api, "equipment_head")
    foreman = await headers_for(api, "foreman")
    code = await new_type(api, head)
    west_unit = await new_unit(
        api,
        head,
        auth_world,
        code=code,
        legal_entity=auth_world.legal_entities[devseed.LE_WEST],
    )
    operator = await own_operator(api, await headers_for(api, "machinist"))
    hidden = await post_shift(
        api,
        foreman,
        shift_body(
            unit_id=west_unit["id"],
            project_id=east(auth_world),
            work_date=past(17),
            operator_id=operator,
        ),
    )
    assert hidden.status_code == 404 and hidden.json() == {"detail": "not_found"}
