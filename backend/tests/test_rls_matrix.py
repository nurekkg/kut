import dataclasses
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.context import MissingContextError, RequestContext, use_context
from app.core.db import get_engine, get_portal_engine, get_portal_sessionmaker, get_sessionmaker
from app.core.rls import apply_context
from app.modules.objects.models import Project
from tests.support import World, internal

RLS_TABLES = (
    "legal_entity",
    "counterparty",
    "user",
    "project",
    "user_project_access",
    "user_legal_entity_access",
    "project_node",
    "work",
)


async def rows(
    sql: str, context: RequestContext, *, portal: bool = False, **params: object
) -> list[tuple[object, ...]]:
    maker = get_portal_sessionmaker() if portal else get_sessionmaker()
    with use_context(context):
        async with maker() as session:
            return [tuple(row) for row in await session.execute(text(sql), params)]


async def ids(table: str, context: RequestContext, *, portal: bool = False) -> set[UUID]:
    result = await rows(f'SELECT id FROM "{table}"', context, portal=portal)  # noqa: S608
    return {row[0] for row in result}  # type: ignore[misc]


async def access_pairs(
    table: str, column: str, context: RequestContext, *, portal: bool = False
) -> set[tuple[UUID, UUID]]:
    result = await rows(f"SELECT user_id, {column} FROM {table}", context, portal=portal)  # noqa: S608
    return {(row[0], row[1]) for row in result}  # type: ignore[misc]


# --- пустой контекст ---------------------------------------------------------------------------


@pytest.mark.parametrize("table", RLS_TABLES)
async def test_an_empty_context_sees_nothing_through_the_application_login(
    world: World, table: str
) -> None:
    async with get_engine().connect() as connection:
        assert await connection.scalar(text(f'SELECT count(*) FROM "{table}"')) == 0  # noqa: S608


@pytest.mark.parametrize(
    "table", ["counterparty", "user", "project", "user_project_access", "project_node"]
)
async def test_an_empty_context_sees_nothing_through_the_portal_login(
    world: World, table: str
) -> None:
    async with get_portal_engine().connect() as connection:
        assert await connection.scalar(text(f'SELECT count(*) FROM "{table}"')) == 0  # noqa: S608


async def test_the_audit_journal_is_empty_for_an_empty_context(world: World) -> None:
    async with get_engine().connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM audit_log")) == 0


# --- внутренние пользователи -------------------------------------------------------------------


async def test_an_internal_user_sees_only_the_projects_he_was_given(world: World) -> None:
    assert await ids("project", world.foreman()) == {world.p1, world.p_deleted}
    assert await ids("project", world.owner()) == {world.p1, world.p2}
    assert await ids("project", internal(uuid4())) == set()


async def test_the_orm_hides_soft_deleted_projects_unless_asked(world: World) -> None:
    with use_context(world.foreman()):
        async with get_sessionmaker()() as session:
            default = set(await session.scalars(select(Project.id)))
            everything = set(
                await session.scalars(select(Project.id).execution_options(include_deleted=True))
            )
    assert default == {world.p1}
    assert everything == {world.p1, world.p_deleted}


async def test_a_legal_entity_is_visible_through_an_accessible_project_or_explicit_access(
    world: World,
) -> None:
    assert await ids("legal_entity", world.foreman()) == {world.le1}
    assert await ids("legal_entity", world.owner()) == {world.le1, world.le2}
    assert await ids("legal_entity", internal(world.foreman_user, legal_entities=[world.le2])) == {
        world.le2
    }


async def test_internal_users_see_the_group_directories_but_not_other_scopes(
    world: World,
) -> None:
    counterparties = await ids("counterparty", world.foreman())
    assert {world.sub_a, world.sub_b, world.customer, world.supplier} <= counterparties
    users = await ids("user", world.foreman())
    assert {world.owner_user, world.foreman_user, world.sub_a_user} <= users


async def test_project_access_rows_follow_the_projects_of_the_viewer(world: World) -> None:
    foreman = await access_pairs("user_project_access", "project_id", world.foreman())
    assert foreman == {
        (world.owner_user, world.p1),
        (world.foreman_user, world.p1),
        (world.foreman_user, world.p_deleted),
        (world.sub_a_user, world.p1),
        (world.sub_a_user, world.p_deleted),
        (world.sub_b_user, world.p1),
        (world.customer_user, world.p1),
    }
    owner = await access_pairs("user_project_access", "project_id", world.owner())
    assert (world.sub_b_user, world.p2) in owner
    assert (world.owner_user, world.p2) in owner
    assert (world.sub_a_user, world.p_deleted) not in owner


async def test_legal_entity_access_rows_need_matching_legal_entities(world: World) -> None:
    assert (
        await access_pairs("user_legal_entity_access", "legal_entity_id", world.foreman()) == set()
    )
    assert await access_pairs("user_legal_entity_access", "legal_entity_id", world.owner()) == {
        (world.owner_user, world.le1),
        (world.owner_user, world.le2),
    }


# --- внешние пользователи через пул kut_portal --------------------------------------------------


