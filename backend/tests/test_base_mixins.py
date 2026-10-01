import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import system_context, use_context
from tests.models import Widget
from tests.support import internal


@pytest.fixture(autouse=True)
def _system_context() -> Iterator[None]:
    # Любая транзакция Session требует контекст запроса (после_begin-хук RLS).
    with system_context():
        yield


async def test_defaults_are_filled_on_insert(session: AsyncSession) -> None:
    widget = Widget(name="a")
    session.add(widget)
    await session.flush()

    assert isinstance(widget.id, uuid.UUID)
    assert widget.created_at.tzinfo is not None
    assert widget.updated_at.tzinfo is not None
    assert widget.created_by is None
    assert widget.updated_by is None
    assert widget.deleted_at is None


async def test_updated_at_is_bumped_by_an_update(session: AsyncSession) -> None:
    widget = Widget(name="a")
    session.add(widget)
    await session.flush()
    widget.updated_at = datetime(2000, 1, 1, tzinfo=UTC)
    await session.flush()

    widget.name = "b"
    await session.flush()
    await session.refresh(widget)

    assert widget.updated_at.year > 2000


async def test_actor_is_stamped_on_insert_and_update(session: AsyncSession) -> None:
    author, editor = uuid.uuid4(), uuid.uuid4()
    widget = Widget(name="a")
    with use_context(internal(author)):
        session.add(widget)
        await session.flush()
    assert (widget.created_by, widget.updated_by) == (author, author)

    with use_context(internal(editor)):
        widget.name = "b"
        await session.flush()
    assert (widget.created_by, widget.updated_by) == (author, editor)


async def test_system_change_without_a_user_leaves_the_actor_null(session: AsyncSession) -> None:
    author = uuid.uuid4()
    widget = Widget(name="a")
    with use_context(internal(author)):
        session.add(widget)
        await session.flush()

    widget.name = "b"
    await session.flush()
    system_row = Widget(name="system")
    session.add(system_row)
    await session.flush()

    assert (widget.created_by, widget.updated_by) == (author, None)
    assert (system_row.created_by, system_row.updated_by) == (None, None)


async def test_soft_deleted_rows_are_hidden_unless_requested(session: AsyncSession) -> None:
    keep, drop = Widget(name="keep"), Widget(name="drop")
    session.add_all([keep, drop])
    await session.flush()
    drop.deleted_at = datetime.now(UTC)
    await session.flush()

    visible = (await session.scalars(select(Widget))).all()
    everything = (
        await session.scalars(select(Widget).execution_options(include_deleted=True))
    ).all()

    assert [w.name for w in visible] == ["keep"]
    assert {w.name for w in everything} == {"keep", "drop"}
