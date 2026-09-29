import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import current_user_id
from tests.models import Widget


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
    token = current_user_id.set(author)
    try:
        widget = Widget(name="a")
        session.add(widget)
        await session.flush()
        assert (widget.created_by, widget.updated_by) == (author, author)

        current_user_id.set(editor)
        widget.name = "b"
        await session.flush()
        assert (widget.created_by, widget.updated_by) == (author, editor)
    finally:
        current_user_id.reset(token)


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
