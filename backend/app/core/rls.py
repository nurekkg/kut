from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext, require_context

# is_local = true: значения живут до COMMIT/ROLLBACK, поэтому не утекают между запросами
# на одном соединении пула (в том числе за PgBouncer в transaction-режиме).
_SET_CONTEXT = text(
    "SELECT set_config('app.user_id', :user_id, true),"
    " set_config('app.is_internal', :is_internal, true),"
    " set_config('app.project_ids', :project_ids, true),"
    " set_config('app.legal_entity_ids', :legal_entity_ids, true),"
    " set_config('app.counterparty_id', :counterparty_id, true),"
    " set_config('app.request_id', :request_id, true),"
    " set_config('app.sees_all_users', :sees_all_users, true)"
)


def _join(ids: frozenset[object]) -> str:
    return ",".join(sorted(str(item) for item in ids))


def context_params(context: RequestContext) -> dict[str, str]:
    return {
        "user_id": str(context.user_id or ""),
        "is_internal": "true" if context.is_internal else "false",
        "project_ids": _join(context.project_ids),
        "legal_entity_ids": _join(context.legal_entity_ids),
        "counterparty_id": str(context.counterparty_id or ""),
        "request_id": str(context.request_id or ""),
        "sees_all_users": "true" if context.sees_all_users else "false",
    }


def set_connection_context(connection: Connection) -> None:
    connection.execute(_SET_CONTEXT, context_params(require_context()))


async def apply_context(session: AsyncSession) -> None:
    """Переставить GUC в текущей транзакции после смены контекста.

    Нужно, когда сервис расширил контекст посреди транзакции, например добавил id только что
    созданного объекта: INSERT ... RETURNING требует, чтобы новая строка проходила SELECT-политику.
    """
    await session.execute(_SET_CONTEXT, context_params(require_context()))
