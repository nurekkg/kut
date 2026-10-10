"""S3a: правила ГПР живут в БД: один объект, живые узел и вид работ, зависимости без циклов,
запрет удалять строку со связями, права на запись по gpr:write."""

import asyncio
from collections.abc import AsyncIterator
from datetime import date
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from tests.auth_support import AuthWorld
from tests.db_support import call
from tests.gpr_support import Plan, add_item, ctx, make_plan, purge, unique

START, END = date(2026, 9, 1), date(2026, 9, 10)
DEPENDENCY = (
    "INSERT INTO schedule_dependency (project_id, legal_entity_id, predecessor_id, successor_id)"
    " VALUES (:p, :le, :a, :b) RETURNING id"
)


@pytest.fixture(scope="module", autouse=True)
async def cleanup(worker_engine: AsyncEngine, auth_world: AuthWorld) -> AsyncIterator[None]:
    yield
    await purge(worker_engine, auth_world)


@pytest.fixture
async def worker(worker_engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """Соединение kut_worker с откатом: правила проверяем, мир не загрязняем."""
    async with worker_engine.connect() as connection:
        yield connection
        await connection.rollback()


async def new_item(connection: AsyncConnection, plan: Plan, node: UUID | None = None) -> UUID:
    """Ещё одна строка ГПР на узле плана с новым видом работ."""
    work = await connection.scalar(
        text(
            "INSERT INTO work (code, name, unit, category)"
            " VALUES (:c, 'x', 'м3', 'S3A') RETURNING id"
        ),
        {"c": unique()},
    )
    return await add_item(
        connection, plan.project, plan.entity, node or plan.node, work, "10", START, END
    )


async def new_item_with_dates(
    connection: AsyncConnection, plan: Plan, start: date, end: date
) -> UUID:
    work = await connection.scalar(
        text(
            "INSERT INTO work (code, name, unit, category)"
            " VALUES (:c, 'x', 'м3', 'S3A') RETURNING id"
        ),
        {"c": unique()},
    )
    return await add_item(connection, plan.project, plan.entity, plan.node, work, "10", start, end)


async def edge(connection: AsyncConnection, plan: Plan, a: UUID, b: UUID) -> Any:
    return await connection.scalar(
        text(DEPENDENCY), {"p": plan.project, "le": plan.entity, "a": a, "b": b}
    )


@pytest.fixture
async def plan(worker_engine: AsyncEngine, auth_world: AuthWorld) -> Plan:
    return await make_plan(worker_engine, auth_world)


async def rule(connection: AsyncConnection, message: str, sql: str, **params: Any) -> None:
    with pytest.raises(IntegrityError, match=message):
        async with connection.begin_nested():
            await connection.execute(text(sql), params)


# --- строка ГПР ---------------------------------------------------------------------------------


async def test_a_node_and_a_work_have_one_live_schedule_row(
    worker: AsyncConnection, plan: Plan
) -> None:
    with pytest.raises(IntegrityError, match="uq_schedule_item_node_id_work_id"):
        async with worker.begin_nested():
            await add_item(worker, plan.project, plan.entity, plan.node, plan.work, "5", START, END)
    await worker.execute(
        text("UPDATE schedule_item SET deleted_at = now() WHERE id = :id"), {"id": plan.item}
    )
    again = await add_item(worker, plan.project, plan.entity, plan.node, plan.work, "5", START, END)
    assert again != plan.item


async def test_the_node_and_the_work_of_a_row_must_be_alive(
    worker: AsyncConnection, plan: Plan
) -> None:
    other = await worker.scalar(
        text(
            "INSERT INTO work (code, name, unit, category)"
            " VALUES (:c, 'x', 'м3', 'S3A') RETURNING id"
        ),
        {"c": unique()},
    )
    await worker.execute(
        text("UPDATE schedule_item SET deleted_at = now() WHERE id = :id"), {"id": plan.item}
    )
    await worker.execute(
        text("UPDATE project_node SET deleted_at = now() WHERE id = :id"), {"id": plan.node}
    )
    with pytest.raises(IntegrityError, match=r"schedule\.node_invalid"):
        async with worker.begin_nested():
            await add_item(worker, plan.project, plan.entity, plan.node, other, "5", START, END)


async def test_a_deleted_work_cannot_be_planned(worker: AsyncConnection, plan: Plan) -> None:
    other = await worker.scalar(
        text(
            "INSERT INTO work (code, name, unit, category)"
            " VALUES (:c, 'x', 'м3', 'S3A') RETURNING id"
        ),
        {"c": unique()},
    )
    await worker.execute(text("UPDATE work SET deleted_at = now() WHERE id = :id"), {"id": other})
    with pytest.raises(IntegrityError, match=r"schedule\.work_invalid"):
        async with worker.begin_nested():
            await add_item(worker, plan.project, plan.entity, plan.node, other, "5", START, END)


async def test_the_node_must_belong_to_the_project_of_the_row(
    worker: AsyncConnection, plan: Plan, auth_world: AuthWorld
) -> None:
    west_node = await worker.scalar(
        text(
            "INSERT INTO project_node (project_id, legal_entity_id, type, name)"
            " SELECT id, legal_entity_id, 'building', :n FROM project WHERE id = :p RETURNING id"
        ),
        {"n": unique(), "p": auth_world.projects["west"]},
    )
    with pytest.raises(IntegrityError, match=r"schedule\.node_invalid"):
        async with worker.begin_nested():
            await new_item(worker, plan, node=west_node)


@pytest.mark.parametrize(
    ("column", "value", "constraint"),
    [
        ("plan_volume", "0", "ck_schedule_item_volume_positive"),
        ("plan_volume", "-5", "ck_schedule_item_volume_positive"),
        ("weight", "0", "ck_schedule_item_weight_positive"),
    ],
)
async def test_volume_and_weight_must_be_positive(
    worker: AsyncConnection, plan: Plan, column: str, value: str, constraint: str
) -> None:
    await rule(
        worker, constraint, f"UPDATE schedule_item SET {column} = :v WHERE id = :id",  # noqa: S608
        v=value, id=plan.item,
    )  # fmt: skip


async def test_the_end_cannot_precede_the_start(worker: AsyncConnection, plan: Plan) -> None:
    await rule(
        worker, "ck_schedule_item_dates_ordered",
        "UPDATE schedule_item SET plan_end = DATE '2026-08-01' WHERE id = :id", id=plan.item,
    )  # fmt: skip


SET_DATES = "UPDATE schedule_item SET plan_start = :s, plan_end = :e WHERE id = :id"


@pytest.mark.parametrize(
    ("start", "end", "constraint"),
    [
        (date(1999, 12, 31), date(2000, 1, 5), "ck_schedule_item_dates_in_range"),
        (date(2026, 9, 1), date(2101, 1, 1), "ck_schedule_item_dates_in_range"),
        (date(1900, 1, 1), date(9999, 12, 31), "ck_schedule_item_dates_in_range"),
        (date(2026, 9, 1), date(2036, 9, 2), "ck_schedule_item_duration_max"),
        (date(2024, 2, 29), date(2034, 3, 1), "ck_schedule_item_duration_max"),
        (date(2000, 1, 1), date(2010, 1, 2), "ck_schedule_item_duration_max"),
    ],
)
async def test_dates_outside_the_limits_are_refused(
    worker: AsyncConnection, plan: Plan, start: date, end: date, constraint: str
) -> None:
    await rule(worker, constraint, SET_DATES, s=start, e=end, id=plan.item)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (date(2000, 1, 1), date(2000, 1, 1)),
        (date(2100, 12, 1), date(2100, 12, 31)),
        (date(2026, 9, 1), date(2036, 9, 1)),
        (date(2024, 2, 29), date(2034, 2, 28)),
        (date(2000, 1, 1), date(2010, 1, 1)),
    ],
)
async def test_the_edges_of_the_limits_are_accepted(
    worker: AsyncConnection, plan: Plan, start: date, end: date
) -> None:
    await worker.execute(text(SET_DATES), {"s": start, "e": end, "id": plan.item})

    got = await worker.execute(
        text("SELECT plan_start, plan_end FROM schedule_item WHERE id = :id"), {"id": plan.item}
    )
    assert got.one() == (start, end)


