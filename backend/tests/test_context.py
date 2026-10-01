from uuid import uuid4

import pytest
from sqlalchemy import text

from app.core.context import (
    MissingContextError,
    RequestContext,
    current_user_id,
    get_context,
    require_context,
    system_context,
    use_context,
)
from app.core.db import get_engine, get_sessionmaker
from app.core.rls import context_params
from tests.support import internal

_GUCS = (
    "app.user_id, app.is_internal, app.project_ids, app.legal_entity_ids,"
    " app.counterparty_id, app.request_id"
)


def test_context_is_absent_by_default_and_restored_after_use() -> None:
    assert get_context() is None
    assert current_user_id() is None
    with pytest.raises(MissingContextError):
        require_context()

    user = uuid4()
    with use_context(internal(user)) as context:
        assert require_context() is context
        assert current_user_id() == user
        with system_context():
            assert current_user_id() is None
        assert current_user_id() == user
    assert get_context() is None


def test_system_context_has_no_actor_and_no_scope() -> None:
    request_id = uuid4()
    with system_context(request_id) as context:
        assert context.user_id is None
        assert context.is_internal is True
        assert not context.project_ids
        assert not context.legal_entity_ids
        assert context.request_id == request_id


def test_context_params_are_deterministic_and_empty_means_empty_string() -> None:
    first, second = sorted((uuid4(), uuid4()))
    params = context_params(
        RequestContext(user_id=None, is_internal=False, project_ids=frozenset({second, first}))
    )
    assert params == {
        "user_id": "",
        "is_internal": "false",
        "project_ids": f"{first},{second}",
        "legal_entity_ids": "",
        "counterparty_id": "",
        "request_id": "",
        "sees_all_users": "false",
    }


async def test_session_without_a_context_fails_loudly(test_database: None) -> None:
    async with get_sessionmaker()() as session:
        with pytest.raises(MissingContextError):
            await session.execute(text("SELECT 1"))


async def test_the_hook_sets_every_guc_for_the_transaction(test_database: None) -> None:
    user, project, entity, counterparty, request_id = (uuid4() for _ in range(5))
    context = RequestContext(
        user_id=user,
        is_internal=False,
        project_ids=frozenset({project}),
        legal_entity_ids=frozenset({entity}),
        counterparty_id=counterparty,
        request_id=request_id,
    )
    query = text(f"SELECT {', '.join(f'current_setting(:k{i})' for i in range(6))}")
    keys = {f"k{i}": name.strip() for i, name in enumerate(_GUCS.split(","))}

    with use_context(context):
        async with get_sessionmaker()() as session:
            row = (await session.execute(query, keys)).one()

    assert tuple(row) == (
        str(user),
        "false",
        str(project),
        str(entity),
        str(counterparty),
        str(request_id),
    )


async def test_gucs_do_not_survive_the_transaction(test_database: None) -> None:
    with use_context(internal(uuid4(), projects=[uuid4()])):
        async with get_sessionmaker()() as session:
            await session.execute(text("SELECT 1"))

    # Новое соединение без хука: контекст пуст, функции app.*() fail-closed.
    async with get_engine().connect() as connection:
        row = (
            await connection.execute(
                text("SELECT app.user_id(), app.is_internal(), app.project_ids()")
            )
        ).one()
    assert tuple(row) == (None, False, [])
