"""Схема документов: неизменяемость версий, журнал только на добавление, переходы статусов
и их отказы, счётчик номеров недоступен приложению, литералы CHECK совпадают с кодом
(ADR-015 часть 1 раздел 3, часть 2 раздел 5.2, DoD S5a и F4)."""

import re
from collections.abc import AsyncIterator
from uuid import UUID

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from app.core.context import system_context
from app.core.db import get_sessionmaker
from app.modules.documents.types import FINANCIAL_KINDS, KINDS, OBJECT_KINDS
from tests.auth_support import AuthWorld
from tests.documents_support import (  # noqa: F401
    PDF,
    PDF_OK,
    approve,
    give_access,
    leave_no_access_behind,
    legal_entity,
    project,
    upload,
)
from tests.equipment_support import headers_for, in_context, rows
from tests.test_documents_api import _draft, _setup


@pytest.fixture
async def s3() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=10) as client:
        yield client


def _literals(definition: str) -> set[str]:
    return set(re.findall(r"'([a-z_0-9]+)'", definition))


async def test_the_check_constraints_repeat_the_kinds_module(worker_sync_engine: Engine) -> None:
    with worker_sync_engine.connect() as connection:
        definitions = dict(
            connection.execute(
                text(
                    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
                    " WHERE conname IN ("
                    "'ck_document_kind_valid', 'ck_document_financial_matches_kind',"
                    " 'ck_document_scope_valid', 'ck_document_number_counter_kind_valid')"
                )
            ).all()
        )
    assert _literals(definitions["ck_document_kind_valid"]) == set(KINDS)
    assert _literals(definitions["ck_document_financial_matches_kind"]) == set(FINANCIAL_KINDS)
    object_part = definitions["ck_document_scope_valid"].split("WHEN")[1].split("THEN")[0]
    assert _literals(object_part) == set(OBJECT_KINDS)
    assert _literals(definitions["ck_document_number_counter_kind_valid"]) == set(KINDS)


async def test_a_version_content_cannot_be_changed_by_anyone(
    worker_sync_engine: Engine, auth_world: AuthWorld, api: AsyncClient, s3: httpx.AsyncClient
) -> None:
    legal, _code, identifier, heads = await _setup(
        api, auth_world, worker_sync_engine, ("estimator",)
    )
    document_id = await _draft(api, heads["estimator"], legal, identifier)
    uploaded = await upload(api, s3, document_id, heads["estimator"], PDF_OK, PDF)
    version_id = uploaded.json()["id"]
    for statement in (
        "UPDATE document_version SET size_bytes = 1 WHERE id = CAST(:v AS uuid)",
        "UPDATE document_version SET s3_key = 'x' WHERE id = CAST(:v AS uuid)",
        "UPDATE document_version SET version_no = 9 WHERE id = CAST(:v AS uuid)",
    ):
        with pytest.raises(DBAPIError), worker_sync_engine.begin() as connection:
            connection.execute(text(statement), {"v": version_id})


async def test_the_application_cannot_change_a_version_or_its_status_directly(
    worker_sync_engine: Engine, auth_world: AuthWorld, api: AsyncClient, s3: httpx.AsyncClient
) -> None:
    legal, _code, identifier, heads = await _setup(
        api, auth_world, worker_sync_engine, ("estimator",)
    )
    document_id = await _draft(api, heads["estimator"], legal, identifier)
    version_id = (await upload(api, s3, document_id, heads["estimator"], PDF_OK, PDF)).json()["id"]
    context = in_context(auth_world.users["estimator"], (identifier,), (legal,))
    with pytest.raises(DBAPIError, match="permission denied"):
        await rows(
            "UPDATE document_version SET status = 'approved' WHERE id = CAST(:v AS uuid)"
            " RETURNING id",
            context,
            {"v": version_id},
        )


async def test_the_status_log_is_append_only_for_everyone(
    worker_sync_engine: Engine, auth_world: AuthWorld, api: AsyncClient, s3: httpx.AsyncClient
) -> None:
    legal, _code, identifier, heads = await _setup(
        api, auth_world, worker_sync_engine, ("estimator",)
    )
    document_id = await _draft(api, heads["estimator"], legal, identifier)
    await upload(api, s3, document_id, heads["estimator"], PDF_OK, PDF)
    with worker_sync_engine.connect() as connection:
        event_id = connection.scalar(
            text(
                "SELECT e.id FROM document_status_event e JOIN document_version v"
                " ON v.id = e.document_version_id WHERE v.document_id = CAST(:d AS uuid) LIMIT 1"
            ),
            {"d": document_id},
        )
    assert event_id is not None, "при создании версии пишется событие draft"
    for statement in (
        "UPDATE document_status_event SET comment = 'x' WHERE id = CAST(:e AS uuid)",
        "DELETE FROM document_status_event WHERE id = CAST(:e AS uuid)",
    ):
        with pytest.raises(DBAPIError), worker_sync_engine.begin() as connection:
            connection.execute(text(statement), {"e": event_id})
    with worker_sync_engine.connect() as connection:
        guard = connection.scalar(
            text(
                "SELECT count(*) FROM pg_trigger WHERE tgname = 'document_status_event_forbid'"
                " AND tgrelid = 'document_status_event'::regclass"
            )
        )
    assert guard == 1, "второй рубеж: триггер запрета, если права когда-нибудь выдадут"