async def test_a_new_row_outside_the_limits_is_refused_too(
    worker: AsyncConnection, plan: Plan
) -> None:
    with pytest.raises(IntegrityError, match="ck_schedule_item_dates_in_range"):
        async with worker.begin_nested():
            await new_item_with_dates(worker, plan, date(2026, 9, 1), date(9999, 12, 31))


@pytest.mark.parametrize("column", ["node_id", "work_id", "project_id", "legal_entity_id"])
async def test_the_identity_of_a_row_cannot_be_changed(
    worker: AsyncConnection, plan: Plan, column: str
) -> None:
    await rule(
        worker, "is immutable",
        f"UPDATE schedule_item SET {column} = gen_random_uuid() WHERE id = :id",  # noqa: S608
        id=plan.item,
    )  # fmt: skip


# --- зависимости ------------------------------------------------------------------------------


async def test_a_dependency_cannot_close_a_cycle_of_any_length(
    worker: AsyncConnection, plan: Plan
) -> None:
    a, b, c = plan.item, await new_item(worker, plan), await new_item(worker, plan)
    await edge(worker, plan, a, b)
    await edge(worker, plan, b, c)
    for back in ((b, a), (c, a), (c, b)):
        with pytest.raises(IntegrityError, match=r"dependency\.cycle"):
            async with worker.begin_nested():
                await edge(worker, plan, *back)
    with pytest.raises(IntegrityError, match=r"dependency\.cycle"):
        async with worker.begin_nested():
            await edge(worker, plan, a, a)
    assert await edge(worker, plan, a, c)  # обходной путь вперёд - не цикл


