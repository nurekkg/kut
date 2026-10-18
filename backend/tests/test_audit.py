from datetime import UTC, datetime
from time import perf_counter
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.context import system_context, use_context
from app.core.db import get_engine, get_portal_engine, get_sessionmaker
from app.modules.org.models import Counterparty
from tests.support import World, external, internal

BULK_ROWS = 10_000


async def journal(engine: AsyncEngine, table: str, row_id: UUID) -> list[object]:
    async with engine.connect() as connection:
        result = await connection.execute(
            text(
                "SELECT action, actor_id, actor_kind, request_id, old, new, meta, project_id,"
                " legal_entity_id FROM audit_log WHERE table_name = :table AND row_id = :id"
                " ORDER BY id"
            ),
            {"table": table, "id": row_id},
        )
        return list(result)


# --- покрытие триггерами -----------------------------------------------------------------------


async def test_every_table_with_created_at_has_three_statement_level_audit_triggers(
    owner_engine: AsyncEngine,
) -> None:
    async with owner_engine.connect() as connection:
        audited = {
            row.relname
            for row in await connection.execute(
                text(
                    "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                    " JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'created_at'"
                    " WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT a.attisdropped"
                )
            )
        }
        triggers = list(
            await connection.execute(
                text(
                    "SELECT c.relname, t.tgname, t.tgtype, t.tgfoid::regproc::text AS fn,"
                    " t.tgnewtable, t.tgoldtable FROM pg_trigger t"
                    " JOIN pg_class c ON c.oid = t.tgrelid WHERE NOT t.tgisinternal"
                    " AND t.tgname LIKE 'audit\\_%'"
                )
            )
        )
    assert len(audited) >= 9
    expected = {
        "audit_insert": (4, "app.audit_insert", "new"),
        "audit_update": (16, "app.audit_update", "both"),
        "audit_delete": (8, "app.audit_delete", "old"),
    }
    by_table: dict[str, dict[str, tuple[int, str, str | None, str | None]]] = {}
    for row in triggers:
        by_table.setdefault(row.relname, {})[row.tgname] = (
            row.tgtype,
            row.fn,
            row.tgnewtable,
            row.tgoldtable,
        )
    for table in audited:
        found = by_table.get(table, {})
        assert set(found) == set(expected), f"{table}: audit triggers {sorted(found)}"
        for name, (event_bit, function, tables) in expected.items():
            tgtype, fn, new_table, old_table = found[name]
            assert tgtype & 1 == 0, f"{table}.{name} is row-level; statement-level is required"
            assert tgtype & 2 == 0, f"{table}.{name} is BEFORE; AFTER is required"
            assert tgtype & event_bit, f"{table}.{name} fires on the wrong event"
            assert fn == function
            assert (new_table is not None) == (tables in ("new", "both")), (table, name)
            assert (old_table is not None) == (tables in ("old", "both")), (table, name)


# --- содержимое журнала ------------------------------------------------------------------------


async def test_insert_update_and_soft_delete_are_journaled_with_actor_and_request(
    world: World, worker_engine: AsyncEngine
) -> None:
    request_id = uuid4()
    with use_context(world.owner(request_id=request_id)):
        async with get_sessionmaker()() as session:
            row = Counterparty(name="Аудит", types=["supplier"])
            session.add(row)
            await session.commit()
            row.name = "Аудит 2"
            await session.commit()
            row.deleted_at = datetime.now(UTC)
            await session.commit()

    insert, update, soft_delete = await journal(worker_engine, "counterparty", row.id)
    assert [insert.action, update.action, soft_delete.action] == ["insert", "update", "soft_delete"]
    for entry in (insert, update, soft_delete):
        assert entry.actor_id == world.owner_user
        assert entry.actor_kind == "user"
        assert entry.request_id == request_id
        assert entry.meta == {"db_user": "kut_app"}
    assert insert.old is None
    assert insert.new["name"] == "Аудит"
    assert insert.new["created_by"] == str(world.owner_user)
    assert (update.old, update.new) == ({"name": "Аудит"}, {"name": "Аудит 2"})
    assert soft_delete.old == {"deleted_at": None}
    assert set(soft_delete.new) == {"deleted_at"}


