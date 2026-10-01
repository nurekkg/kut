from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID


class MissingContextError(RuntimeError):
    """Запрос к БД без контекста запроса: без него RLS и аудит не знают, кто действует."""


@dataclass(frozen=True, slots=True)
class RequestContext:
    user_id: UUID | None
    is_internal: bool
    project_ids: frozenset[UUID] = frozenset()
    legal_entity_ids: frozenset[UUID] = frozenset()
    counterparty_id: UUID | None = None
    request_id: UUID | None = None


_current: ContextVar[RequestContext | None] = ContextVar("request_context", default=None)


def get_context() -> RequestContext | None:
    return _current.get()


def require_context() -> RequestContext:
    context = _current.get()
    if context is None:
        raise MissingContextError(
            "no request context: wrap the work in use_context(...) or system_context()"
        )
    return context


def current_user_id() -> UUID | None:
    context = _current.get()
    return context.user_id if context else None


@contextmanager
def use_context(context: RequestContext) -> Iterator[RequestContext]:
    token = _current.set(context)
    try:
        yield context
    finally:
        _current.reset(token)


def system_context(request_id: UUID | None = None) -> AbstractContextManager[RequestContext]:
    """Контекст системной задачи: актора нет (created_by/updated_by = NULL), объектов нет."""
    return use_context(RequestContext(user_id=None, is_internal=True, request_id=request_id))
