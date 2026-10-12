"""S2: правила дерева объекта живут в БД: их не обойти ни сидом, ни воркером, ни новым модулем."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.modules.objects.models import ALLOWED_CHILDREN, NodeType
from tests.auth_support import AuthWorld
from tests.support import World

SOFT_DELETE = "UPDATE project_node SET deleted_at = now() WHERE id = :id"
SET_PATH = "UPDATE project_node SET path = ARRAY[gen_random_uuid()] WHERE id = :id"

# Один родитель каждого типа получает допустимую цепочку предков: корень объекта может быть
# только этапом или корпусом, дальше по матрице.
CHAIN: dict[NodeType, tuple[NodeType, ...]] = {
    NodeType.STAGE: (NodeType.STAGE,),
    NodeType.BUILDING: (NodeType.BUILDING,),
    NodeType.SECTION: (NodeType.BUILDING, NodeType.SECTION),
    NodeType.FLOOR: (NodeType.BUILDING, NodeType.SECTION, NodeType.FLOOR),
    NodeType.ROOM: (NodeType.BUILDING, NodeType.SECTION, NodeType.FLOOR, NodeType.ROOM),
}


@pytest.fixture
async def worker(worker_engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """Соединение kut_worker с откатом: правила проверяем, мир не загрязняем."""
    async with worker_engine.connect() as connection:
        yield connection
        await connection.rollback()


async def add(
    connection: AsyncConnection,
    world: World,
    kind: NodeType,
    parent: UUID | None = None,
    *,
    project: str = "p1",
    name: str | None = None,
    code: str | None = None,
    **extra: Any,
) -> UUID:
    node_id = uuid4()
    project_id = getattr(world, project)
    entity = world.le1 if project == "p1" else world.le2
    params: dict[str, Any] = {
        "id": node_id,
        "project": project_id,
        "entity": entity,
        "parent": parent,
        "type": kind.value,
        "name": f"{kind.value}-{node_id.hex[:6]}" if name is None else name,
        "code": code,
        "polygon": None,
        **extra,
    }
    await connection.execute(
        text(
            "INSERT INTO project_node (id, project_id, legal_entity_id, parent_id, type, name,"
            " code, planned_start, planned_end, polygon) VALUES (:id, :project, :entity, :parent,"
            " :type, :name, :code, :planned_start, :planned_end, CAST(:polygon AS jsonb))"
        ),
        {"planned_start": None, "planned_end": None, **params},
    )
    return node_id


async def chain(
    connection: AsyncConnection, world: World, kind: NodeType, *, project: str = "p1"
) -> UUID:
    parent: UUID | None = None
    for step in CHAIN[kind]:
        parent = await add(connection, world, step, parent, project=project)
    assert parent is not None
    return parent


@asynccontextmanager
async def rule(connection: AsyncConnection, message: str) -> AsyncIterator[None]:
    """Блок обязан упасть с правилом БД; savepoint оставляет транзакцию пригодной."""
    with pytest.raises(IntegrityError, match=message):
        async with connection.begin_nested():
            yield


async def fails(connection: AsyncConnection, message: str, statement: str, **params: Any) -> None:
    async with rule(connection, message):
        await connection.execute(text(statement), params)


async def path_of(connection: AsyncConnection, node_id: UUID) -> list[UUID]:
    value = await connection.scalar(
        text("SELECT path FROM project_node WHERE id = :id"), {"id": node_id}
    )
    return list(value)


# --- матрица допустимых типов ---------------------------------------------------------------


async def test_the_database_matrix_equals_the_python_table(worker: AsyncConnection) -> None:
    for parent, allowed in ALLOWED_CHILDREN.items():
        for child in NodeType:
            result = await worker.scalar(
                text("SELECT app.project_node_child_allowed(:p, :c)"),
                {"p": parent.value if parent else None, "c": child.value},
            )
            assert result is (child in allowed), (parent, child)


@pytest.mark.parametrize("child", list(NodeType))
@pytest.mark.parametrize("parent", [None, *NodeType])
async def test_every_parent_and_child_type_pair_is_enforced_on_insert(
    world: World, worker: AsyncConnection, parent: NodeType | None, child: NodeType
) -> None:
    parent_id = await chain(worker, world, parent) if parent else None
    if child in ALLOWED_CHILDREN[parent]:
        node = await add(worker, world, child, parent_id)
        assert (await path_of(worker, node))[-1] == node
    else:
        async with rule(worker, r"node\.type_not_allowed"):
            await add(worker, world, child, parent_id)


async def test_a_floor_cannot_hold_a_building_and_a_room_holds_nothing(
    world: World, worker: AsyncConnection
) -> None:
    floor = await chain(worker, world, NodeType.FLOOR)
    room = await add(worker, world, NodeType.ROOM, floor)
    for child in NodeType:
        async with rule(worker, r"node\.type_not_allowed"):
            await add(worker, world, child, room)
    async with rule(worker, r"node\.type_not_allowed"):
        await add(worker, world, NodeType.BUILDING, floor)


async def test_changing_the_type_is_checked_against_the_parent_and_the_children(
    world: World, worker: AsyncConnection
) -> None:
    building = await add(worker, world, NodeType.BUILDING)
    section = await add(worker, world, NodeType.SECTION, building)
    floor = await add(worker, world, NodeType.FLOOR, section)

    await fails(
        worker, r"node\.type_not_allowed", "UPDATE project_node SET type = 'room' WHERE id = :id",
        id=section,
    )  # fmt: skip
    await fails(
        worker, r"node\.type_not_allowed", "UPDATE project_node SET type = 'stage' WHERE id = :id",
        id=section,
    )  # fmt: skip
    # building -> stage: корень этапа допустим, дочерняя секция под этапом тоже
    await worker.execute(
        text("UPDATE project_node SET type = 'stage' WHERE id = :id"), {"id": building}
    )
    # section -> floor: пока под секцией этаж, нельзя (этаж под этажом)
    await fails(
        worker, r"node\.type_not_allowed", "UPDATE project_node SET type = 'floor' WHERE id = :id",
        id=section,
    )  # fmt: skip
    assert (await path_of(worker, floor))[0] == building


# --- цикл, чужой объект, юрлицо ---------------------------------------------------------------


async def test_a_node_cannot_become_its_own_ancestor(world: World, worker: AsyncConnection) -> None:
    building = await add(worker, world, NodeType.BUILDING)
    section = await add(worker, world, NodeType.SECTION, building)
    floor = await add(worker, world, NodeType.FLOOR, section)

    for node, new_parent in (
        (building, floor),
        (building, section),
        (section, floor),
        (floor, floor),
    ):
        await fails(
            worker,
            r"node\.cycle",
            "UPDATE project_node SET parent_id = :parent WHERE id = :id",
            id=node,
            parent=new_parent,
        )


async def test_the_parent_must_belong_to_the_same_project(
    world: World, worker: AsyncConnection
) -> None:
    other = await add(worker, world, NodeType.BUILDING, project="p2")
    async with rule(worker, r"node\.parent_invalid"):
        await add(worker, world, NodeType.SECTION, other, project="p1")
    mine = await add(worker, world, NodeType.BUILDING)
    section = await add(worker, world, NodeType.SECTION, mine)
    await fails(
        worker,
        r"node\.parent_invalid",
        "UPDATE project_node SET parent_id = :parent WHERE id = :id",
        id=section,
        parent=other,
    )


async def test_an_unknown_or_deleted_parent_is_refused(
    world: World, worker: AsyncConnection
) -> None:
    async with rule(worker, r"node\.parent_invalid"):
        await add(worker, world, NodeType.SECTION, uuid4())
    building = await add(worker, world, NodeType.BUILDING)
    await worker.execute(
        text("UPDATE project_node SET deleted_at = now() WHERE id = :id"), {"id": building}
    )
    async with rule(worker, r"node\.parent_invalid"):
        await add(worker, world, NodeType.SECTION, building)


async def test_a_node_carries_the_legal_entity_of_its_project_and_never_changes_it(
    world: World, worker: AsyncConnection
) -> None:
    async with rule(worker, "fk_project_node_project_id_project"):
        await worker.execute(
            text(
                "INSERT INTO project_node (project_id, legal_entity_id, type, name)"
                " VALUES (:p, :le, 'building', 'чужое юрлицо')"
            ),
            {"p": world.p1, "le": world.le2},
        )
    node = await add(worker, world, NodeType.BUILDING)
    await fails(
        worker,
        "legal_entity_id is immutable",
        "UPDATE project_node SET legal_entity_id = :le WHERE id = :id",
        id=node,
        le=world.le2,
    )
    await fails(
        worker,
        "project_id is immutable",
        "UPDATE project_node SET project_id = :p WHERE id = :id",
        id=node,
        p=world.p2,
    )


# --- удаление ---------------------------------------------------------------------------------


async def test_a_node_with_live_children_cannot_be_deleted_until_they_go(
    world: World, worker: AsyncConnection
) -> None:
    building = await add(worker, world, NodeType.BUILDING)
    section = await add(worker, world, NodeType.SECTION, building)

    await fails(
        worker, r"node\.has_dependents", SOFT_DELETE,
        id=building,
    )  # fmt: skip
    await worker.execute(
        text("UPDATE project_node SET deleted_at = now() WHERE id = :id"), {"id": section}
    )
    await worker.execute(
        text("UPDATE project_node SET deleted_at = now() WHERE id = :id"), {"id": building}
    )


async def test_a_deleted_node_comes_back_only_under_a_live_parent(
    world: World, worker: AsyncConnection
) -> None:
    building = await add(worker, world, NodeType.BUILDING)
    section = await add(worker, world, NodeType.SECTION, building)
    for node in (section, building):
        await worker.execute(
            text("UPDATE project_node SET deleted_at = now() WHERE id = :id"), {"id": node}
        )

    await fails(
        worker, r"node\.parent_invalid", "UPDATE project_node SET deleted_at = NULL WHERE id = :id",
        id=section,
    )  # fmt: skip
    await worker.execute(
        text("UPDATE project_node SET deleted_at = NULL WHERE id = :id"), {"id": building}
    )
    await worker.execute(
        text("UPDATE project_node SET deleted_at = NULL WHERE id = :id"), {"id": section}
    )


async def test_the_dependents_check_is_an_extension_point_other_modules_can_widen(
    world: World, worker: AsyncConnection, owner_engine: AsyncEngine
) -> None:
    node = await add(worker, world, NodeType.BUILDING)
    await worker.commit()
    # Тело функции возвращаем ТЕКУЩЕЕ, а не первоначальное: иначе проверки ГПР, фактов и техники,
    # добавленные позже, пропадают для всех тестов, которые идут после этого файла.
    async with owner_engine.connect() as owner:
        original = await owner.scalar(
            text("SELECT pg_get_functiondef('app.project_node_has_dependents(uuid)'::regprocedure)")
        )
    try:
        async with owner_engine.connect() as owner:
            await owner.execute(text("SET LOCAL ROLE kut_auth"))
            await owner.execute(
                text(
                    "CREATE OR REPLACE FUNCTION app.project_node_has_dependents(p_node uuid)"
                    " RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER"
                    " SET search_path = pg_catalog AS $$ SELECT true $$"
                )
            )
            await owner.commit()
        await fails(
            worker, r"node\.has_dependents", SOFT_DELETE,
            id=node,
        )  # fmt: skip
    finally:
        async with owner_engine.connect() as owner:
            await owner.execute(text("SET LOCAL ROLE kut_auth"))
            await owner.exec_driver_sql(original)
            await owner.commit()
        await worker.execute(
            text("UPDATE project_node SET deleted_at = now() WHERE id = :id"), {"id": node}
        )
        await worker.commit()


async def test_an_object_with_live_nodes_cannot_be_deleted(
    world: World, worker: AsyncConnection
) -> None:
    node = await add(worker, world, NodeType.BUILDING)
    await fails(
        worker, r"project\.has_dependents", "UPDATE project SET deleted_at = now() WHERE id = :id",
        id=world.p1,
    )  # fmt: skip
    await worker.execute(
        text("UPDATE project_node SET deleted_at = now() WHERE id = :id"), {"id": node}
    )
    await worker.execute(
        text("UPDATE project SET deleted_at = now() WHERE id = :id"), {"id": world.p1}
    )


# --- путь -------------------------------------------------------------------------------------


async def test_the_path_runs_from_the_root_to_the_node_and_follows_a_move(
    world: World, worker: AsyncConnection
) -> None:
    first = await add(worker, world, NodeType.STAGE)
    second = await add(worker, world, NodeType.STAGE)
    building = await add(worker, world, NodeType.BUILDING, first)
    section = await add(worker, world, NodeType.SECTION, building)
    floor = await add(worker, world, NodeType.FLOOR, section)
    await worker.execute(
        text("UPDATE project_node SET deleted_at = now() WHERE id = :id"), {"id": floor}
    )  # потомок, мягко удалённый: путь ему тоже меняется (восстановление не должно ломать дерево)
    assert await path_of(worker, floor) == [first, building, section, floor]

    await worker.execute(
        text("UPDATE project_node SET parent_id = :p WHERE id = :id"), {"id": building, "p": second}
    )

    assert await path_of(worker, building) == [second, building]
    assert await path_of(worker, section) == [second, building, section]
    assert await path_of(worker, floor) == [second, building, section, floor]
    await worker.execute(
        text("UPDATE project_node SET parent_id = NULL WHERE id = :id"), {"id": building}
    )
    assert await path_of(worker, floor) == [building, section, floor]


async def test_the_path_cannot_be_written_by_hand(
    auth_world: AuthWorld, owner_engine: AsyncEngine
) -> None:
    """Колонку path у kut_app и kut_worker отзывает права (S3a, test_finance_visibility); триггер -
    вторая линия: он не пускает и того, у кого право на колонку осталось (определитель kut_auth)."""
    east = auth_world.projects["east"]
    async with owner_engine.connect() as connection:
        await connection.execute(text("SET LOCAL ROLE kut_auth"))
        node = await connection.scalar(
            text(
                "SELECT id FROM project_node WHERE project_id = :p AND deleted_at IS NULL LIMIT 1"
            ),
            {"p": east},
        )
        with pytest.raises(IntegrityError, match=r"node\.path_is_managed"):
            await connection.execute(text(SET_PATH), {"id": node})
        await connection.rollback()


async def test_the_seeded_tree_has_consistent_paths(
    auth_world: AuthWorld, worker: AsyncConnection
) -> None:
    east = auth_world.projects["east"]
    broken = await worker.scalar(
        text(
            "WITH RECURSIVE walk AS ("
            " SELECT id, ARRAY[id] AS expected FROM project_node"
            "  WHERE project_id = :p AND parent_id IS NULL"
            " UNION ALL SELECT n.id, walk.expected || n.id FROM project_node n"
            "  JOIN walk ON n.parent_id = walk.id)"
            " SELECT count(*) FROM project_node n JOIN walk USING (id)"
            " WHERE n.path IS DISTINCT FROM walk.expected"
        ),
        {"p": east},
    )
    total = await worker.scalar(
        text("SELECT count(*) FROM project_node WHERE project_id = :p AND deleted_at IS NULL"),
        {"p": east},
    )
    assert broken == 0
    assert total == 1166  # 2 очереди x 2 корпуса x 2 секции x 16 этажей x 8 помещений


# --- прочие ограничения -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("polygon", "valid"),
    [
        ([[0, 0], [10, 0], [10, 5]], True),
        ([[0, 0], [10, 0], [10, 5], [0, 5]], True),
        ([[0.5, -2.25], [1e3, 0], [5, 5]], True),
        (None, True),
        ([[0, 0], [10, 0]], False),
        ([], False),
        ({"points": [[0, 0], [1, 1], [2, 2]]}, False),
        ("[[0,0],[1,1],[2,2]]", False),
        ([[0, 0], [10, 0], [10]], False),
        ([[0, 0], [10, 0], [10, 5, 7]], False),
        ([[0, 0], [10, 0], ["10", 5]], False),
        ([[0, 0], [10, 0], [None, 5]], False),
        ([[0, 0], [10, 0], [2_000_000, 5]], False),
        ([[i, i] for i in range(1001)], False),
    ],
)
async def test_the_polygon_shape_is_validated_by_the_database(
    world: World, worker: AsyncConnection, polygon: Any, valid: bool
) -> None:
    payload = None if polygon is None else json.dumps(polygon)
    if valid:
        await add(worker, world, NodeType.BUILDING, polygon=payload)
    else:
        async with rule(worker, "ck_project_node_polygon_valid"):
            await add(worker, world, NodeType.BUILDING, polygon=payload)


async def test_sibling_codes_are_unique_among_live_nodes_only(
    world: World, worker: AsyncConnection
) -> None:
    first = await add(worker, world, NodeType.BUILDING, code="К1")
    building = await add(worker, world, NodeType.BUILDING, code="К2")
    await add(worker, world, NodeType.SECTION, building, code="А")
    other = await add(worker, world, NodeType.SECTION, first, code="А")  # другой родитель
    assert other

    async with rule(worker, "uq_project_node_root_code"):
        await add(worker, world, NodeType.BUILDING, code="К1")
    async with rule(worker, "uq_project_node_sibling_code"):
        await add(worker, world, NodeType.SECTION, building, code="А")
    await worker.execute(
        text("UPDATE project_node SET deleted_at = now() WHERE id = :id"), {"id": other}
    )
    await add(worker, world, NodeType.SECTION, first, code="А")  # код освободился


async def test_names_and_dates_are_checked(world: World, worker: AsyncConnection) -> None:
    for name in ("", "   "):
        async with rule(worker, "ck_project_node_name_not_blank"):
            await add(worker, world, NodeType.BUILDING, name=name)
    async with rule(worker, "ck_project_node_planned_dates_ordered"):
        await add(
            worker, world, NodeType.BUILDING, planned_start="2026-05-02", planned_end="2026-05-01"
        )
    await add(
        worker, world, NodeType.BUILDING, planned_start="2026-05-01", planned_end="2026-05-01"
    )


async def test_the_customer_of_an_object_must_be_a_live_customer_counterparty(
    auth_world: AuthWorld, world: World, worker: AsyncConnection
) -> None:
    customer = world.customer  # тип customer
    # Заказчик лежит в коммерческой части карточки (project_finance, S3a); строки у объекта ещё нет.
    await worker.execute(
        text(
            "INSERT INTO project_finance (project_id, legal_entity_id, customer_id)"
            " VALUES (:p, :le, :c)"
        ),
        {"p": world.p1, "le": world.le1, "c": customer},
    )
    for wrong in (world.sub_a, world.supplier, uuid4()):
        async with rule(worker, r"project\.customer_invalid|fk_project_finance_customer"):
            await worker.execute(
                text("UPDATE project_finance SET customer_id = :c WHERE project_id = :p"),
                {"c": wrong, "p": world.p1},
            )
    await worker.execute(
        text("UPDATE project_finance SET customer_id = NULL WHERE project_id = :p"),
        {"p": world.p1},
    )


async def test_the_card_of_an_object_is_checked(world: World, worker: AsyncConnection) -> None:
    async with rule(worker, "ck_project_lifecycle_stage_valid"):
        await worker.execute(
            text("UPDATE project SET lifecycle_stage = 'dreaming' WHERE id = :p"), {"p": world.p1}
        )
    # Бюджет, валюта и координаты проверяются в project_finance (коммерческая часть, S3a).
    await worker.execute(
        text("INSERT INTO project_finance (project_id, legal_entity_id) VALUES (:p, :le)"),
        {"p": world.p1, "le": world.le1},
    )
    for column, value, message in (
        ("planned_budget", -1, "ck_project_finance_budget_not_negative"),
        ("budget_currency", "USD", "ck_project_finance_budget_currency_kgs"),
        ("latitude", 91, "ck_project_finance_coordinates_valid"),
    ):
        async with rule(worker, message):
            await worker.execute(
                text(f"UPDATE project_finance SET {column} = :v WHERE project_id = :p"),  # noqa: S608
                {"v": value, "p": world.p1},
            )
    async with rule(worker, "ck_project_planned_dates_ordered"):
        await worker.execute(
            text(
                "UPDATE project SET planned_start = '2027-01-02', planned_end = '2027-01-01'"
                " WHERE id = :p"
            ),
            {"p": world.p1},
        )
    for stage in (
        "land_purchase", "concept", "design", "permits", "site_preparation", "construction",
        "commissioning", "handover", "warranty",
    ):  # fmt: skip
        await worker.execute(
            text("UPDATE project SET lifecycle_stage = :s WHERE id = :p"),
            {"s": stage, "p": world.p1},
        )
