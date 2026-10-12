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
    "project_finance",
    "schedule_item",
    "schedule_dependency",
    "work_actual",
    "work_photo",
    "equipment_type",
    "contract",
    "equipment_unit",
    "equipment_own_rate",
    "equipment_rate",
    "equipment_operator",
    "equipment_request",
    "equipment_assignment",
    "equipment_maintenance",
    "equipment_document",
    "equipment_import_batch",
    "equipment_shift",
    "equipment_shift_line",
    "equipment_fuel",
    "equipment_delegate",
    "cost_entry",
)
# Колонки, которые обязаны быть неизменяемыми (id есть у всех, у project ещё и юрлицо, у узла
# объект и юрлицо, у таблиц S3a - объект, юрлицо и то, что определяет смысл строки).
FROZEN = {table: {"id"} for table in AUDITED_TABLES} | {
    "project": {"id", "legal_entity_id"},
    "project_node": {"id", "project_id", "legal_entity_id"},
    "project_finance": {"id", "project_id", "legal_entity_id"},
    "schedule_item": {"id", "project_id", "legal_entity_id", "node_id", "work_id"},
    "schedule_dependency": {
        "id",
        "project_id",
        "legal_entity_id",
        "predecessor_id",
        "successor_id",
    },
    "work_actual": {"id", "project_id", "legal_entity_id", "author_id"},
    "work_photo": {
        "id",
        "project_id",
        "legal_entity_id",
        "work_actual_id",
        "s3_key",
        "content_type",
        "size_bytes",
        "latitude",
        "longitude",
        "taken_at",
        "uploaded_by",
    },
    "contract": {"id", "legal_entity_id", "kind"},
    "equipment_unit": {"id", "legal_entity_id"},
    "equipment_own_rate": {
        "id",
        "legal_entity_id",
        "equipment_type_id",
        "unit",
        "amount",
        "valid_from",
        "proposed_by",
    },
    "equipment_rate": {
        "id",
        "contract_id",
        "unit",
        "with_operator",
        "amount",
        "min_quantity",
        "valid_from",
        "equipment_unit_id",
        "equipment_type_id",
    },
    "equipment_operator": {"id", "legal_entity_id", "employment"},
    "equipment_request": {"id", "project_id", "legal_entity_id"},
    "equipment_assignment": {
        "id",
        "project_id",
        "legal_entity_id",
        "equipment_unit_id",
        "request_id",
    },
    "equipment_maintenance": {"id", "legal_entity_id", "equipment_unit_id", "kind"},
    "equipment_document": {
        "id",
        "legal_entity_id",
        "equipment_unit_id",
        "kind",
        "number",
        "valid_until",
        "version",
    },
    "equipment_shift": {"id", "legal_entity_id", "project_id", "equipment_unit_id", "created_by"},
    "equipment_shift_line": {"id", "shift_id", "project_id"},
    "equipment_fuel": {
        "id",
        "legal_entity_id",
        "project_id",
        "equipment_unit_id",
        "fuel_date",
        "liters",
        "amount",
        "source",
        "created_by",
        "shift_id",
    },
    "equipment_delegate": {
        "id",
        "project_id",
        "delegate_user_id",
        "date_from",
        "date_to",
        "reason",
        "created_by",
    },
    "cost_entry": {
        "id",
        "legal_entity_id",
        "project_id",
        "node_id",
        "schedule_item_id",
        "equipment_unit_id",
        "period",
        "category",
        "source_type",
        "source_id",
        "posting_version",
        "line_no",
        "quantity",
        "tariff_unit",
        "rate",
        "amount",
        "reversal_of_id",
    },
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