async def test_the_number_counter_is_invisible_to_the_application(
    worker_sync_engine: Engine, auth_world: AuthWorld
) -> None:
    """Счётчик меняет только функция (от владельца kut_auth), приложение его не видит."""
    legal, _code = legal_entity(worker_sync_engine)
    with system_context():
        async with get_sessionmaker()() as session:
            await session.execute(
                text("SELECT app.next_document_number(CAST(:le AS uuid), 'ks2', 2026)"),
                {"le": legal},
            )
            await session.commit()
    context = in_context(
        auth_world.users["owner"],
        tuple(auth_world.projects.values()),
        tuple(auth_world.legal_entities.values()),
    )
    with pytest.raises(DBAPIError, match="permission denied"):
        await rows("SELECT count(*) FROM document_number_counter", context)


async def test_the_application_cannot_move_a_version_through_the_transition_function(
    api: AsyncClient, worker_sync_engine: Engine, auth_world: AuthWorld, s3: httpx.AsyncClient
) -> None:
    """Переход статуса - путь воркера: приложению EXECUTE не выдан, даже в системной сессии."""
    legal, _code, identifier, heads = await _setup(
        api, auth_world, worker_sync_engine, ("estimator",)
    )
    document_id = await _draft(api, heads["estimator"], legal, identifier)
    version_id = (await upload(api, s3, document_id, heads["estimator"], PDF_OK, PDF)).json()["id"]
    context = in_context(auth_world.users["estimator"], (identifier,), (legal,))
    with pytest.raises(DBAPIError, match="permission denied"):
        await rows(
            "SELECT app.document_version_transition(CAST(:v AS uuid), 'on_approval', NULL)",
            context,
            {"v": version_id},
        )


async def test_the_worker_path_follows_the_route_of_the_statuses(
    api: AsyncClient, worker_sync_engine: Engine, auth_world: AuthWorld, s3: httpx.AsyncClient
) -> None:
    legal, _code, identifier, heads = await _setup(
        api, auth_world, worker_sync_engine, ("estimator",)
    )
    document_id = await _draft(api, heads["estimator"], legal, identifier)
    version_id = (await upload(api, s3, document_id, heads["estimator"], PDF_OK, PDF)).json()["id"]
    with (
        pytest.raises(DBAPIError, match=r"document\.transition_invalid"),
        worker_sync_engine.begin() as connection,
    ):
        connection.execute(
            text("SELECT app.document_version_transition(CAST(:v AS uuid), 'approved', NULL)"),
            {"v": version_id},
        )
    with worker_sync_engine.begin() as connection:
        connection.execute(
            text("SELECT app.document_version_transition(CAST(:v AS uuid), 'on_approval', NULL)"),
            {"v": version_id},
        )
    with (
        pytest.raises(DBAPIError, match=r"document\.comment_required"),
        worker_sync_engine.begin() as connection,
    ):
        connection.execute(
            text("SELECT app.document_version_transition(CAST(:v AS uuid), 'draft', ' ')"),
            {"v": version_id},
        )


async def test_a_new_approval_makes_the_version_current_and_archives_the_previous(
    api: AsyncClient, worker_sync_engine: Engine, auth_world: AuthWorld, s3: httpx.AsyncClient
) -> None:
    legal, _code, identifier, heads = await _setup(
        api, auth_world, worker_sync_engine, ("estimator",)
    )
    document_id = await _draft(api, heads["estimator"], legal, identifier)
    first = (await upload(api, s3, document_id, heads["estimator"], PDF_OK, PDF)).json()["id"]
    second = (await upload(api, s3, document_id, heads["estimator"], PDF_OK, PDF)).json()["id"]
    approve(worker_sync_engine, first)
    approve(worker_sync_engine, second)
    with worker_sync_engine.connect() as connection:
        statuses = dict(
            connection.execute(
                text(
                    "SELECT id, status FROM document_version WHERE document_id = CAST(:d AS uuid)"
                ),
                {"d": document_id},
            ).all()
        )
        current = connection.scalar(
            text("SELECT current_version_id FROM document WHERE id = CAST(:d AS uuid)"),
            {"d": document_id},
        )
        log = connection.scalar(
            text(
                "SELECT count(*) FROM document_status_event WHERE document_version_id"
                " = CAST(:v AS uuid) AND to_status = 'archived'"
            ),
            {"v": first},
        )
    assert statuses[UUID(first)] == "archived"
    assert statuses[UUID(second)] == "approved"
    assert current == UUID(second)
    assert log == 1


async def test_a_legal_entity_code_is_unique_among_live_rows(worker_sync_engine: Engine) -> None:
    _legal, code = legal_entity(worker_sync_engine)
    with (
        pytest.raises(DBAPIError, match=r"uq_legal_entity_code"),
        worker_sync_engine.begin() as connection,
    ):
        connection.execute(
            text(
                "INSERT INTO legal_entity (id, name, code) VALUES (gen_random_uuid(), 'Копия', :c)"
            ),
            {"c": code},
        )


async def test_nobody_deletes_a_document_or_a_version(
    worker_sync_engine: Engine, auth_world: AuthWorld, api: AsyncClient, s3: httpx.AsyncClient
) -> None:
    """Документы и версии не удаляются (ТЗ §5.4): ни приложение, ни воркер не получают DELETE."""
    legal, _code = legal_entity(worker_sync_engine)
    identifier = project(worker_sync_engine, legal)
    give_access(worker_sync_engine, [auth_world.users["estimator"]], (identifier,), legal)
    document_id = await _draft(api, await headers_for(api, "estimator"), legal, identifier)
    context = in_context(auth_world.users["estimator"], (identifier,), (legal,))
    with pytest.raises(DBAPIError, match="permission denied"):
        await rows(
            "DELETE FROM document WHERE id = CAST(:d AS uuid) RETURNING id",
            context,
            {"d": document_id},
        )
    with (
        pytest.raises(DBAPIError, match="permission denied"),
        worker_sync_engine.begin() as connection,
    ):
        connection.execute(
            text("DELETE FROM document_version WHERE document_id = CAST(:d AS uuid)"),
            {"d": document_id},
        )
