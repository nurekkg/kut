from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.core.context import use_context
from app.core.db import get_sessionmaker
from tests.support import World

AUDITED_TABLES = (
    "legal_entity",
    "counterparty",
    "role",
    "permission",
    "role_permission",
    "user",
    "project",
    "user_project_access",
    "user_legal_entity_access",
    "project_node",
    "work",
)
# Колонки, которые обязаны быть неизменяемыми (id есть у всех, у project ещё и юрлицо, у узла
# объект и юрлицо).
FROZEN = {table: {"id"} for table in AUDITED_TABLES} | {
    "project": {"id", "legal_entity_id"},
    "project_node": {"id", "project_id", "legal_entity_id"},
}


@pytest.fixture
async def worker(worker_engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    async with worker_engine.connect() as connection:
        yield connection
        await connection.rollback()


async def test_every_audited_table_freezes_its_primary_key(owner_engine: AsyncEngine) -> None:
    async with owner_engine.connect() as connection:
        rows = {
            row.relname: row.definition
            for row in await connection.execute(
                text(
                    "SELECT c.relname, pg_get_triggerdef(t.oid) AS definition FROM pg_trigger t"
                    " JOIN pg_class c ON c.oid = t.tgrelid"
                    " WHERE t.tgname = 'immutable_columns' AND NOT t.tgisinternal"
                )
            )
        }
    assert set(rows) == set(AUDITED_TABLES)
    for table, columns in FROZEN.items():
        definition = rows[table]
        assert "BEFORE UPDATE OF" in definition and "FOR EACH ROW" in definition, table
        for column in columns:
            assert f"'{column}'" in definition, (table, column)


@pytest.mark.parametrize(
    "table", ["counterparty", "legal_entity", "project", "user_project_access", "user"]
)
async def test_a_primary_key_cannot_be_changed(
    world: World, worker: AsyncConnection, table: str
) -> None:
    quoted = f'"{table}"'
    with pytest.raises(IntegrityError, match="is immutable"):
        await worker.execute(
            text(
                f"UPDATE {quoted} SET id = gen_random_uuid()"  # noqa: S608
                f" WHERE id = (SELECT id FROM {quoted} LIMIT 1)"
            )
        )


async def test_naming_the_key_without_changing_it_is_harmless(
    world: World, worker: AsyncConnection
) -> None:
    result = await worker.execute(
        text("UPDATE counterparty SET id = id, name = name WHERE id = :id"), {"id": world.sub_a}
    )
    assert result.rowcount == 1


async def test_an_ordinary_update_still_works_and_is_journaled(
    world: World, worker: AsyncConnection
) -> None:
    await worker.execute(
        text("UPDATE counterparty SET name = 'Переименован' WHERE id = :id"), {"id": world.supplier}
    )
    actions = list(
        await worker.scalars(
            text(
                "SELECT action FROM audit_log WHERE table_name = 'counterparty'"
                " AND row_id = :id ORDER BY id"
            ),
            {"id": world.supplier},
        )
    )
    assert actions[-1] == "update"


async def test_a_project_cannot_be_moved_to_another_legal_entity(
    world: World, worker: AsyncConnection
) -> None:
    with pytest.raises(IntegrityError, match=r"project\.legal_entity_id is immutable"):
        await worker.execute(
            text("UPDATE project SET legal_entity_id = :le WHERE id = :id"),
            {"le": world.le2, "id": world.p1},
        )


async def test_the_application_cannot_move_a_project_or_rekey_an_access_row(world: World) -> None:
    with use_context(world.foreman()):
        async with get_sessionmaker()() as session:
            with pytest.raises(IntegrityError, match=r"project\.legal_entity_id is immutable"):
                await session.execute(
                    text("UPDATE project SET legal_entity_id = :le WHERE id = :id"),
                    {"le": world.le2, "id": world.p1},
                )
            await session.rollback()
            with pytest.raises(IntegrityError, match=r"user_project_access\.id is immutable"):
                await session.execute(
                    text(
                        "UPDATE user_project_access SET id = gen_random_uuid()"
                        " WHERE user_id = :u AND project_id = :p"
                    ),
                    {"u": world.sub_a_user, "p": world.p1},
                )