async def test_a_dependency_needs_two_live_rows_of_the_same_project(
    worker: AsyncConnection, plan: Plan, auth_world: AuthWorld
) -> None:
    west_node = await worker.scalar(
        text(
            "INSERT INTO project_node (project_id, legal_entity_id, type, name)"
            " SELECT id, legal_entity_id, 'building', :n FROM project WHERE id = :p RETURNING id"
        ),
        {"n": unique(), "p": auth_world.projects["west"]},
    )
    west = Plan(
        project=auth_world.projects["west"], entity=plan.entity, node=west_node,
        work=plan.work, item=plan.item,
    )  # fmt: skip
    west_item = await worker.scalar(
        text(
            "INSERT INTO schedule_item (project_id, legal_entity_id, node_id, work_id, plan_start,"
            " plan_end, plan_volume) SELECT p.id, p.legal_entity_id, :n, :w, :s, :e, 1"
            " FROM project p WHERE p.id = :p RETURNING id"
        ),
        {"n": west_node, "w": plan.work, "s": START, "e": END, "p": west.project},
    )
    with pytest.raises(IntegrityError, match=r"dependency\.item_invalid"):
        async with worker.begin_nested():
            await edge(worker, plan, plan.item, west_item)

    deleted = await new_item(worker, plan)
    await worker.execute(
        text("UPDATE schedule_item SET deleted_at = now() WHERE id = :id"), {"id": deleted}
    )
    with pytest.raises(IntegrityError, match=r"dependency\.item_invalid"):
        async with worker.begin_nested():
            await edge(worker, plan, plan.item, deleted)