async def test_subcontractor_a_sees_only_his_own_data(world: World) -> None:
    context = world.sub("a")
    # p_deleted есть в его списке объектов, но мягко удалённые строки политика скрывает сама.
    assert await ids("project", context, portal=True) == {world.p1}
    assert await ids("counterparty", context, portal=True) == {world.sub_a}
    assert await ids("user", context, portal=True) == {world.sub_a_user}
    assert await access_pairs("user_project_access", "project_id", context, portal=True) == {
        (world.sub_a_user, world.p1),
        (world.sub_a_user, world.p_deleted),
    }


async def test_subcontractor_b_does_not_see_subcontractor_a(world: World) -> None:
    context = world.sub("b")
    assert await ids("project", context, portal=True) == {world.p1, world.p2}
    assert await ids("counterparty", context, portal=True) == {world.sub_b}
    assert await ids("user", context, portal=True) == {world.sub_b_user}


async def test_the_customer_sees_only_his_object_and_himself(world: World) -> None:
    context = world.customer_ctx()
    assert await ids("project", context, portal=True) == {world.p1}
    assert await ids("counterparty", context, portal=True) == {world.customer}
    assert await ids("user", context, portal=True) == {world.customer_user}


async def test_a_foreign_counterparty_id_in_the_context_cannot_widen_the_view_of_users(
    world: World,
) -> None:
    # Контекст строит сервер, но даже подмена counterparty_id не раскрывает чужих пользователей.
    forged = dataclasses.replace(world.sub("a"), counterparty_id=world.sub_b)
    assert await ids("user", forged, portal=True) == {world.sub_a_user}


async def test_the_portal_pool_has_no_access_to_legal_entities(world: World) -> None:
    with use_context(world.sub("a")):
        async with get_portal_sessionmaker()() as session:
            with pytest.raises(ProgrammingError, match="permission denied"):
                await session.execute(text("SELECT id FROM legal_entity"))


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO project (legal_entity_id, name) SELECT id, 'x' FROM legal_entity",
        "UPDATE project SET name = 'hacked'",
        "UPDATE counterparty SET name = 'hacked'",
        'UPDATE "user" SET is_active = false',
        "INSERT INTO user_project_access (user_id, project_id)"
        " VALUES (gen_random_uuid(), gen_random_uuid())",
    ],
)
async def test_every_write_through_the_portal_pool_is_refused(world: World, statement: str) -> None:
    with use_context(world.sub("a")):
        async with get_portal_sessionmaker()() as session:
            with pytest.raises(ProgrammingError, match="permission denied"):
                await session.execute(text(statement))


# --- запись внутренних пользователей под RLS ---------------------------------------------------


async def test_a_foreman_can_update_his_project_but_not_a_foreign_one(world: World) -> None:
    with use_context(world.foreman()):
        async with get_sessionmaker()() as session:
            own = await session.execute(
                text("UPDATE project SET name = name WHERE id = :id"), {"id": world.p1}
            )
            foreign = await session.execute(
                text("UPDATE project SET name = 'hacked' WHERE id = :id"), {"id": world.p2}
            )
            await session.rollback()
    assert (own.rowcount, foreign.rowcount) == (1, 0)


async def test_a_project_cannot_be_created_in_a_legal_entity_without_access(world: World) -> None:
    with use_context(internal(world.foreman_user, legal_entities=[world.le1])):
        async with get_sessionmaker()() as session:
            with pytest.raises(ProgrammingError, match="row-level security"):
                await session.execute(
                    text("INSERT INTO project (legal_entity_id, name) VALUES (:le, 'x')"),
                    {"le": world.le2},
                )


async def test_access_can_be_granted_only_to_projects_in_the_grantors_scope(world: World) -> None:
    with use_context(world.foreman()):
        async with get_sessionmaker()() as session:
            await session.execute(
                text("INSERT INTO user_project_access (user_id, project_id) VALUES (:u, :p)"),
                {"u": world.customer_user, "p": world.p_deleted},
            )
            with pytest.raises(ProgrammingError, match="row-level security"):
                await session.execute(
                    text("INSERT INTO user_project_access (user_id, project_id) VALUES (:u, :p)"),
                    {"u": world.customer_user, "p": world.p2},
                )
            await session.rollback()


async def test_insert_returning_needs_the_new_row_to_pass_the_select_policy(
    world: World,
) -> None:
    new_id = uuid4()
    context = world.owner()
    insert = "INSERT INTO project (id, legal_entity_id, name) VALUES (:id, :le, 'new')"
    params = {"id": new_id, "le": world.le1}
    with use_context(context):
        async with get_sessionmaker()() as session:
            await session.execute(text(insert), params)  # без RETURNING проходит
            await session.rollback()

        async with get_sessionmaker()() as session:
            with pytest.raises(ProgrammingError, match="row-level security"):
                await session.execute(text(insert + " RETURNING id"), params)

    # Сервис расширяет контекст id нового объекта и переставляет GUC в той же транзакции.
    extended = dataclasses.replace(context, project_ids=context.project_ids | {new_id})
    with use_context(context):
        async with get_sessionmaker()() as session:
            with use_context(extended):
                await apply_context(session)
                created = await session.scalar(text(insert + " RETURNING id"), params)
            await session.rollback()
    assert created == new_id


