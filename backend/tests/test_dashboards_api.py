"""S4: дашборды объекта, группы и вид заказчика.

Числа сверяются с /readiness (S3a) и с ручным расчётом по формулам: ряд по неделям, факт по
work_date, только approved; пороги из настроек; права по матрице.
"""

from collections.abc import AsyncIterator
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.core.config import get_settings
from tests.auth_support import AuthWorld, bearer, token_for
from tests.gpr_support import unique
from tests.test_readiness import build_reference, cleanup  # noqa: F401  (фикстура эталона S3a)

P = "/api/v1/projects"
D = "/api/v1/dashboards"
TWO = Decimal("0.01")
FORBIDDEN_FOR_PORTAL = {
    "budget", "planned_budget", "weight", "weights", "photos", "uploaded_by_name", "uploaded_by",
    "responsible_name", "problems", "zones", "nodes", "facts", "counts", "items", "overdue",
    "delay_days", "node_path",
}  # fmt: skip


def _pct(part: Decimal, total: Decimal) -> Decimal:
    """Как в сервисе: четыре знака в SQL, затем два знака в ответе."""
    four = (part / total * 100).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    return four.quantize(TWO, rounding=ROUND_HALF_UP)


async def _today(engine: AsyncEngine) -> date:
    async with engine.connect() as connection:
        value: date = await connection.scalar(
            text("SELECT (now() AT TIME ZONE 'Asia/Bishkek')::date")
        )
    return value


async def call(api: AsyncClient, token: str, path: str, **params: Any) -> Response:
    return await api.get(path, headers=bearer(token), params=params)