async def test_an_update_that_changes_nothing_writes_no_journal_row(
    world: World, worker_engine: AsyncEngine
) -> None:
    with use_context(world.owner()):
        async with get_sessionmaker()() as session:
            row = Counterparty(name="Без изменений", types=["supplier"])
            session.add(row)
            await session.commit()
            await session.execute(
                text("UPDATE counterparty SET name = name, updated_at = now() WHERE id = :id"),
                {"id": row.id},
            )
            await session.commit()
    assert [entry.action for entry in await journal(worker_engine, "counterparty", row.id)] == [
        "insert"
    ]


async def test_system_changes_are_attributed_to_the_system(
    world: World, worker_engine: AsyncEngine
) -> None:
    row_id = uuid4()
    with system_context():
        async with async_sessionmaker(worker_engine)() as session:
            await session.execute(
                text(
                    "INSERT INTO counterparty (id, name, types)"
                    " VALUES (:id, 'sys', ARRAY['supplier'])"
                ),
                {"id": row_id},
            )
            await session.commit()
    (entry,) = await journal(worker_engine, "counterparty", row_id)
    assert (entry.actor_id, entry.actor_kind, entry.meta) == (
        None,
        "system",
        {"db_user": "kut_worker"},
    )


async def test_external_actors_are_marked_as_external(
    world: World, worker_engine: AsyncEngine
) -> None:
    row_id = uuid4()
    context = external(world.sub_a_user, world.sub_a, projects=[world.p1])
    with use_context(context):
        async with async_sessionmaker(worker_engine)() as session:
            await session.execute(
                text(
                    "INSERT INTO counterparty (id, name, types)"
                    " VALUES (:id, 'ext', ARRAY['supplier'])"
                ),
                {"id": row_id},
            )
            await session.commit()
    (entry,) = await journal(worker_engine, "counterparty", row_id)
    assert (entry.actor_id, entry.actor_kind) == (world.sub_a_user, "external")


async def test_scope_columns_of_the_audited_row_are_copied_into_the_journal(
    world: World, worker_engine: AsyncEngine
) -> None:
    (project_entry, *_) = await journal(worker_engine, "project", world.p1)
    assert (project_entry.project_id, project_entry.legal_entity_id) == (world.p1, world.le1)
    (entity_entry, *_) = await journal(worker_engine, "legal_entity", world.le1)
    assert (entity_entry.project_id, entity_entry.legal_entity_id) == (None, world.le1)


async def test_secrets_never_reach_the_journal(world: World, worker_engine: AsyncEngine) -> None:
    user_id = uuid4()
    async with worker_engine.begin() as connection:
        await connection.execute(
            text(
                'INSERT INTO "user" (id, phone, full_name, role_id, password_hash)'
                " VALUES (:id, :phone, 'Секрет', :role, 'SECRET-HASH-1')"
            ),
            {"id": user_id, "phone": "+996555000999", "role": world.roles["foreman"]},
        )
        await connection.execute(
            text(
                "UPDATE \"user\" SET password_hash = 'SECRET-HASH-2', full_name = 'Секрет 2'"
                " WHERE id = :id"
            ),
            {"id": user_id},
        )
        await connection.execute(
            text("UPDATE \"user\" SET password_hash = 'SECRET-HASH-3' WHERE id = :id"),
            {"id": user_id},
        )
    entries = await journal(worker_engine, "user", user_id)
    assert [entry.action for entry in entries] == [
        "insert",
        "update",
    ]  # смена только хэша не пишется
    assert "password_hash" not in entries[0].new
    assert (entries[1].old, entries[1].new) == ({"full_name": "Секрет"}, {"full_name": "Секрет 2"})
    assert "SECRET-HASH" not in str([(e.old, e.new) for e in entries])


# --- журнал только на добавление ---------------------------------------------------------------


@pytest.mark.parametrize(
    "statement", ["UPDATE audit_log SET action = 'x'", "DELETE FROM audit_log"]
)
async def test_the_application_cannot_change_or_delete_the_journal(
    world: World, statement: str
) -> None:
    with use_context(world.owner()):
        async with get_sessionmaker()() as session:
            with pytest.raises(ProgrammingError, match="permission denied"):
                await session.execute(text(statement))


async def test_the_application_cannot_truncate_the_journal(world: World) -> None:
    async with get_engine().connect() as connection:
        with pytest.raises(ProgrammingError, match="permission denied"):
            await connection.execute(text("TRUNCATE audit_log"))


