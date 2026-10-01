from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from tests.support import World


@pytest.fixture
async def worker(worker_engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """Соединение kut_worker с откатом: ограничения проверяем, мир не загрязняем."""
    async with worker_engine.connect() as connection:
        yield connection
        await connection.rollback()


async def add_user(connection: AsyncConnection, world: World, **values: object) -> None:
    params: dict[str, object] = {
        "id": uuid4(),
        "phone": None,
        "email": None,
        "role": world.roles["foreman"],
        **values,
    }
    await connection.execute(
        text(
            'INSERT INTO "user" (id, phone, email, full_name, role_id)'
            " VALUES (:id, :phone, :email, 'Тест', :role)"
        ),
        params,
    )


# --- user --------------------------------------------------------------------------------------


async def test_a_user_needs_a_phone_or_an_email(world: World, worker: AsyncConnection) -> None:
    with pytest.raises(IntegrityError, match="ck_user_contact_required"):
        await add_user(worker, world)


async def test_a_user_may_have_only_a_phone_or_only_an_email(
    world: World, worker: AsyncConnection
) -> None:
    await add_user(worker, world, phone="+996555123456")
    await add_user(worker, world, email="only@example.kg")


@pytest.mark.parametrize("phone", ["996555123456", "+0555123456", "+996 555 123456", "+99655"])
async def test_a_phone_must_be_in_e164_form(
    world: World, worker: AsyncConnection, phone: str
) -> None:
    with pytest.raises(IntegrityError, match="ck_user_phone_e164"):
        await add_user(worker, world, phone=phone)


async def test_an_email_must_be_lowercase(world: World, worker: AsyncConnection) -> None:
    with pytest.raises(IntegrityError, match="ck_user_email_lowercase"):
        await add_user(worker, world, email="Mixed@Example.kg")


async def test_phone_and_email_are_unique_among_active_users(
    world: World, worker: AsyncConnection
) -> None:
    await add_user(worker, world, phone="+996555777001", email="dup@example.kg")
    with pytest.raises(IntegrityError, match="uq_user_email"):
        await add_user(worker, world, email="dup@example.kg")
    await worker.rollback()
    await add_user(worker, world, phone="+996555777001")  # после отката строки нет
    with pytest.raises(IntegrityError, match="uq_user_phone"):
        await add_user(worker, world, phone="+996555777001")


async def test_a_soft_deleted_user_releases_the_phone_and_the_email(
    world: World, worker: AsyncConnection
) -> None:
    await worker.execute(
        text(
            'INSERT INTO "user" (phone, email, full_name, role_id, deleted_at)'
            " VALUES ('+996555777002', 'gone@example.kg', 'Был', :role, now())"
        ),
        {"role": world.roles["foreman"]},
    )
    await add_user(worker, world, phone="+996555777002", email="gone@example.kg")


async def test_a_user_must_reference_an_existing_role(
    world: World, worker: AsyncConnection
) -> None:
    with pytest.raises(IntegrityError, match="fk_user_role_id_role"):
        await worker.execute(
            text(
                "INSERT INTO \"user\" (phone, full_name, role_id) VALUES ('+996555777003', 'x', :r)"
            ),
            {"r": uuid4()},
        )


# --- справочники -------------------------------------------------------------------------------


async def test_a_counterparty_can_have_several_types_but_only_known_ones(
    worker: AsyncConnection,
) -> None:
    await worker.execute(
        text("INSERT INTO counterparty (name, types) VALUES ('Много типов', :types)"),
        {"types": ["supplier", "subcontractor", "investor"]},
    )
    for bad in (["alien"], [], ["supplier", "alien"]):
        savepoint = await worker.begin_nested()
        with pytest.raises(IntegrityError, match="ck_counterparty_types_valid"):
            await worker.execute(
                text("INSERT INTO counterparty (name, types) VALUES ('Плохой', :types)"),
                {"types": bad},
            )
        await savepoint.rollback()


async def test_a_project_needs_a_legal_entity_and_a_known_status(
    world: World, worker: AsyncConnection
) -> None:
    with pytest.raises(IntegrityError, match=r"not-null|null value"):
        await worker.execute(text("INSERT INTO project (name) VALUES ('Без юрлица')"))
    await worker.rollback()
    with pytest.raises(IntegrityError, match="ck_project_status_valid"):
        await worker.execute(
            text(
                "INSERT INTO project (legal_entity_id, name, status) VALUES (:le, 'x', 'dreaming')"
            ),
            {"le": world.le1},
        )


async def test_the_actor_columns_reference_real_users(
    world: World, worker: AsyncConnection
) -> None:
    with pytest.raises(IntegrityError, match="fk_counterparty_created_by_user"):
        await worker.execute(
            text(
                "INSERT INTO counterparty (name, types, created_by)"
                " VALUES ('x', ARRAY['supplier'], :ghost)"
            ),
            {"ghost": uuid4()},
        )


# --- доступы -----------------------------------------------------------------------------------


async def test_project_access_is_unique_until_it_is_revoked(
    world: World, worker: AsyncConnection
) -> None:
    insert = text("INSERT INTO user_project_access (user_id, project_id) VALUES (:u, :p)")
    params = {"u": world.customer_user, "p": world.p2}
    await worker.execute(insert, params)
    savepoint = await worker.begin_nested()
    with pytest.raises(IntegrityError, match="uq_user_project_access_user_id_project_id"):
        await worker.execute(insert, params)
    await savepoint.rollback()
    await worker.execute(
        text(
            "UPDATE user_project_access SET deleted_at = now()"
            " WHERE user_id = :u AND project_id = :p"
        ),
        params,
    )
    await worker.execute(insert, params)  # отозванный доступ можно выдать заново


async def test_the_permission_without_qualifier_is_unique_too(
    owner_engine: AsyncEngine,
) -> None:
    async with owner_engine.connect() as connection:
        with pytest.raises(IntegrityError, match="uq_permission_module_action_qualifier"):
            await connection.execute(
                text(
                    "INSERT INTO permission (module, action, qualifier)"
                    " VALUES ('objects', 'read', NULL)"
                )
            )


# --- составной FK по ADR-001 §5.2 --------------------------------------------------------------


async def test_a_child_cannot_carry_a_different_legal_entity_than_its_project(
    world: World, owner_engine: AsyncEngine, worker_engine: AsyncEngine
) -> None:
    async with owner_engine.begin() as connection:
        for statement in (
            "CREATE TABLE tpl_child (id uuid PRIMARY KEY DEFAULT gen_random_uuid(),"
            " project_id uuid NOT NULL, legal_entity_id uuid NOT NULL,"
            " FOREIGN KEY (project_id, legal_entity_id) REFERENCES project (id, legal_entity_id))",
            "GRANT INSERT ON tpl_child TO kut_worker",
        ):
            await connection.execute(text(statement))
    try:
        async with worker_engine.connect() as connection:
            await connection.execute(
                text("INSERT INTO tpl_child (project_id, legal_entity_id) VALUES (:p, :le)"),
                {"p": world.p1, "le": world.le1},
            )
            savepoint = await connection.begin_nested()
            with pytest.raises(IntegrityError, match="tpl_child_project_id_legal_entity_id_fkey"):
                await connection.execute(
                    text("INSERT INTO tpl_child (project_id, legal_entity_id) VALUES (:p, :le)"),
                    {"p": world.p1, "le": world.le2},
                )
            await savepoint.rollback()
            await connection.rollback()
    finally:
        async with owner_engine.begin() as connection:
            await connection.execute(text("DROP TABLE tpl_child"))