async def test_a_pair_has_one_live_dependency_and_a_deleted_one_returns_only_without_a_cycle(
    worker: AsyncConnection, plan: Plan
) -> None:
    a, b = plan.item, await new_item(worker, plan)
    first = await edge(worker, plan, a, b)
    with pytest.raises(IntegrityError, match="uq_schedule_dependency_predecessor_id_successor_id"):
        async with worker.begin_nested():
            await edge(worker, plan, a, b)

    await worker.execute(
        text("UPDATE schedule_dependency SET deleted_at = now() WHERE id = :id"), {"id": first}
    )
    await edge(worker, plan, b, a)  # теперь обратная связь допустима
    with pytest.raises(IntegrityError, match=r"dependency\.cycle"):
        async with worker.begin_nested():
            await worker.execute(
                text("UPDATE schedule_dependency SET deleted_at = NULL WHERE id = :id"),
                {"id": first},
            )


async def test_the_endpoints_of_a_dependency_cannot_be_changed(
    worker: AsyncConnection, plan: Plan
) -> None:
    b = await new_item(worker, plan)
    dependency = await edge(worker, plan, plan.item, b)
    await rule(
        worker, "is immutable",
        "UPDATE schedule_dependency SET successor_id = predecessor_id WHERE id = :id",
        id=dependency,
    )  # fmt: skip


async def test_two_transactions_cannot_close_a_cycle_together(
    worker_engine: AsyncEngine, auth_world: AuthWorld
) -> None:
    """Каждая транзакция по отдельности безвредна, вместе они замыкают цикл: БД их сериализует."""
    plan = await make_plan(worker_engine, auth_world)
    async with worker_engine.begin() as connection:
        b = await new_item(connection, plan)
    first = await worker_engine.connect()
    second = await worker_engine.connect()
    try:
        await first.execute(
            text(DEPENDENCY), {"p": plan.project, "le": plan.entity, "a": plan.item, "b": b}
        )
        racing = asyncio.create_task(
            second.execute(
                text(DEPENDENCY), {"p": plan.project, "le": plan.entity, "a": b, "b": plan.item}
            )
        )
        await asyncio.sleep(0.5)
        assert not racing.done()  # вторая ждёт блокировку объекта
        await first.commit()
        with pytest.raises(IntegrityError, match=r"dependency\.cycle"):
            await racing
    finally:
        await first.close()
        await second.close()


# --- запрет удаления ---------------------------------------------------------------------------


async def test_a_row_with_dependencies_or_facts_cannot_be_deleted(
    worker: AsyncConnection, plan: Plan, auth_world: AuthWorld
) -> None:
    b = await new_item(worker, plan)
    dependency = await edge(worker, plan, plan.item, b)
    soft_delete = "UPDATE schedule_item SET deleted_at = now() WHERE id = :id"
    await rule(worker, r"schedule\.has_dependents", soft_delete, id=plan.item)
    await worker.execute(
        text("UPDATE schedule_dependency SET deleted_at = now() WHERE id = :id"), {"id": dependency}
    )
    await worker.execute(
        text(
            "INSERT INTO work_actual (project_id, legal_entity_id, node_id, work_id,"
            " schedule_item_id, volume, work_date, author_id, status, approved_by, approved_at)"
            " VALUES (:p, :le, :n, :w, :i, 1, DATE '2026-09-02', :u, 'approved', :u, now())"
        ),
        {
            "p": plan.project, "le": plan.entity, "n": plan.node, "w": plan.work, "i": plan.item,
            "u": auth_world.users["foreman"],
        },
    )  # fmt: skip
    await rule(worker, r"schedule\.has_dependents", soft_delete, id=plan.item)


async def test_a_node_with_a_schedule_row_or_a_fact_cannot_be_deleted(
    worker: AsyncConnection, plan: Plan
) -> None:
    await rule(
        worker, r"node\.has_dependents",
        "UPDATE project_node SET deleted_at = now() WHERE id = :id", id=plan.node,
    )  # fmt: skip
    await worker.execute(
        text("UPDATE schedule_item SET deleted_at = now() WHERE id = :id"), {"id": plan.item}
    )
    await worker.execute(
        text("UPDATE project_node SET deleted_at = now() WHERE id = :id"), {"id": plan.node}
    )  # теперь свободен


