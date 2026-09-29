from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from app.core.base import AuditUserMixin, SoftDeleteMixin
from app.core.context import current_user_id


def stamp_actor(session: Session, flush_context: Any, instances: Any) -> None:
    actor = current_user_id.get()
    for obj in session.new:
        if isinstance(obj, AuditUserMixin):
            obj.created_by = obj.created_by or actor
            obj.updated_by = obj.updated_by or actor
    for obj in session.dirty:
        if isinstance(obj, AuditUserMixin) and session.is_modified(obj):
            obj.updated_by = actor


def hide_soft_deleted(state: ORMExecuteState) -> None:
    if (
        state.is_select
        and not state.is_column_load
        and not state.is_relationship_load
        and not state.execution_options.get("include_deleted", False)
    ):
        state.statement = state.statement.options(
            with_loader_criteria(
                SoftDeleteMixin, lambda cls: cls.deleted_at.is_(None), include_aliases=True
            )
        )


def register_orm_events() -> None:
    for identifier, listener in (
        ("before_flush", stamp_actor),
        ("do_orm_execute", hide_soft_deleted),
    ):
        if not event.contains(Session, identifier, listener):
            event.listen(Session, identifier, listener)