@pytest.mark.parametrize(
    "statement",
    ["UPDATE audit_log SET action = 'x'", "DELETE FROM audit_log", "TRUNCATE audit_log"],
)
async def test_neither_the_worker_nor_the_owner_can_change_the_journal(
    world: World, worker_engine: AsyncEngine, owner_engine: AsyncEngine, statement: str
) -> None:
    for engine in (worker_engine, owner_engine):
        async with engine.connect() as connection:
            with pytest.raises(ProgrammingError, match="permission denied"):
                await connection.execute(text(statement))


async def test_the_immutability_triggers_hold_even_if_the_owner_regrants_everything(
    world: World, owner_engine: AsyncEngine
) -> None:
    """Второй рубеж: права вернули, политику добавили, а журнал всё равно не меняется."""
    async with owner_engine.connect() as connection:
        await connection.execute(text("GRANT UPDATE, DELETE, TRUNCATE ON audit_log TO kut_owner"))
        await connection.execute(
            text("CREATE POLICY tmp_all ON audit_log FOR ALL TO kut_owner USING (true)")
        )
        for statement in (
            "UPDATE audit_log SET action = action",
            "DELETE FROM audit_log",
            "TRUNCATE audit_log",
        ):
            with pytest.raises(DBAPIError, match="append-only"):
                await connection.execute(text(statement))
            await connection.rollback()
            await connection.execute(
                text("GRANT UPDATE, DELETE, TRUNCATE ON audit_log TO kut_owner")
            )
            await connection.execute(
                text("CREATE POLICY tmp_all ON audit_log FOR ALL TO kut_owner USING (true)")
            )
        await connection.rollback()


async def test_every_partition_carries_the_immutability_trigger(owner_engine: AsyncEngine) -> None:
    async with owner_engine.connect() as connection:
        missing = [
            row.relname
            for row in await connection.execute(
                text(
                    "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid"
                    " WHERE i.inhparent = 'public.audit_log'::regclass AND NOT EXISTS ("
                    "   SELECT 1 FROM pg_trigger t WHERE t.tgrelid = c.oid"
                    "   AND t.tgname = 'audit_log_no_change' AND t.tgenabled = 'O')"
                )
            )
        ]
    assert missing == []


# --- видимость журнала -------------------------------------------------------------------------


async def test_the_journal_is_scoped_like_the_data_it_describes(world: World) -> None:
    sql = "SELECT DISTINCT table_name, project_id, legal_entity_id FROM audit_log"
    with use_context(world.foreman()):
        async with get_sessionmaker()() as session:
            foreman = {tuple(row) for row in await session.execute(text(sql))}
    with use_context(world.owner()):
        async with get_sessionmaker()() as session:
            owner = {tuple(row) for row in await session.execute(text(sql))}

    assert foreman, "the foreman sees the journal of his projects"
    assert {project for _t, project, _le in foreman} <= {world.p1, world.p_deleted}
    assert ("project", world.p2, world.le2) not in foreman
    assert ("legal_entity", None, world.le1) not in foreman  # юрлицо без явного доступа
    assert ("project", world.p2, world.le2) in owner
    assert ("legal_entity", None, world.le2) in owner


async def test_access_to_a_legal_entity_does_not_reveal_the_journal_of_its_projects(
    world: World,
) -> None:
    """Юрлицо открывает записи без объекта; журнал объекта виден только тем, у кого есть объект."""
    sql = "SELECT DISTINCT table_name, project_id, legal_entity_id FROM audit_log"
    accountant = internal(world.foreman_user, projects=[world.p2], legal_entities=[world.le1])
    with use_context(accountant):
        async with get_sessionmaker()() as session:
            seen = {tuple(row) for row in await session.execute(text(sql))}

    assert ("project", world.p2, world.le2) in seen  # свой объект
    assert ("legal_entity", None, world.le1) in seen  # запись без объекта по своему юрлицу
    assert ("project", world.p1, world.le1) not in seen  # объект того же юрлица, но не его
    assert {project for _t, project, _le in seen if project} == {world.p2}


async def test_external_users_have_no_access_to_the_journal(world: World) -> None:
    with use_context(world.sub("a")):
        async with get_portal_engine().connect() as connection:
            with pytest.raises(ProgrammingError, match="permission denied"):
                await connection.execute(text("SELECT count(*) FROM audit_log"))


# --- массовые операции и партиции --------------------------------------------------------------