# --- утечка через пул ---------------------------------------------------------------------------


@pytest.fixture
async def single_connection_pool(test_database: None) -> AsyncIterator[AsyncEngine]:
    url = get_settings().database_url.get_secret_value()
    engine = create_async_engine(url, pool_size=1, max_overflow=0)
    yield engine
    await engine.dispose()


async def test_context_does_not_leak_between_users_on_the_same_connection(
    world: World, single_connection_pool: AsyncEngine
) -> None:
    maker = async_sessionmaker(single_connection_pool, expire_on_commit=False)
    query = text("SELECT pg_backend_pid(), (SELECT array_agg(id) FROM project)")

    async def run(context: RequestContext) -> tuple[int, set[UUID]]:
        with use_context(context):
            async with maker() as session:
                pid, projects = (await session.execute(query)).one()
        return pid, set(projects or [])

    foreman_pid, foreman_projects = await run(world.foreman())
    owner_pid, owner_projects = await run(world.owner())
    stranger_pid, stranger_projects = await run(internal(uuid4()))

    assert foreman_pid == owner_pid == stranger_pid, "pool_size=1 must reuse one connection"
    assert foreman_projects == {world.p1, world.p_deleted}
    assert owner_projects == {world.p1, world.p2}
    assert stranger_projects == set()

    # Соединение без хука после чужих транзакций: контекст пуст, строк нет.
    async with single_connection_pool.connect() as connection:
        guc = await connection.scalar(text("SELECT current_setting('app.user_id', true)"))
        count = await connection.scalar(text("SELECT count(*) FROM project"))
    assert (guc, count) == ("", 0)


async def test_a_failed_begin_does_not_leave_a_previous_context_behind(
    world: World, single_connection_pool: AsyncEngine
) -> None:
    maker = async_sessionmaker(single_connection_pool, expire_on_commit=False)
    with use_context(world.owner()):
        async with maker() as session:
            await session.execute(text("SELECT 1"))

    async with maker() as session:
        with pytest.raises(MissingContextError):
            await session.execute(text("SELECT count(*) FROM project"))

    async with single_connection_pool.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM project")) == 0


# --- шаблон политики для таблиц следующих спринтов -------------------------------------------


TEMPLATE_POLICY = """
CREATE POLICY scope ON tpl_subcontract FOR SELECT TO kut_app, kut_portal USING (
    project_id = ANY ((SELECT app.project_ids())::uuid[])
    AND ((SELECT app.is_internal()) OR counterparty_id = (SELECT app.counterparty_id())))
"""


@pytest.fixture
async def template_table(
    world: World, owner_engine: AsyncEngine, worker_engine: AsyncEngine
) -> AsyncIterator[None]:
    async with owner_engine.begin() as connection:
        for statement in (
            "CREATE TABLE tpl_subcontract (id uuid PRIMARY KEY DEFAULT gen_random_uuid(),"
            " project_id uuid NOT NULL, counterparty_id uuid NOT NULL, amount int NOT NULL)",
            "ALTER TABLE tpl_subcontract ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE tpl_subcontract FORCE ROW LEVEL SECURITY",
            TEMPLATE_POLICY,
            "GRANT SELECT ON tpl_subcontract TO kut_app, kut_portal, kut_worker",
            "GRANT INSERT ON tpl_subcontract TO kut_worker",
        ):
            await connection.execute(text(statement))
    async with worker_engine.begin() as connection:
        for project, counterparty, amount in (
            (world.p1, world.sub_a, 10),
            (world.p1, world.sub_b, 20),
            (world.p2, world.sub_b, 30),
        ):
            await connection.execute(
                text(
                    "INSERT INTO tpl_subcontract (project_id, counterparty_id, amount)"
                    " VALUES (:p, :c, :a)"
                ),
                {"p": project, "c": counterparty, "a": amount},
            )
    yield
    async with owner_engine.begin() as connection:
        await connection.execute(text("DROP TABLE tpl_subcontract"))


async def amounts(context: RequestContext, *, portal: bool) -> set[object]:
    result = await rows("SELECT amount FROM tpl_subcontract", context, portal=portal)
    return {row[0] for row in result}


async def test_project_and_counterparty_policy_template(world: World, template_table: None) -> None:
    """Эталон политики по ADR-001 §1 для будущих таблиц с project_id и counterparty_id."""
    assert await amounts(world.sub("a"), portal=True) == {10}
    assert await amounts(world.sub("b"), portal=True) == {20, 30}
    assert await amounts(world.foreman(), portal=False) == {10, 20}
    assert await amounts(world.owner(), portal=False) == {10, 20, 30}
    assert await amounts(internal(uuid4()), portal=False) == set()
    # Подрядчик A на объекте p2: объект не в его списке, значит и чужой договор B не виден.
    assert (
        await amounts(
            dataclasses.replace(world.sub("a"), project_ids=frozenset({world.p2})), portal=True
        )
        == set()
    )
