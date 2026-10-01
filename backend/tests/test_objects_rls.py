"""S2: RLS узлов дерева и справочника работ (владелец, техдир, прораб, мастер, внешние, портал)."""

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.context import RequestContext, use_context
from app.core.db import get_portal_sessionmaker, get_sessionmaker
from tests.support import World, external, internal

PORTAL_VISIBLE = (
    "id, project_id, parent_id, type, name, code, sort_order, path, planned_start, planned_end,"
    " polygon"
)


async def run(
    sql: str, context: RequestContext, *, portal: bool = False, **params: object
) -> list[tuple[object, ...]]:
    maker = get_portal_sessionmaker() if portal else get_sessionmaker()
    with use_context(context):
        async with maker() as session:
            result = await session.execute(text(sql), params)
            rows = [tuple(row) for row in result] if result.returns_rows else []
            await session.commit()
            return rows


async def node_ids(context: RequestContext, *, portal: bool = False) -> set[UUID]:
    rows = await run("SELECT id FROM project_node WHERE name LIKE 'RLS-%'", context, portal=portal)
    return {row[0] for row in rows}  # type: ignore[misc]


@pytest.fixture(scope="module")
async def tree(world: World, worker_engine: AsyncEngine) -> AsyncIterator[dict[str, UUID]]:
    ids = {name: uuid4() for name in ("b1", "s1", "f1", "r1", "b2")}
    layout = (
        ("b1", "p1", "le1", None, "building"),
        ("s1", "p1", "le1", "b1", "section"),
        ("f1", "p1", "le1", "s1", "floor"),
        ("r1", "p1", "le1", "f1", "room"),
        ("b2", "p2", "le2", None, "building"),
    )
    async with worker_engine.begin() as connection:
        for key, project, entity, parent, kind in layout:
            await connection.execute(
                text(
                    "INSERT INTO project_node (id, project_id, legal_entity_id, parent_id, type,"
                    " name, foreman_user_id) VALUES (:id, :p, :le, :parent, :type, :name, :foreman)"
                ),
                {
                    "id": ids[key],
                    "p": getattr(world, project),
                    "le": getattr(world, entity),
                    "parent": ids[parent] if parent else None,
                    "type": kind,
                    "name": f"RLS-{key}",
                    "foreman": world.foreman_user,
                },
            )
    yield ids
    async with worker_engine.begin() as connection:
        for kind in ("room", "floor", "section", "building", "stage"):  # листья раньше родителей
            await connection.execute(
                text(
                    "UPDATE project_node SET deleted_at = now() WHERE name LIKE 'RLS-%'"
                    " AND type = :t AND deleted_at IS NULL"
                ),
                {"t": kind},
            )


# --- пустой и чужой контекст ----------------------------------------------------------------


async def test_an_empty_context_sees_no_nodes_through_either_login(
    tree: dict[str, UUID], world: World
) -> None:
    empty = RequestContext(user_id=None, is_internal=True)
    assert await node_ids(empty) == set()
    assert await node_ids(RequestContext(user_id=None, is_internal=False), portal=True) == set()


async def test_the_owner_sees_the_nodes_of_both_objects(
    tree: dict[str, UUID], world: World
) -> None:
    assert await node_ids(world.owner()) == set(tree.values())


async def test_a_foreman_sees_only_the_nodes_of_his_object(
    tree: dict[str, UUID], world: World
) -> None:
    assert await node_ids(world.foreman()) == {tree[k] for k in ("b1", "s1", "f1", "r1")}


async def test_a_user_with_no_objects_sees_no_nodes(tree: dict[str, UUID]) -> None:
    assert await node_ids(internal(uuid4())) == set()


async def test_an_external_context_through_the_application_login_sees_nothing(
    tree: dict[str, UUID], world: World
) -> None:
    # Внешние роли ходят через kut_portal; политики kut_app требуют внутренний контекст.
    assert await node_ids(world.customer_ctx()) == set()
    assert await node_ids(world.sub("b")) == set()


# --- портал -----------------------------------------------------------------------------------


async def test_the_customer_sees_the_nodes_of_his_own_object_only(
    tree: dict[str, UUID], world: World
) -> None:
    assert await node_ids(world.customer_ctx(), portal=True) == {
        tree[k] for k in ("b1", "s1", "f1", "r1")
    }


async def test_subcontractors_see_their_own_object_and_never_the_other_one(
    tree: dict[str, UUID], world: World
) -> None:
    assert await node_ids(world.sub("a"), portal=True) == {
        tree[k] for k in ("b1", "s1", "f1", "r1")
    }
    assert await node_ids(world.sub("b"), portal=True) == set(tree.values())  # b: p1 и p2
    stranger = external(uuid4(), world.supplier, projects=[])
    assert await node_ids(stranger, portal=True) == set()