async def test_a_bulk_update_is_one_statement_trigger_call_not_one_per_row(
    world: World, worker_engine: AsyncEngine
) -> None:
    request_id = uuid4()
    prefix = f"bulk-{request_id}"
    with system_context(request_id):
        async with async_sessionmaker(worker_engine)() as session:
            await session.execute(
                text(
                    "INSERT INTO counterparty (name, types)"
                    " SELECT :prefix || g, ARRAY['supplier'] FROM generate_series(1, :n) g"
                ),
                {"prefix": prefix, "n": BULK_ROWS},
            )
            started = perf_counter()
            await session.execute(
                text("UPDATE counterparty SET name = name || '_u' WHERE name LIKE :like"),
                {"like": f"{prefix}%"},
            )
            elapsed = perf_counter() - started
            journaled = await session.scalar(
                text(
                    "SELECT count(*) FROM audit_log WHERE request_id = :rid"
                    " AND table_name = 'counterparty' AND action = 'update'"
                ),
                {"rid": request_id},
            )
            plan = await session.scalar(
                text(
                    "EXPLAIN (ANALYZE, FORMAT JSON)"
                    " UPDATE counterparty SET name = name || '_v' WHERE name LIKE :like"
                ),
                {"like": f"{prefix}%"},
            )
            await session.rollback()

    trigger_calls = {t["Trigger Name"]: t["Calls"] for t in plan[0]["Triggers"]}
    trigger_ms = {t["Trigger Name"]: round(t["Time"]) for t in plan[0]["Triggers"]}
    print(
        f"\nbulk UPDATE of {BULK_ROWS} rows: {elapsed:.2f}s wall; server side: statement"
        f" {round(plan[0]['Execution Time'])} ms incl. audit trigger {trigger_ms} ms;"
        f" trigger calls: {trigger_calls}"
    )
    assert journaled == BULK_ROWS
    assert trigger_calls == {"audit_update": 1}, "one statement-level call, not one per row"


@pytest.mark.perf
async def test_a_bulk_update_takes_under_thirty_seconds(
    world: World, worker_engine: AsyncEngine
) -> None:
    """Порог времени зависит от дисковой нагрузки машины: проверяется только в CI (маркер perf)."""
    request_id = uuid4()
    prefix = f"perf-{request_id}"
    with system_context(request_id):
        async with async_sessionmaker(worker_engine)() as session:
            await session.execute(
                text(
                    "INSERT INTO counterparty (name, types)"
                    " SELECT :prefix || g, ARRAY['supplier'] FROM generate_series(1, :n) g"
                ),
                {"prefix": prefix, "n": BULK_ROWS},
            )
            started = perf_counter()
            await session.execute(
                text("UPDATE counterparty SET name = name || '_u' WHERE name LIKE :like"),
                {"like": f"{prefix}%"},
            )
            elapsed = perf_counter() - started
            await session.rollback()
    assert elapsed < 30, f"bulk UPDATE of {BULK_ROWS} rows took {elapsed:.2f}s"


async def test_journal_rows_land_in_the_monthly_partition(
    world: World, worker_engine: AsyncEngine
) -> None:
    async with worker_engine.connect() as connection:
        partition = await connection.scalar(
            text(
                "SELECT tableoid::regclass::text FROM audit_log"
                " WHERE table_name = 'project' AND row_id = :id"
            ),
            {"id": world.p1},
        )
    assert partition == f"audit_log_{datetime.now(UTC):%Y_%m}"


async def test_partitions_are_created_ahead_idempotently_and_only_by_the_worker(
    world: World, worker_engine: AsyncEngine
) -> None:
    async with worker_engine.connect() as connection:
        created = await connection.scalar(text("SELECT app.ensure_audit_partitions(40)"))
        again = await connection.scalar(text("SELECT app.ensure_audit_partitions(40)"))
        await connection.rollback()
    # created зависит от числа партиций, уже созданных в базе (другими тестами и задачами):
    # проверяем неотрицательность и идемпотентность, а не число.
    assert created >= 0
    assert again == 0

    async with get_engine().connect() as connection:
        with pytest.raises(ProgrammingError, match="permission denied"):
            await connection.execute(text("SELECT app.ensure_audit_partitions(1)"))


async def test_a_row_outside_every_partition_goes_to_the_default_partition(
    owner_engine: AsyncEngine,
) -> None:
    async with owner_engine.connect() as connection:
        await connection.execute(
            text(
                "INSERT INTO audit_log (occurred_at, kind, action, actor_kind)"
                " VALUES ('2001-01-01 00:00:00+00', 'event', 'ancient', 'system')"
            )
        )
        await connection.rollback()
