"""ГСМ в путевом листе (SE-2b, решение PO 3): машинист вносит заправку в листе до отправки, журнал
получает её при подтверждении, правка подтверждённого листа - техдиректором со сторно. Лист заводит
и отправляет прораб (автор); подтверждает техдиректор, потому что автор своё не подтверждает. Чек -
подписанная загрузка в S3, как у фото факта."""

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx
import pytest
from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.auth_support import AuthWorld
from tests.equipment_support import (
    API,
    east,
    headers_for,
    idem,
    le_east,
    money_keys,
    move,
    own_operator,
    own_unit,
    past,
    post_shift,
    shift_body,
    signed_in,
)


@pytest.fixture
async def s3() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=10) as client:
        yield client


async def sheet(
    api: AsyncClient, auth_world: AuthWorld, finance: dict[str, str] | None = None
) -> tuple[dict[str, str], dict[str, str], str]:
    """Лист на свою технику: заводит прораб (автор), заправку в черновике вносит машинист.

    Заголовки финдиректора передаются, если тест уже вошёл под ним: TOTP-код одноразовый."""
    head = await headers_for(api, "equipment_head")
    finance = finance or await headers_for(api, "finance")
    unit = await own_unit(api, auth_world, head, finance)
    machinist = await headers_for(api, "machinist")
    foreman = await headers_for(api, "foreman")
    created = await post_shift(
        api,
        foreman,
        shift_body(
            unit_id=unit["id"],
            project_id=east(auth_world),
            work_date=past(1),
            operator_id=await own_operator(api, machinist),
        ),
    )
    assert created.status_code == 201, created.text
    return machinist, foreman, created.json()["id"]


async def add_fuel(
    api: AsyncClient,
    headers: dict[str, str],
    shift_id: str,
    liters: str = "40.00",
    amount: str = "3200.00",
) -> Response:
    return await api.post(
        f"{API}/shifts/{shift_id}/fuel",
        json={"liters": liters, "amount": amount},
        headers={**headers, **idem()},
    )


async def journal_fuel(engine: AsyncEngine, fuel_id: str) -> list[tuple[Any, ...]]:
    """Строки журнала заправки (версия, количество, сумма, сторно) как есть, BYPASSRLS."""
    async with engine.connect() as connection:
        result = await connection.execute(
            text(
                "SELECT posting_version, quantity, amount, reversal_of_id IS NOT NULL"
                " FROM cost_entry WHERE source_type = 'equipment_fuel'"
                " AND source_id = CAST(:id AS uuid)"
                " ORDER BY posting_version, reversal_of_id NULLS FIRST"
            ),
            {"id": fuel_id},
        )
        return [tuple(row) for row in result]


async def test_a_machinist_enters_fuel_in_his_sheet_and_does_not_see_the_amount(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    machinist, _, shift_id = await sheet(api, auth_world)
    added = await add_fuel(api, machinist, shift_id)
    assert added.status_code == 201, added.text
    body = added.json()
    assert (body["shift_id"], body["liters"], body["has_receipt"]) == (shift_id, "40.00", False)
    assert "amount" not in body
    listed = await api.get(f"{API}/shifts/{shift_id}/fuel", headers=machinist)
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()] == [body["id"]]
    assert money_keys(listed.json()) == set()


async def test_a_foreman_who_did_not_write_the_sheet_cannot_add_fuel_to_it(
    api: AsyncClient, auth_world: AuthWorld, new_user: Any, worker_sync_engine: Engine
) -> None:
    _, _, shift_id = await sheet(api, auth_world)
    _, other = await signed_in(
        api,
        new_user,
        worker_sync_engine,
        "foreman",
        projects=(east(auth_world),),
        entities=(le_east(auth_world),),
    )
    refused = await add_fuel(api, other, shift_id)
    assert (refused.status_code, refused.json()) == (403, {"detail": "forbidden"})


