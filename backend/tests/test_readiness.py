"""S3a: % готовности и отклонения по срокам - эталонные расчёты по решениям PO (tz-changes §4).

Работа = min(подтверждённый факт / плановый объём, 1); узел = сумма(вес x %) / сумма весов; вес по
умолчанию - длительность в днях, schedule_item.weight переопределяет; поддерево и объект считаются
по тем же весам, а не средним процентов детей; в процент идёт только approved.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from tests.auth_support import AuthWorld, bearer, token_for
from tests.gpr_support import purge, unique

AS_OF = "2026-09-06"
P = "/api/v1/projects"


@dataclass(frozen=True)
class Reference:
    """Узел с тремя работами разных единиц (эталонный пример PO) в отдельном объекте."""

    project: UUID
    entity: UUID
    parent: UUID
    node: UUID
    items: dict[str, UUID]
    works: dict[str, UUID]


async def _new_work(connection: AsyncConnection, unit: str) -> UUID:
    work: UUID = await connection.scalar(
        text(
            "INSERT INTO work (code, name, unit, category) VALUES (:c, :n, :u, 'S3A') RETURNING id"
        ),
        {"c": unique(), "n": unique("Работа "), "u": unit},
    )
    return work


async def _new_node(
    connection: AsyncConnection, project: UUID, entity: UUID, kind: str, parent: UUID | None
) -> UUID:
    node: UUID = await connection.scalar(
        text(
            "INSERT INTO project_node (project_id, legal_entity_id, parent_id, type, name)"
            " VALUES (:p, :le, :parent, :kind, :name) RETURNING id"
        ),
        {"p": project, "le": entity, "parent": parent, "kind": kind, "name": unique()},
    )
    return node


async def _new_item(
    connection: AsyncConnection,
    project: UUID,
    entity: UUID,
    node: UUID,
    work: UUID,
    *,
    volume: str,
    start: date,
    end: date,
    weight: str | None = None,
) -> UUID:
    item: UUID = await connection.scalar(
        text(
            "INSERT INTO schedule_item (project_id, legal_entity_id, node_id, work_id,"
            " plan_start, plan_end, plan_volume, weight)"
            " VALUES (:p, :le, :n, :w, :s, :e, :v, :wt) RETURNING id"
        ),
        {"p": project, "le": entity, "n": node, "w": work, "s": start, "e": end, "v": volume,
         "wt": weight},
    )  # fmt: skip
    return item


async def _fact(
    connection: AsyncConnection,
    ref_project: UUID,
    entity: UUID,
    node: UUID,
    work: UUID,
    item: UUID | None,
    volume: str,
    status: str,
    author: UUID,
    *,
    deleted: bool = False,
) -> None:
    approved = status == "approved"
    await connection.execute(
        text(
            "INSERT INTO work_actual (project_id, legal_entity_id, node_id, work_id,"
            " schedule_item_id, volume, work_date, author_id, status, approved_by, approved_at,"
            " reject_reason, deleted_at) VALUES (:p, :le, :n, :w, :i, :v, DATE '2026-09-03', :u,"
            " :s, CASE WHEN :ok THEN :u END, CASE WHEN :ok THEN now() END,"
            " CAST(:reason AS text), CASE WHEN :gone THEN now() END)"
        ),
        {"p": ref_project, "le": entity, "n": node, "w": work, "i": item, "v": volume,
         "u": author, "s": status, "ok": approved, "gone": deleted,
         "reason": "Неверный обмер" if status == "rejected" else None},
    )  # fmt: skip


async def build_reference(
    engine: AsyncEngine, world: AuthWorld, *, default_weights: bool = False
) -> Reference:
    """A: 100 м3, 10 дней (01-10.09), подтверждено 50 (плюс черновик, отправленный и отклонённый,
    которые не считаются). B: 200 м2, 5 дней (05-09.09), вес вручную 8, подтверждено 250
    (перевыполнение). C: 10 т, 5 дней (07-11.09), подтверждено 2,5 (плюс удалённый факт). На узле
    есть и факт по виду работ без плана - в процент он не входит."""
    author = world.users["foreman"]
    async with engine.begin() as connection:
        entity = await connection.scalar(
            text("SELECT legal_entity_id FROM project WHERE id = :id"),
            {"id": world.projects["east"]},
        )
        project: UUID = await connection.scalar(
            text("INSERT INTO project (legal_entity_id, name) VALUES (:le, :n) RETURNING id"),
            {"le": entity, "n": unique("S3A-ref-")},
        )
        parent = await _new_node(connection, project, entity, "building", None)
        node = await _new_node(connection, project, entity, "section", parent)
        works = {
            "A": await _new_work(connection, "м3"),
            "B": await _new_work(connection, "м2"),
            "C": await _new_work(connection, "т"),
            "unplanned": await _new_work(connection, "шт"),
        }
        weight_b = None if default_weights else "8"
        items = {
            "A": await _new_item(connection, project, entity, node, works["A"], volume="100",
                                 start=date(2026, 9, 1), end=date(2026, 9, 10)),
            "B": await _new_item(connection, project, entity, node, works["B"], volume="200",
                                 start=date(2026, 9, 5), end=date(2026, 9, 9), weight=weight_b),
            "C": await _new_item(connection, project, entity, node, works["C"], volume="10",
                                 start=date(2026, 9, 7), end=date(2026, 9, 11)),
        }  # fmt: skip
        for key, volume, status, gone in (
            ("A", "30", "approved", False),
            ("A", "20", "approved", False),
            ("A", "25", "draft", False),
            ("A", "15", "submitted", False),
            ("A", "10", "rejected", False),
            ("B", "150", "approved", False),
            ("B", "100", "approved", False),
            ("C", "2.5", "approved", False),
            ("C", "5", "approved", True),
        ):
            await _fact(connection, project, entity, node, works[key], items[key], volume, status,
                        author, deleted=gone)  # fmt: skip
        await _fact(connection, project, entity, node, works["unplanned"], None, "7", "approved",
                    author)  # fmt: skip
    return Reference(project, entity, parent, node, items, works)


@pytest.fixture
async def cleanup(worker_engine: AsyncEngine, auth_world: AuthWorld) -> AsyncIterator[None]:
    yield
    await purge(worker_engine, auth_world)  # факт, строки ГПР и виды работ тестов (по коду S3A-)
    async with worker_engine.begin() as connection:
        ours = "project_id IN (SELECT id FROM project WHERE name LIKE 'S3A-ref-%')"
        for kind in ("section", "building"):
            await connection.execute(
                text(
                    "UPDATE project_node SET deleted_at = now() WHERE deleted_at IS NULL"  # noqa: S608
                    f" AND type = :t AND {ours}"
                ),
                {"t": kind},
            )
        await connection.execute(
            text(
                "UPDATE project SET deleted_at = now()"
                " WHERE deleted_at IS NULL AND name LIKE 'S3A-ref-%'"
            )
        )


async def get(api: AsyncClient, token: str, path: str, **params: Any) -> dict[str, Any]:
    response = await api.get(path, headers=bearer(token), params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def d(value: str) -> Decimal:
    return Decimal(value)


async def node_view(
    api: AsyncClient, token: str, ref: Reference, node: UUID | None = None, as_of: str = AS_OF
) -> dict[str, Any]:
    return await get(
        api, token, f"{P}/{ref.project}/nodes/{node or ref.node}/readiness", as_of=as_of
    )


# --- эталон PO ----------------------------------------------------------------------------------


async def test_the_reference_example_of_three_works_with_different_units_and_weights(
    api: AsyncClient, worker_engine: AsyncEngine, auth_world: AuthWorld, cleanup: None
) -> None:
    ref = await build_reference(worker_engine, auth_world)
    detail = await node_view(api, await token_for(api, "owner"), ref)

    # работа = min(подтверждённый факт / план, 1): 50 %, 100 % (250 из 200), 25 %
    by_item = {row["item_id"]: row for row in detail["items"]}
    a, b, c = (by_item[str(ref.items[key])] for key in "ABC")
    assert (d(a["percent"]), d(b["percent"]), d(c["percent"])) == (
        d("50.00"),
        d("100.00"),
        d("25.00"),
    )
    assert d(a["confirmed_volume"]) == 50 and d(b["confirmed_volume"]) == 250
    assert d(b["overrun_volume"]) == 50 and d(a["overrun_volume"]) == 0
    assert (a["unit"], b["unit"], c["unit"]) == ("м3", "м2", "т")

    # узел = (10 x 0,5 + 8 x 1 + 5 x 0,25) / (10 + 8 + 5) = 14,25 / 23
    own = detail["own"]
    assert own["items"] == 3 and d(own["weight"]) == 23
    assert d(own["percent"]) == d("61.96")
    assert detail["subtree"]["percent"] == own["percent"]


async def test_the_default_weight_is_the_planned_duration_in_days(
    api: AsyncClient, worker_engine: AsyncEngine, auth_world: AuthWorld, cleanup: None
) -> None:
    ref = await build_reference(worker_engine, auth_world, default_weights=True)
    detail = await node_view(api, await token_for(api, "owner"), ref)

    assert sorted(d(row["weight"]) for row in detail["items"]) == [5, 5, 10]
    # (10 x 0,5 + 5 x 1 + 5 x 0,25) / 20 = 11,25 / 20
    assert d(detail["own"]["percent"]) == d("56.25")


async def test_only_approved_facts_count_and_a_status_change_moves_the_percent(
    api: AsyncClient, worker_engine: AsyncEngine, auth_world: AuthWorld, cleanup: None
) -> None:
    ref = await build_reference(worker_engine, auth_world)
    token = await token_for(api, "owner")
    key = str(ref.items["A"])

    # по работе A есть черновик (25), отправленный (15) и отклонённый (10): в процент не входят
    before = await node_view(api, token, ref)
    item_a = next(row for row in before["items"] if row["item_id"] == key)
    assert d(item_a["confirmed_volume"]) == 50 and d(before["own"]["percent"]) == d("61.96")

    # подтвердим отправленный факт (15): подтверждённый объём A растёт до 65
    async with worker_engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE work_actual SET status = 'approved', approved_by = :u, approved_at = now()"
                " WHERE schedule_item_id = :i AND status = 'submitted'"
            ),
            {"u": auth_world.users["foreman"], "i": ref.items["A"]},
        )
    after = await node_view(api, token, ref)
    item_a = next(row for row in after["items"] if row["item_id"] == key)
    assert d(item_a["confirmed_volume"]) == 65 and d(item_a["percent"]) == d("65.00")
    # (10 x 0,65 + 8 x 1 + 5 x 0,25) / 23 = 15,75 / 23
    assert d(after["own"]["percent"]) == d("68.48")


async def test_over_fulfilment_is_capped_at_one_hundred_and_reported_separately(
    api: AsyncClient, worker_engine: AsyncEngine, auth_world: AuthWorld, cleanup: None
) -> None:
    ref = await build_reference(worker_engine, auth_world)
    detail = await node_view(api, await token_for(api, "owner"), ref)

    b = next(row for row in detail["items"] if row["item_id"] == str(ref.items["B"]))
    assert d(b["percent"]) == 100 and d(b["overrun_volume"]) == 50 and b["state"] == "done"
    assert d(detail["own"]["percent"]) <= 100


async def test_a_fact_without_a_plan_is_listed_apart_and_does_not_enter_the_percent(
    api: AsyncClient, worker_engine: AsyncEngine, auth_world: AuthWorld, cleanup: None
) -> None:
    ref = await build_reference(worker_engine, auth_world)
    detail = await node_view(api, await token_for(api, "owner"), ref)

    assert [(row["work_id"], d(row["volume"]), row["facts"]) for row in detail["unplanned"]] == [
        (str(ref.works["unplanned"]), d("7"), 1)
    ]
    assert d(detail["own"]["percent"]) == d("61.96")  # тот же эталон: лишний факт ничего не двигает


# --- отклонения по срокам -------------------------------------------------------------------------


async def test_the_deviation_from_the_plan_is_in_points_and_in_days_weighted(
    api: AsyncClient, worker_engine: AsyncEngine, auth_world: AuthWorld, cleanup: None
) -> None:
    ref = await build_reference(worker_engine, auth_world)
    detail = await node_view(api, await token_for(api, "owner"), ref)  # 2026-09-06

    by_item = {row["item_id"]: row for row in detail["items"]}
    a, b, c = (by_item[str(ref.items[key])] for key in "ABC")
    assert (d(a["planned_percent"]), d(b["planned_percent"]), d(c["planned_percent"])) == (
        d("60.00"), d("40.00"), d("0.00"),
    )  # fmt: skip
    # отставание в днях = прошло дней - доля факта x длительность: +1, -3, -1,25 (минус - опережаем)
    assert (d(a["delay_days"]), d(b["delay_days"]), d(c["delay_days"])) == (
        d("1.0"), d("-3.0"), d("-1.3"),
    )  # fmt: skip

    own = detail["own"]
    assert d(own["planned_percent"]) == d("40.00") and d(own["deviation"]) == d("21.96")
    assert d(own["delay_days"]) == d("-0.9")  # (10 x 1 - 8 x 3 - 5 x 1,25) / 23, опережение


async def test_work_states_follow_the_calendar_and_the_confirmed_volume(
    api: AsyncClient, worker_engine: AsyncEngine, auth_world: AuthWorld, cleanup: None
) -> None:
    ref = await build_reference(worker_engine, auth_world)
    async with worker_engine.begin() as connection:
        silent_work = await _new_work(connection, "м3")
        silent = await _new_item(
            connection, ref.project, ref.entity, ref.node, silent_work,
            volume="10", start=date(2026, 9, 8), end=date(2026, 9, 12),
        )  # fmt: skip
    token = await token_for(api, "owner")

    states = {}
    for as_of in ("2026-09-07", "2026-09-10", "2026-09-20"):
        detail = await node_view(api, token, ref, as_of=as_of)
        states[as_of] = {row["item_id"]: row["state"] for row in detail["items"]}

    key = str(silent)
    assert states["2026-09-07"][key] == "not_started"  # срок начала ещё не наступил
    assert states["2026-09-10"][key] == "late_start"  # начало прошло, факта нет
    assert states["2026-09-20"][key] == "overdue_no_fact"  # срок вышел, подтверждённого факта нет
    assert states["2026-09-20"][str(ref.items["A"])] == "overdue"  # срок вышел, 50 из 100
    assert states["2026-09-20"][str(ref.items["B"])] == "done"
    late = await node_view(api, token, ref, as_of="2026-09-20")
    row = next(row for row in late["items"] if row["item_id"] == key)
    assert row["overdue_days"] == 8 and d(late["own"]["planned_percent"]) == 100
    assert late["own"]["overdue_no_fact"] == 1 and late["own"]["overdue"] == 3
    assert late["own"]["completed"] == 1


# --- поддерево и объект ---------------------------------------------------------------------------


async def test_the_subtree_and_the_object_are_aggregated_by_weights_not_by_child_percents(
    api: AsyncClient, worker_engine: AsyncEngine, auth_world: AuthWorld, cleanup: None
) -> None:
    ref = await build_reference(worker_engine, auth_world)
    async with worker_engine.begin() as connection:
        # второй узел под тем же родителем: одна работа, 2 дня, выполнена полностью
        other = await _new_node(connection, ref.project, ref.entity, "section", ref.parent)
        work = await _new_work(connection, "м3")
        item = await _new_item(connection, ref.project, ref.entity, other, work, volume="10",
                               start=date(2026, 9, 1), end=date(2026, 9, 2))  # fmt: skip
        await _fact(connection, ref.project, ref.entity, other, work, item, "10", "approved",
                    auth_world.users["foreman"])  # fmt: skip
    token = await token_for(api, "owner")

    parent = await node_view(api, token, ref, node=ref.parent)
    assert parent["own"] is None  # на родителе своих работ нет
    # (14,25 + 2 x 1) / (23 + 2) = 16,25 / 25 = 65,00 %, а не среднее 61,96 и 100 (= 80,98)
    assert d(parent["subtree"]["percent"]) == d("65.00")
    assert parent["subtree"]["items"] == 4 and d(parent["subtree"]["weight"]) == 25
    assert {child["node_id"] for child in parent["children"]} == {str(ref.node), str(other)}

    whole = await get(api, token, f"{P}/{ref.project}/readiness", as_of=AS_OF)
    assert d(whole["project"]["percent"]) == d("65.00") and whole["project"]["items"] == 4
    by_node = {row["node_id"]: row for row in whole["nodes"]}
    assert d(by_node[str(ref.parent)]["subtree"]["percent"]) == d("65.00")
    assert by_node[str(ref.parent)]["own"] is None
    assert d(by_node[str(ref.node)]["own"]["percent"]) == d("61.96")
    assert d(by_node[str(other)]["own"]["percent"]) == d("100.00")


async def test_a_node_without_works_in_its_subtree_is_absent_from_the_readiness_list(
    api: AsyncClient, worker_engine: AsyncEngine, auth_world: AuthWorld, cleanup: None
) -> None:
    ref = await build_reference(worker_engine, auth_world)
    async with worker_engine.begin() as connection:
        empty = await _new_node(connection, ref.project, ref.entity, "section", ref.parent)
    token = await token_for(api, "owner")

    whole = await get(api, token, f"{P}/{ref.project}/readiness", as_of=AS_OF)
    assert str(empty) not in {row["node_id"] for row in whole["nodes"]}
    detail = await node_view(api, token, ref, node=empty)
    assert detail["own"] is None and detail["subtree"] is None and detail["items"] == []


# --- доступ ---------------------------------------------------------------------------------------


async def test_readiness_of_a_foreign_object_or_node_is_not_found(
    api: AsyncClient, worker_engine: AsyncEngine, auth_world: AuthWorld, cleanup: None
) -> None:
    ref = await build_reference(worker_engine, auth_world)
    token = await token_for(api, "master")  # доступ только к «Востоку»

    for path in (f"{P}/{ref.project}/readiness", f"{P}/{ref.project}/nodes/{ref.node}/readiness"):
        assert (await api.get(path, headers=bearer(token))).status_code == 404
    east = auth_world.projects["east"]
    foreign_node = f"{P}/{east}/nodes/{ref.node}/readiness"  # узел чужого объекта в пути своего
    assert (await api.get(foreign_node, headers=bearer(token))).status_code == 404


@pytest.mark.parametrize(
    ("who", "expected"),
    [("foreman", 200), ("master", 200), ("estimator", 200), ("customer", 200), ("sub_a", 200),
     ("storekeeper", 403), ("accountant", 404), ("supplier", 404)],
)  # fmt: skip
async def test_who_reads_the_readiness_of_the_east_object(
    api: AsyncClient, auth_world: AuthWorld, who: str, expected: int
) -> None:
    east = auth_world.projects["east"]
    response = await api.get(f"{P}/{east}/readiness", headers=bearer(await token_for(api, who)))
    assert response.status_code == expected, response.text


async def test_an_external_role_sees_the_same_numbers_from_approved_facts_only(
    api: AsyncClient, worker_engine: AsyncEngine, auth_world: AuthWorld, cleanup: None
) -> None:
    east = auth_world.projects["east"]
    async with worker_engine.begin() as connection:
        entity = await connection.scalar(
            text("SELECT legal_entity_id FROM project WHERE id = :id"), {"id": east}
        )
        node = await _new_node(connection, east, entity, "building", None)
        work = await _new_work(connection, "м3")
        item = await _new_item(connection, east, entity, node, work, volume="100",
                               start=date(2026, 9, 1), end=date(2026, 9, 10))  # fmt: skip
        for volume, status in (("40", "approved"), ("30", "draft"), ("20", "submitted")):
            await _fact(connection, east, entity, node, work, item, volume, status,
                        auth_world.users["foreman"])  # fmt: skip

    for who in ("owner", "customer", "sub_a"):
        view = await get(api, await token_for(api, who), f"{P}/{east}/nodes/{node}/readiness",
                         as_of=AS_OF)  # fmt: skip
        assert d(view["own"]["percent"]) == d("40.00"), who
        assert d(view["items"][0]["confirmed_volume"]) == 40, who
