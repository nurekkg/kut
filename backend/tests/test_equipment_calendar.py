"""Календарь занятости техники (ADR-014 часть 3 §6, §9 SE-1): окно, состав, замер."""

import statistics
import time
from datetime import date, timedelta

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.auth_support import AuthWorld
from tests.equipment_support import API, assign, day, east, headers_for, new_unit


async def test_the_window_is_limited_to_sixty_two_days(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    head = await headers_for(api, "equipment_head")
    too_long = await api.get(
        f"{API}/calendar", params={"date_from": "2031-01-01", "date_to": "2031-03-04"}, headers=head
    )
    assert too_long.status_code == 422
    assert too_long.json() == {"detail": "equipment.calendar_window_invalid"}
    backwards = await api.get(
        f"{API}/calendar", params={"date_from": "2031-03-04", "date_to": "2031-03-01"}, headers=head
    )
    assert backwards.status_code == 422


async def test_the_calendar_lists_assignments_with_hours_per_day(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    head = await headers_for(api, "equipment_head")
    unit = await new_unit(api, head, auth_world)
    created = await assign(
        api, head, unit_id=unit["id"], project_id=east(auth_world), start=100, end=102, hours="8"
    )
    assert created.status_code == 201
    response = await api.get(
        f"{API}/calendar",
        params={"date_from": day(99), "date_to": day(103), "limit": 2000},
        headers=head,
    )
    body = response.json()
    ids = [item["id"] for item in body["units"]]
    assert unit["id"] in ids, (response.status_code, len(ids), unit["id"], body.get("detail"))
    row = next(item for item in body["units"] if item["id"] == unit["id"])
    assert [float(item["hours"]) for item in row["days"]] == [8.0, 8.0, 8.0]
    assert row["assignments"][0]["project_name"] == "ЖК «Восток» (dev)"


async def test_the_calendar_for_eight_weeks_over_three_hundred_units(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    """Замер: окно ±4 недели (57 дней) по всем единицам демо-парка. Цель - не дольше одной секунды
    (p95, ADR-014 раздел 9). Числа печатаются для evidence (pytest -s)."""
    head = await headers_for(api, "equipment_head")
    today = date.today()
    params = {
        "date_from": (today - timedelta(days=28)).isoformat(),
        "date_to": (today + timedelta(days=28)).isoformat(),
        "limit": 2000,
    }
    async with worker_engine.connect() as connection:
        units = (
            await connection.execute(
                text("SELECT count(*) FROM equipment_unit WHERE deleted_at IS NULL")
            )
        ).scalar_one()
    for _ in range(2):
        await api.get(f"{API}/calendar", params=params, headers=head)
    timings: list[float] = []
    for _ in range(20):
        started = time.perf_counter()
        response = await api.get(f"{API}/calendar", params=params, headers=head)
        timings.append(time.perf_counter() - started)
        assert response.status_code == 200
    p50 = statistics.median(timings)
    p95 = sorted(timings)[int(len(timings) * 0.95) - 1]
    print(f"\ncalendar units={units} window=57 days p50={p50:.3f}s p95={p95:.3f}s")
    assert units >= 300
    assert p95 <= 1.0, f"p95 {p95:.3f}s больше цели 1 с"


async def test_the_plans_of_the_calendar_queries_are_recorded(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    """EXPLAIN (ANALYZE, BUFFERS) запросов календаря под RLS руководителя (evidence, pytest -s)."""
    from app.core.context import RequestContext, use_context
    from app.core.db import get_sessionmaker
    from app.modules.auth import devseed

    head = RequestContext(
        user_id=auth_world.users["equipment_head"],
        is_internal=True,
        project_ids=frozenset(auth_world.projects.values()),
        legal_entity_ids=frozenset(auth_world.legal_entities.values()),
        counterparty_id=None,
        request_id=None,
        sees_all_users=True,
    )
    units_sql = text(
        "EXPLAIN (ANALYZE, BUFFERS) SELECT u.id, t.code FROM equipment_unit u JOIN equipment_type t"
        " ON t.id = u.equipment_type_id WHERE u.deleted_at IS NULL"
        " ORDER BY t.code, u.registry_number, u.id LIMIT 1000"
    )
    assignments_sql = text(
        "EXPLAIN (ANALYZE, BUFFERS) SELECT a.id, p.name FROM equipment_assignment a JOIN project p"
        " ON p.id = a.project_id WHERE a.equipment_unit_id = ANY(ARRAY(SELECT id"
        " FROM equipment_unit"
        " WHERE deleted_at IS NULL LIMIT 1000)) AND a.status = 'planned' AND a.deleted_at IS NULL"
        " AND a.date_from <= DATE '2031-06-12' AND a.date_to >= DATE '2031-06-08'"
    )
    _ = devseed.LE_EAST
    with use_context(head):
        async with get_sessionmaker()() as session:
            for label, statement in (("units", units_sql), ("assignments", assignments_sql)):
                plan = [row[0] for row in await session.execute(statement)]
                print(f"\nEXPLAIN {label}:\n" + "\n".join(plan))
                assert plan