async def test_a_submitted_sheet_takes_no_more_fuel_from_its_machinist(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    machinist, foreman, shift_id = await sheet(api, auth_world)
    assert (await move(api, foreman, shift_id, "submit")).status_code == 200
    locked = await add_fuel(api, machinist, shift_id)
    assert (locked.status_code, locked.json()) == (409, {"detail": "equipment.shift_locked"})


async def test_the_journal_gets_the_fuel_only_when_the_sheet_is_confirmed(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    machinist, foreman, shift_id = await sheet(api, auth_world)
    fuel_id = (await add_fuel(api, machinist, shift_id)).json()["id"]
    assert (await move(api, foreman, shift_id, "submit")).status_code == 200
    assert await journal_fuel(worker_engine, fuel_id) == []
    techdir = await headers_for(api, "techdir")
    confirmed = await move(api, techdir, shift_id, "confirm")
    assert confirmed.status_code == 200, confirmed.text
    assert await journal_fuel(worker_engine, fuel_id) == [(1, 40, 3200, False)]


async def test_a_correction_after_confirmation_is_made_by_the_technical_director_and_reposted(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    machinist, foreman, shift_id = await sheet(api, auth_world)
    first = (await add_fuel(api, machinist, shift_id)).json()["id"]
    assert (await move(api, foreman, shift_id, "submit")).status_code == 200
    techdir = await headers_for(api, "techdir")
    assert (await move(api, techdir, shift_id, "confirm")).status_code == 200
    by_author = await add_fuel(api, foreman, shift_id, "5.00", "400.00")
    assert (by_author.status_code, by_author.json()) == (403, {"detail": "forbidden"})
    second = (await add_fuel(api, techdir, shift_id, "10.00", "800.00")).json()["id"]
    removed = await api.post(f"{API}/shifts/{shift_id}/fuel/{first}/remove", headers=techdir)
    assert removed.status_code == 204, removed.text
    # v1 - первая заправка; v2 - обе (вторая добавлена техдиректором); v3 - без первой (удалена).
    assert await journal_fuel(worker_engine, first) == [
        (1, 40, 3200, False),
        (1, -40, -3200, True),
        (2, 40, 3200, False),
        (2, -40, -3200, True),
    ]
    assert await journal_fuel(worker_engine, second) == [
        (2, 10, 800, False),
        (2, -10, -800, True),
        (3, 10, 800, False),
    ]


async def test_the_receipt_is_presigned_uploaded_confirmed_and_not_replaced(
    api: AsyncClient, auth_world: AuthWorld, s3: httpx.AsyncClient
) -> None:
    finance = await headers_for(api, "finance")
    machinist, foreman, shift_id = await sheet(api, auth_world, finance)
    fuel_id = (await add_fuel(api, machinist, shift_id)).json()["id"]
    payload = b"fake-jpeg!!"
    presigned = await api.post(
        f"{API}/shifts/{shift_id}/fuel/{fuel_id}/receipt",
        json={"content_type": "image/jpeg", "size_bytes": len(payload)},
        headers=machinist,
    )
    assert presigned.status_code == 200, presigned.text
    item = presigned.json()
    assert (
        item["key"].startswith("project/") and f"/shift/{shift_id}/fuel/{fuel_id}/" in item["key"]
    )
    assert item["expires_in"] == 900 and "X-Amz-Signature" in item["upload_url"]
    uploaded = await s3.put(item["upload_url"], content=payload, headers=item["headers"])
    assert uploaded.status_code == 200, uploaded.text
    confirmed = await api.post(
        f"{API}/shifts/{shift_id}/fuel/{fuel_id}/receipt/confirm",
        json={"key": item["key"]},
        headers=machinist,
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["has_receipt"] is True
    # Чек показывает сумму: ссылку получает загрузивший его и read:rates; прораб без денег - нет.
    own_link = await api.get(f"{API}/shifts/{shift_id}/fuel/{fuel_id}/receipt", headers=machinist)
    assert own_link.status_code == 200 and own_link.json()["expires_in"] == 120
    foreman_link = await api.get(f"{API}/shifts/{shift_id}/fuel/{fuel_id}/receipt", headers=foreman)
    assert (foreman_link.status_code, foreman_link.json()) == (403, {"detail": "forbidden"})
    link = await api.get(f"{API}/shifts/{shift_id}/fuel/{fuel_id}/receipt", headers=finance)
    assert link.status_code == 200 and link.json()["expires_in"] == 120
    # Пока лист в черновике, чек заменяют: новый файл загружен, подтверждён и стал ссылкой.
    second = await upload_receipt(api, s3, machinist, shift_id, fuel_id, b"second-jpeg!")
    replaced = await api.post(
        f"{API}/shifts/{shift_id}/fuel/{fuel_id}/receipt/confirm",
        json={"key": second},
        headers=machinist,
    )
    assert replaced.status_code == 200, replaced.text
    relinked = await api.get(f"{API}/shifts/{shift_id}/fuel/{fuel_id}/receipt", headers=finance)
    assert relinked.status_code == 200 and second in relinked.json()["url"]


async def upload_receipt(
    api: AsyncClient,
    s3: httpx.AsyncClient,
    headers: dict[str, str],
    shift_id: str,
    fuel_id: str,
    payload: bytes,
) -> str:
    presigned = await api.post(
        f"{API}/shifts/{shift_id}/fuel/{fuel_id}/receipt",
        json={"content_type": "image/jpeg", "size_bytes": len(payload)},
        headers=headers,
    )
    assert presigned.status_code == 200, presigned.text
    item = presigned.json()
    uploaded = await s3.put(item["upload_url"], content=payload, headers=item["headers"])
    assert uploaded.status_code == 200, uploaded.text
    return str(item["key"])


async def test_a_receipt_key_of_another_fuel_row_and_a_missing_upload_are_refused(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    machinist, _, shift_id = await sheet(api, auth_world)
    fuel_id = (await add_fuel(api, machinist, shift_id)).json()["id"]
    project = east(auth_world)
    foreign = f"project/{project}/shift/{shift_id}/fuel/{uuid4()}/{uuid4()}.jpg"
    refused = await api.post(
        f"{API}/shifts/{shift_id}/fuel/{fuel_id}/receipt/confirm",
        json={"key": foreign},
        headers=machinist,
    )
    assert (refused.status_code, refused.json()) == (422, {"detail": "receipt_key_invalid"})
    missing = f"project/{project}/shift/{shift_id}/fuel/{fuel_id}/{uuid4()}.jpg"
    not_uploaded = await api.post(
        f"{API}/shifts/{shift_id}/fuel/{fuel_id}/receipt/confirm",
        json={"key": missing},
        headers=machinist,
    )
    assert (not_uploaded.status_code, not_uploaded.json()) == (
        409,
        {"detail": "receipt_not_uploaded"},
    )


async def test_only_the_machinist_or_the_author_removes_fuel_from_a_draft(
    api: AsyncClient, auth_world: AuthWorld, new_user: Any, worker_sync_engine: Engine
) -> None:
    machinist, _, shift_id = await sheet(api, auth_world)
    fuel_id = (await add_fuel(api, machinist, shift_id)).json()["id"]
    _, other = await signed_in(
        api,
        new_user,
        worker_sync_engine,
        "foreman",
        projects=(east(auth_world),),
        entities=(le_east(auth_world),),
    )
    refused = await api.post(f"{API}/shifts/{shift_id}/fuel/{fuel_id}/remove", headers=other)
    assert (refused.status_code, refused.json()) == (403, {"detail": "forbidden"})
    removed = await api.post(f"{API}/shifts/{shift_id}/fuel/{fuel_id}/remove", headers=machinist)
    assert removed.status_code == 204, removed.text
    assert (await api.get(f"{API}/shifts/{shift_id}/fuel", headers=machinist)).json() == []


async def test_a_fuel_of_a_sheet_is_never_reversed_by_the_standalone_route(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    machinist, _, shift_id = await sheet(api, auth_world)
    fuel_id = (await add_fuel(api, machinist, shift_id)).json()["id"]
    techdir = await headers_for(api, "techdir")
    reversed_standalone = await api.post(
        f"{API}/fuel/{fuel_id}/reverse", json={"reason": "ошибка"}, headers=techdir
    )
    assert (reversed_standalone.status_code, reversed_standalone.json()) == (
        409,
        {"detail": "equipment.fuel_shift_bound"},
    )


async def test_fuel_rows_carry_no_amount_and_the_journal_holds_it_for_the_rates_reader(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    """Заправка в листе без суммы для всех; сумма - в журнале текущей версии, видна с read:rates."""
    finance = await headers_for(api, "finance")
    machinist, foreman, shift_id = await sheet(api, auth_world, finance)
    fuel_id = (await add_fuel(api, machinist, shift_id)).json()["id"]
    assert (await move(api, foreman, shift_id, "submit")).status_code == 200
    techdir = await headers_for(api, "techdir")
    assert (await move(api, techdir, shift_id, "confirm")).status_code == 200
    for who in (finance, machinist, foreman):
        listed = await api.get(f"{API}/shifts/{shift_id}/fuel", headers=who)
        assert listed.status_code == 200, listed.text
        assert [item["id"] for item in listed.json()] == [fuel_id]
        assert money_keys(listed.json()) == set()
    entries = await api.get(
        f"{API}/costs/entries",
        params={"date_from": "2026-01-01", "date_to": "2100-12-31", "category": "equipment_fuel"},
        headers=finance,
    )
    assert entries.status_code == 200, entries.text
    assert [
        (row["source_id"], row["amount"]) for row in entries.json() if row["source_id"] == fuel_id
    ] == [(fuel_id, "3200.00")]
    refused = await api.get(
        f"{API}/costs/entries",
        params={"date_from": "2026-01-01", "date_to": "2100-12-31"},
        headers=foreman,
    )
    assert refused.status_code == 403