async def test_a_work_with_rows_cannot_be_deleted_or_change_its_unit(
    worker: AsyncConnection, plan: Plan
) -> None:
    await rule(
        worker, r"work\.has_dependents",
        "UPDATE work SET deleted_at = now() WHERE id = :id", id=plan.work,
    )  # fmt: skip
    await rule(
        worker, r"work\.unit_locked", "UPDATE work SET unit = 'м2' WHERE id = :id", id=plan.work
    )
    await worker.execute(
        text("UPDATE work SET name = 'Новое имя' WHERE id = :id"), {"id": plan.work}
    )


# --- права в БД -----------------------------------------------------------------------------------


INSERT_ITEM = (
    "INSERT INTO schedule_item (project_id, legal_entity_id, node_id, work_id, plan_start,"
    " plan_end, plan_volume) VALUES (:p, :le, :n, :w, :s, :e, 1)"
)


@pytest.mark.parametrize("who", ["foreman", "techdir", "estimator"])
async def test_roles_with_gpr_write_may_plan(
    auth_world: AuthWorld, plan: Plan, who: str, worker_engine: AsyncEngine
) -> None:
    other = await make_plan(worker_engine, auth_world, with_item=False)
    await call(
        ctx(auth_world, who), INSERT_ITEM,
        p=other.project, le=other.entity, n=other.node, w=other.work, s=START, e=END,
    )  # fmt: skip


@pytest.mark.parametrize("who", ["master", "owner", "finance", "storekeeper"])
async def test_roles_without_gpr_write_cannot_plan_even_through_sql(
    auth_world: AuthWorld, worker_engine: AsyncEngine, who: str
) -> None:
    other = await make_plan(worker_engine, auth_world, with_item=False)
    with pytest.raises(IntegrityError, match=r"schedule\.forbidden"):
        await call(
            ctx(auth_world, who), INSERT_ITEM,
            p=other.project, le=other.entity, n=other.node, w=other.work, s=START, e=END,
        )  # fmt: skip


async def test_external_roles_read_the_schedule_of_their_object_without_the_responsible(
    auth_world: AuthWorld, plan: Plan
) -> None:
    for who in ("customer", "sub_a"):
        rows = await call(
            ctx(auth_world, who),
            "SELECT id, plan_volume, weight FROM schedule_item WHERE node_id = :n",
            portal=True,
            n=plan.node,
        )
        assert [row.id for row in rows] == [plan.item]
        with pytest.raises(DBAPIError, match="permission denied"):
            await call(
                ctx(auth_world, who), "SELECT responsible_user_id FROM schedule_item", portal=True
            )
        with pytest.raises(DBAPIError, match="permission denied"):
            await call(
                ctx(auth_world, who), INSERT_ITEM, portal=True,
                p=plan.project, le=plan.entity, n=plan.node, w=plan.work, s=START, e=END,
            )  # fmt: skip


async def test_an_external_role_of_another_object_sees_no_schedule_rows(
    auth_world: AuthWorld, plan: Plan
) -> None:
    rows = await call(
        ctx(auth_world, "sub_b", project="west"),
        "SELECT id FROM schedule_item WHERE node_id = :n",
        portal=True,
        n=plan.node,
    )
    assert rows == []


async def test_external_roles_see_only_the_works_of_their_own_schedule(
    auth_world: AuthWorld, plan: Plan, worker_engine: AsyncEngine
) -> None:
    unplanned = await make_plan(worker_engine, auth_world, with_item=False)
    rows = await call(
        ctx(auth_world, "customer"),
        "SELECT id FROM work WHERE id = ANY(:ids)",
        portal=True,
        ids=[plan.work, unplanned.work],
    )
    assert {row.id for row in rows} == {plan.work}
    with pytest.raises(DBAPIError, match="permission denied"):
        await call(ctx(auth_world, "customer"), "SELECT created_by FROM work", portal=True)