async def test_the_portal_reads_the_listed_columns_but_not_the_people_or_the_service_fields(
    tree: dict[str, UUID], world: World
) -> None:
    rows = await run(
        f"SELECT {PORTAL_VISIBLE} FROM project_node WHERE id = :id",  # noqa: S608
        world.customer_ctx(),
        portal=True,
        id=tree["b1"],
    )
    assert len(rows) == 1
    for column in (
        "foreman_user_id",
        "master_user_id",
        "created_by",
        "updated_by",
        "legal_entity_id",
    ):
        with use_context(world.customer_ctx()):
            async with get_portal_sessionmaker()() as session:
                with pytest.raises(ProgrammingError, match="permission denied"):
                    await session.execute(text(f"SELECT {column} FROM project_node"))  # noqa: S608
    with use_context(world.customer_ctx()):
        async with get_portal_sessionmaker()() as session:
            with pytest.raises(ProgrammingError, match="permission denied"):
                await session.execute(text("SELECT * FROM project_node"))


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE project_node SET name = 'hacked'",
        "UPDATE project_node SET deleted_at = now()",
        "INSERT INTO project_node (project_id, legal_entity_id, type, name)"
        " VALUES (gen_random_uuid(), gen_random_uuid(), 'building', 'x')",
        "INSERT INTO work (code, name, unit, category) VALUES ('X', 'x', 'шт', 'x')",
    ],
)
async def test_every_write_to_nodes_or_works_through_the_portal_pool_is_refused(
    tree: dict[str, UUID], world: World, statement: str
) -> None:
    for context in (world.customer_ctx(), world.sub("a")):
        with use_context(context):
            async with get_portal_sessionmaker()() as session:
                with pytest.raises(ProgrammingError, match="permission denied"):
                    await session.execute(text(statement))


async def test_the_portal_pool_cannot_read_the_works_catalog(world: World) -> None:
    with use_context(world.customer_ctx()):
        async with get_portal_sessionmaker()() as session:
            with pytest.raises(ProgrammingError, match="permission denied"):
                await session.execute(text("SELECT id FROM work"))


# --- запись внутренних пользователей ------------------------------------------------------------


async def test_a_foreman_writes_nodes_of_his_object_but_not_of_another(
    tree: dict[str, UUID], world: World
) -> None:
    context = world.foreman()
    await run(
        "INSERT INTO project_node (project_id, legal_entity_id, type, name)"
        " VALUES (:p, :le, 'stage', 'RLS-own')",
        context,
        p=world.p1,
        le=world.le1,
    )
    with pytest.raises(DBAPIError, match="row-level security"):
        await run(
            "INSERT INTO project_node (project_id, legal_entity_id, type, name)"
            " VALUES (:p, :le, 'stage', 'RLS-foreign')",
            context,
            p=world.p2,
            le=world.le2,
        )
    changed = await run(
        "UPDATE project_node SET name = 'RLS-b2-renamed' WHERE id = :id RETURNING id",
        context,
        id=tree["b2"],
    )
    assert changed == []  # чужой узел для прораба не существует: ни строки, ни ошибки
    renamed = await run(
        "UPDATE project_node SET sort_order = 7 WHERE id = :id RETURNING id", context, id=tree["b1"]
    )
    assert len(renamed) == 1


async def test_a_write_cannot_move_a_node_into_an_object_the_user_does_not_have(
    tree: dict[str, UUID], world: World
) -> None:
    with pytest.raises(DBAPIError, match=r"immutable|row-level security|node"):
        await run(
            "UPDATE project_node SET project_id = :p WHERE id = :id",
            world.foreman(),
            p=world.p2,
            id=tree["b1"],
        )


async def test_works_are_readable_and_writable_by_internal_roles_and_closed_to_the_rest(
    world: World,
) -> None:
    code = f"RLS-{uuid4().hex[:6]}"
    created = await run(
        "INSERT INTO work (code, name, unit, category) VALUES (:c, 'Тест', 'м3', 'Тест')"
        " RETURNING id",
        world.foreman(),
        c=code,
    )
    assert len(created) == 1
    assert await run("SELECT id FROM work WHERE code = :c", world.owner(), c=code) == created
    # kut_app с внешним контекстом: политики требуют внутреннего пользователя
    assert await run("SELECT id FROM work WHERE code = :c", world.customer_ctx(), c=code) == []
    with pytest.raises(DBAPIError, match="row-level security"):
        await run(
            "INSERT INTO work (code, name, unit, category) VALUES ('X', 'x', 'шт', 'x')",
            world.customer_ctx(),
        )
    assert await run("SELECT id FROM work", RequestContext(user_id=None, is_internal=False)) == []
    await run("UPDATE work SET deleted_at = now() WHERE code = :c", world.owner(), c=code)
