"""S2: производительность дерева на 10 000 узлов (ТЗ §10.1: p95 API < 500 мс).

Сид 10 665 узлов одним объектом, 20 прогонов GET /tree через полный стек (токен, RLS, сериализация)
и EXPLAIN ANALYZE выборки под контекстом прораба. Цифры печатаются (pytest -s), порог щедрый:
тест страхует от порядкового провала, а не измеряет железо.
"""

import math
import statistics
import time
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.context import use_context
from app.core.db import get_sessionmaker
from app.modules.projects import service
from tests.auth_support import AuthWorld, bearer, token_for
from tests.support import internal

STAGES, BUILDINGS, SECTIONS, FLOORS, ROOMS = 5, 4, 4, 12, 10
EXPECTED = (
    STAGES
    + STAGES * BUILDINGS
    + STAGES * BUILDINGS * SECTIONS
    + STAGES * BUILDINGS * SECTIONS * FLOORS
    + STAGES * BUILDINGS * SECTIONS * FLOORS * ROOMS
)
RUNS = 20
P95_LIMIT_SECONDS = 1.5

LEVELS = (
    # (тип, родитель-уровень или NULL, сколько на родителя)
    ("stage", None, STAGES),
    ("building", "stage", BUILDINGS),
    ("section", "building", SECTIONS),
    ("floor", "section", FLOORS),
    ("room", "floor", ROOMS),
)


@pytest.fixture(scope="module")
async def big_project(auth_world: AuthWorld, worker_engine: AsyncEngine) -> AsyncIterator[UUID]:
    """Объект юрлица Восток с 10 665 узлами; прораб получает к нему доступ."""
    project = uuid4()
    async with worker_engine.begin() as connection:
        entity = await connection.scalar(
            text("SELECT legal_entity_id FROM project WHERE id = :id"),
            {"id": auth_world.projects["east"]},
        )
        await connection.execute(
            text("INSERT INTO project (id, legal_entity_id, name) VALUES (:id, :le, 'PERF-10k')"),
            {"id": project, "le": entity},
        )
        await connection.execute(
            text("INSERT INTO user_project_access (user_id, project_id) VALUES (:u, :p)"),
            {"u": auth_world.users["foreman"], "p": project},
        )
        for kind, parent_kind, count in LEVELS:
            if parent_kind is None:
                await connection.execute(
                    text(
                        "INSERT INTO project_node (project_id, legal_entity_id, type, name,"
                        " code, sort_order) SELECT :p, :le, :kind, 'Узел ' || g, g::text, g"
                        " FROM generate_series(1, :n) g"
                    ),
                    {"p": project, "le": entity, "kind": kind, "n": count},
                )
                continue
            await connection.execute(
                text(
                    "INSERT INTO project_node (project_id, legal_entity_id, parent_id, type,"
                    " name, code, sort_order) SELECT :p, :le, parent.id, :kind, 'Узел ' || g,"
                    " g::text, g FROM project_node parent CROSS JOIN generate_series(1, :n) g"
                    " WHERE parent.project_id = :p AND parent.type = :parent_kind"
                ),
                {"p": project, "le": entity, "kind": kind, "n": count, "parent_kind": parent_kind},
            )
        await connection.execute(text("ANALYZE project_node"))
    yield project
    async with worker_engine.begin() as connection:
        for kind in ("room", "floor", "section", "building", "stage"):
            await connection.execute(
                text(
                    "UPDATE project_node SET deleted_at = now()"
                    " WHERE project_id = :p AND type = :t AND deleted_at IS NULL"
                ),
                {"p": project, "t": kind},
            )
        await connection.execute(
            text("UPDATE project SET deleted_at = now() WHERE id = :p"), {"p": project}
        )


def percentiles(values: list[float]) -> tuple[float, float, float]:
    """p50, p95 и максимум в миллисекундах (p95 по методу ближайшего ранга)."""
    ordered = sorted(values)
    p95 = ordered[max(math.ceil(len(ordered) * 0.95) - 1, 0)]
    return statistics.median(ordered) * 1000, p95 * 1000, ordered[-1] * 1000


async def test_the_tree_of_ten_thousand_nodes_loads_within_the_budget(
    api: AsyncClient, big_project: UUID, auth_world: AuthWorld
) -> None:
    token = await token_for(api, "foreman")
    url = f"/api/v1/projects/{big_project}/tree"

    http: list[float] = []
    size = 0
    for _ in range(RUNS):
        started = time.perf_counter()
        response = await api.get(url, headers=bearer(token))
        http.append(time.perf_counter() - started)
        assert response.status_code == 200
        size = len(response.content)
        assert len(response.json()) == EXPECTED

    # Только слой данных (выборка, RLS, сборка JSON в БД): без токена, HTTP и копирования тела.
    context = internal(auth_world.users["foreman"], projects=[big_project])
    data: list[float] = []
    for _ in range(RUNS):
        with use_context(context):
            async with get_sessionmaker()() as session:
                started = time.perf_counter()
                document = await service.tree_json(session, big_project, internal=True)
                data.append(time.perf_counter() - started)
        assert document.startswith("[")

    with use_context(context):
        async with get_sessionmaker()() as session:
            plan = list(
                await session.scalars(
                    text("EXPLAIN (ANALYZE, BUFFERS) " + str(service._TREE_SQL[True])),
                    {"project": big_project},
                )
            )

    http_p50, http_p95, http_max = percentiles(http)
    data_p50, data_p95, data_max = percentiles(data)
    print(f"\nPERF nodes={EXPECTED} runs={RUNS} bytes={size}")
    print(f"PERF GET /tree     p50={http_p50:.0f} ms p95={http_p95:.0f} ms max={http_max:.0f} ms")
    print(f"PERF tree_json     p50={data_p50:.0f} ms p95={data_p95:.0f} ms max={data_max:.0f} ms")
    print("PERF GET /tree timings ms:", " ".join(f"{value * 1000:.0f}" for value in http))
    print("PERF tree_json timings ms:", " ".join(f"{value * 1000:.0f}" for value in data))
    print("PERF EXPLAIN (ANALYZE, BUFFERS) under the foreman's RLS context:")
    print("\n".join(plan))
    assert http_p95 < P95_LIMIT_SECONDS * 1000, f"p95={http_p95:.0f} ms"