async def ok(api: AsyncClient, token: str, path: str, **params: Any) -> dict[str, Any]:
    response = await call(api, token, path, **params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _keys(value: Any) -> set[str]:
    """Все ключи ответа на любой глубине (для проверки, что скрытые поля не уходят)."""
    found: set[str] = set()
    if isinstance(value, dict):
        for key, inner in value.items():
            found.add(key)
            found |= _keys(inner)
    elif isinstance(value, list):
        for inner in value:
            found |= _keys(inner)
    return found


async def _work(connection: AsyncConnection, unit: str) -> UUID:
    work: UUID = await connection.scalar(
        text("INSERT INTO work (code, name, unit, category) VALUES (:c, :n, :u, 'S4')"
             " RETURNING id"),
        {"c": unique(), "n": unique("Работа S4 "), "u": unit},
    )  # fmt: skip
    return work


async def _node(connection: AsyncConnection, project: UUID, entity: UUID) -> UUID:
    node: UUID = await connection.scalar(
        text("INSERT INTO project_node (project_id, legal_entity_id, type, name)"
             " VALUES (:p, :le, 'building', :n) RETURNING id"),
        {"p": project, "le": entity, "n": unique("Корпус S4 ")},
    )  # fmt: skip
    return node


async def _item(
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
        text("INSERT INTO schedule_item (project_id, legal_entity_id, node_id, work_id,"
             " plan_start, plan_end, plan_volume, weight)"
             " VALUES (:p, :le, :n, :w, :s, :e, :v, :wt) RETURNING id"),
        {"p": project, "le": entity, "n": node, "w": work, "s": start, "e": end, "v": volume,
         "wt": weight},
    )  # fmt: skip
    return item


async def _fact(
    connection: AsyncConnection,
    project: UUID,
    entity: UUID,
    node: UUID,
    work: UUID,
    item: UUID,
    *,
    volume: str,
    work_date: date,
    status: str,
    author: UUID,
) -> None:
    approved = status == "approved"
    await connection.execute(
        text("INSERT INTO work_actual (project_id, legal_entity_id, node_id, work_id,"
             " schedule_item_id, volume, work_date, author_id, status, approved_by, approved_at,"
             " reject_reason) VALUES (:p, :le, :n, :w, :i, :v, :d, :u, :s,"
             " CASE WHEN :ok THEN :u END, CASE WHEN :ok THEN now() END,"
             " CAST(:reason AS text))"),
        {"p": project, "le": entity, "n": node, "w": work, "i": item, "v": volume, "d": work_date,
         "u": author, "s": status, "ok": approved,
         "reason": "Неверный обмер" if status == "rejected" else None},
    )  # fmt: skip


@pytest.fixture
async def example(
    worker_engine: AsyncEngine, auth_world: AuthWorld, today: date
) -> AsyncIterator[dict[str, Any]]:
    """Объект S4 с датами вокруг сегодняшнего дня. Работа A: 100 м3, план с -21 по +7 дней, вес по
    длительности (29). Работа B: 50 м2, с -14 по -7 дней, вес вручную 4. Факт: A одобрен -15 (30) и
    -8 (20); A черновик -6 (25) и отправлен -3 (15) - в ряд не входят; B одобрен -13 (60), отклонён
    -9 (10). Финансы: бюджет 1 500 000."""
    author = auth_world.users["foreman"]
    async with worker_engine.begin() as connection:
        entity = await connection.scalar(
            text("SELECT legal_entity_id FROM project WHERE id = :id"),
            {"id": auth_world.projects["east"]},
        )
        project: UUID = await connection.scalar(
            text("INSERT INTO project (legal_entity_id, name) VALUES (:le, :n) RETURNING id"),
            {"le": entity, "n": unique("S4-example ")},
        )
        await connection.execute(
            text(
                "INSERT INTO project_finance (project_id, legal_entity_id, planned_budget)"
                " VALUES (:p, :le, 1500000)"
            ),
            {"p": project, "le": entity},
        )
        node = await _node(connection, project, entity)
        work_a = await _work(connection, "м3")
        work_b = await _work(connection, "м2")
        item_a = await _item(
            connection,
            project,
            entity,
            node,
            work_a,
            volume="100",
            start=today - timedelta(days=21),
            end=today + timedelta(days=7),
        )
        item_b = await _item(connection, project, entity, node, work_b, volume="50",
                             start=today - timedelta(days=14), end=today - timedelta(days=7),
                             weight="4")  # fmt: skip
        facts = (
            (item_a, work_a, "30", -15, "approved"),
            (item_a, work_a, "20", -8, "approved"),
            (item_a, work_a, "25", -6, "draft"),
            (item_a, work_a, "15", -3, "submitted"),
            (item_b, work_b, "60", -13, "approved"),
            (item_b, work_b, "10", -9, "rejected"),
        )
        for item, work, volume, offset, status in facts:
            await _fact(
                connection,
                project,
                entity,
                node,
                work,
                item,
                volume=volume,
                work_date=today + timedelta(days=offset),
                status=status,
                author=author,
            )
    # Объект не удаляем: правило БД не даёт мягко удалить проект с живыми узлами, а тестовая база
    # пересоздаётся при каждом прогоне.
    yield {"project": project, "items": (item_a, item_b), "works": (work_a, work_b),
           "entity": entity, "node": node}  # fmt: skip


@pytest.fixture
async def today(worker_engine: AsyncEngine) -> date:
    return await _today(worker_engine)


def _manual(
    points: list[date],
    items: list[tuple[date, date, Decimal, Decimal]],
    facts: list[tuple[int, date, Decimal]],
) -> list[tuple[date, Decimal, Decimal]]:
    """Ручной расчёт ряда по формулам: план линейный по дням, факт - approved с work_date <= точки,
    доля работы не выше единицы, веса суммируются в общий знаменатель."""
    total = sum((weight for *_, weight in items), Decimal(0))
    rows: list[tuple[date, Decimal, Decimal]] = []
    for point in points:
        plan = Decimal(0)
        fact = Decimal(0)
        for index, (start, end, volume, weight) in enumerate(items):
            days = (end - start).days + 1
            elapsed = min(max((point - start).days + 1, 0), days)
            plan += weight * Decimal(elapsed) / Decimal(days)
            confirmed = sum(
                (amount for i, when, amount in facts if i == index and when <= point), Decimal(0)
            )
            fact += weight * min(confirmed / volume, Decimal(1))
        rows.append((point, _pct(plan, total), _pct(fact, total)))
    return rows


# --- эталон S3a: дашборд = /readiness ------------------------------------------------------------


async def test_the_object_dashboard_equals_readiness_on_the_s3a_reference(
    api: AsyncClient,
    worker_engine: AsyncEngine,
    auth_world: AuthWorld,
    cleanup: None,  # noqa: F811
) -> None:
    ref = await build_reference(worker_engine, auth_world)
    owner = await token_for(api, "owner")
    readiness = await ok(api, owner, f"{P}/{ref.project}/readiness")
    dash = await ok(api, owner, f"{D}/object/{ref.project}")

    project = readiness["project"]
    assert dash["progress"] == {
        "percent": project["percent"],
        "planned_percent": project["planned_percent"],
        "deviation": project["deviation"],
        "delay_days": project["delay_days"],
    }
    # эталон PO: три работы, узел 61,96 %; все сроки прошли - план 100 %, отставание -38,04 п.п.
    assert (
        dash["progress"]["percent"] == "61.96" and dash["progress"]["planned_percent"] == "100.00"
    )
    assert dash["counts"] == {"works": 3, "completed": 1, "overdue": 2, "no_fact_started": 0,
                              "problems": 2}  # fmt: skip
    # узлы верхнего уровня: здание эталона со всем поддеревом
    top = [node for node in readiness["nodes"] if node["parent_id"] is None]
    assert [tile["progress"]["percent"] for tile in dash["nodes"]] == [
        node["subtree"]["percent"] for node in top
    ]
    # последняя точка ряда - сегодня: та же готовность, что в S3a
    last = dash["series"][-1]
    assert last["date"] == dash["as_of"] and last["fact_percent"] == "61.96"
    assert last["plan_percent"] == "100.00"


# --- ряд по неделям: ручной пример ---------------------------------------------------------------


async def test_the_weekly_series_is_the_manual_calculation_with_approved_facts_by_work_date(
    api: AsyncClient, auth_world: AuthWorld, example: dict[str, Any], today: date
) -> None:
    owner = await token_for(api, "owner")
    dash = await ok(api, owner, f"{D}/object/{example['project']}")

    points = [today + timedelta(days=step) for step in (-21, -14, -7, 0, 7)]
    assert [row["date"] for row in dash["series"]] == [p.isoformat() for p in points]

    a_start, a_end = today - timedelta(days=21), today + timedelta(days=7)
    b_start, b_end = today - timedelta(days=14), today - timedelta(days=7)
    items = [(a_start, a_end, Decimal(100), Decimal(29)), (b_start, b_end, Decimal(50), Decimal(4))]
    facts = [
        (0, today - timedelta(days=15), Decimal(30)),
        (0, today - timedelta(days=8), Decimal(20)),
        (1, today - timedelta(days=13), Decimal(60)),
    ]  # черновик, отправленный и отклонённый факты в расчёт не входят
    expected = _manual(points, items, facts)
    for row, (when, plan, fact) in zip(dash["series"], expected, strict=True):
        assert row["plan_percent"] == str(plan), row
        if when > today:
            assert row["fact_percent"] is None
        else:
            assert row["fact_percent"] == str(fact), row

    # сегодня ряда совпадает с итогом объекта (та же формула, что у S3a)
    assert dash["series"][3]["plan_percent"] == dash["progress"]["planned_percent"]
    assert dash["series"][3]["fact_percent"] == dash["progress"]["percent"]
    assert dash["facts"] == {"awaiting_approval": 1, "awaiting_approval_stale": 0, "recent": 1}


async def test_a_problem_is_a_work_behind_the_plan_by_the_threshold_from_settings(
    api: AsyncClient, auth_world: AuthWorld, example: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    owner = await token_for(api, "owner")
    settings = get_settings()
    monkeypatch.setattr(settings, "dashboard_problem_lag_days", 3)
    at_three = await ok(api, owner, f"{D}/object/{example['project']}")
    monkeypatch.setattr(settings, "dashboard_problem_lag_days", 8)
    at_eight = await ok(api, owner, f"{D}/object/{example['project']}")

    # работа A отстаёт на 7,5 дня: при пороге 3 - проблема, при 8 - нет; B выполнена
    assert at_three["counts"]["problems"] == 1 and at_eight["counts"]["problems"] == 0
    assert [row["work_id"] for row in at_three["problems"]] == [str(example["works"][0])]
    assert at_eight["problems"] == []
    assert at_three["thresholds"]["lag_days"] == 3 and at_eight["thresholds"]["lag_days"] == 8


# --- права и виды ---------------------------------------------------------------------------------


async def test_money_is_shown_only_to_finance_readers(
    api: AsyncClient, auth_world: AuthWorld, example: dict[str, Any]
) -> None:
    owner = await token_for(api, "owner")
    with_money = await ok(api, owner, f"{D}/object/{example['project']}")
    assert with_money["budget"] == {"planned_budget": "1500000.00", "currency": "KGS"}
    # мастер видит Восток, но финансов у него нет (право finance:read): бюджет не отдаётся
    master = await token_for(api, "master")
    east = await ok(api, master, f"{D}/object/{auth_world.projects['east']}")
    assert east["budget"] is None


async def test_the_object_dashboard_is_hidden_from_a_foreign_object_and_from_counterparties(
    api: AsyncClient, auth_world: AuthWorld, example: dict[str, Any]
) -> None:
    foreman = await token_for(api, "foreman")
    assert (await call(api, foreman, f"{D}/object/{example['project']}")).status_code == 404
    customer = await token_for(api, "customer")
    east_object = await call(api, customer, f"{D}/object/{auth_world.projects['east']}")
    assert east_object.status_code == 403


async def test_the_group_dashboard_is_for_the_group_scope_only(
    api: AsyncClient, auth_world: AuthWorld, example: dict[str, Any]
) -> None:
    foreman = await token_for(api, "foreman")
    assert (await call(api, foreman, f"{D}/group")).status_code == 403
    owner = await token_for(api, "owner")
    group = await ok(api, owner, f"{D}/group")
    row = next(item for item in group["rows"] if item["project_id"] == str(example["project"]))
    object_view = await ok(api, owner, f"{D}/object/{example['project']}")
    # сводка по группе сходится с дашбордом объекта: та же готовность, то же число проблем
    assert row["progress"] == object_view["progress"]
    assert row["problems"] == object_view["counts"]["problems"] == 1
    assert row["awaiting_approval"] == 1 and row["items"] == 2
    assert row["budget"] == {"planned_budget": "1500000.00", "currency": "KGS"}


async def test_the_customer_view_has_percent_and_series_and_no_money_weights_or_people(
    api: AsyncClient, auth_world: AuthWorld, example: dict[str, Any]
) -> None:
    owner = await token_for(api, "owner")
    view = await ok(api, owner, f"{D}/portal/{example['project']}")
    object_view = await ok(api, owner, f"{D}/object/{example['project']}")
    assert set(view) == {"as_of", "project_id", "name", "status", "planned_start", "planned_end",
                         "progress", "series"}  # fmt: skip
    assert _keys(view).isdisjoint(FORBIDDEN_FOR_PORTAL)
    assert set(view["progress"]) == {"percent", "planned_percent", "deviation"}
    assert set(view["series"][0]) == {"date", "plan_percent", "fact_percent"}
    # заказчику отдаётся то же, что внутреннему ряду, без денег и людей
    assert view["progress"] == {k: object_view["progress"][k] for k in view["progress"]}
    assert view["series"] == [
        {"date": row["date"], "plan_percent": row["plan_percent"],
         "fact_percent": row["fact_percent"]}
        for row in object_view["series"]
    ]  # fmt: skip

    customer = await token_for(api, "customer")
    east = await call(api, customer, f"{D}/portal/{auth_world.projects['east']}")
    assert east.status_code == 200 and _keys(east.json()).isdisjoint(FORBIDDEN_FOR_PORTAL)
    supplier = await token_for(api, "supplier")
    west = await call(api, supplier, f"{D}/portal/{auth_world.projects['west']}")
    assert west.status_code == 403  # у поставщика нет права на ГПР
    foreman = await token_for(api, "foreman")
    other = await call(api, foreman, f"{D}/portal/{example['project']}")
    assert other.status_code == 404
